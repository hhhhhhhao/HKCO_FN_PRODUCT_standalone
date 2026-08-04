"""
港股主营业务收入产品分布表 — HKCO_FN_PRODUCT
"""
import json
import os
import platform
import re
import time
import traceback
import unicodedata

from custom.extend.pdfplumber_extend_object import ExtendPlumber
from custom.utils.public_custom_util import (
    call_derived_data_interface,
    call_task_center_multi_taskid,
    call_task_center_single_taskid,
    delete_sql_ein1,
    get_basic_info,
    get_basic_info_by_task,
    insert_newsadmin_and_check,
    insert_pdfjx_and_return_detail,
    select_sql_ein1,
)
from custom.utils.upload_derived_data_util import upload_derived_data
from loguru import logger
from shared.conf.service_conf import config
from shared.enums.error_code_enum import ErrorCodeType
from custom.service.HKCO_FN_PRODUCT_document import (
    get_document_period_text,
    split_into_sections,
)
from custom.service.HKCO_FN_PRODUCT_selector import select_main_table
from custom.service.HKCO_FN_PRODUCT_extraction import extract_main_table
from custom.service.HKCO_FN_PRODUCT_metric_enrichment import enrich_metrics
from custom.service.EAPS_HKCO_FN_PRODUCT_format_data import format_records

# region mineru ocr
def parse_mineru_result_to_lines(pages_data,page_num):
    lines = []
    for line in pages_data:
        # source_type 只保留给 debug；章节切割只使用 document 中的正则。
        line['source_type'] = line.get('type', '')
        if line.get('type') in ['page_number', 'aside_text','image']:
            continue
        if line.get('type') in ['footer'] and not re.search(r'后附.*部分',line.get('content')):
            continue
        if not line.get('content'):
            continue
        if line.get('type') == 'table':
            table = format_mineru_table(fullwidth_to_halfwidth(line.get('content')))
            line['table'] = table
            line['is_table'] = True
        bbox = line.get('bbox')
        if line.get('x0') is None and isinstance(bbox, (list, tuple)) and bbox:
            line['x0'] = bbox[0]
        line['page_number'] = page_num
        line['text'] = fullwidth_to_halfwidth(line['content'] )

        lines.append(line)
    return lines


def format_mineru_table(html_content):
    """将 HTML 表格字符串转为二维数组"""
    import re

    # 提取所有行
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html_content, re.DOTALL)
    result = []

    for row_html in rows:
        # 提取所有单元格（包含完整标签以获取属性）
        cell_matches = re.findall(r'<(td|th)([^>]*)>(.*?)</\1>', row_html, re.DOTALL)
        row = []
        for tag, attrs, content in cell_matches:
            # 解析 colspan
            colspan_match = re.search(r'colspan=["\']?(\d+)', attrs)
            colspan = int(colspan_match.group(1)) if colspan_match else 1

            # 提取纯文本
            text = re.sub(r'<[^>]+>', '', content).strip()
            # 展开 colspan
            row.extend([text] * colspan)
        result.append(row)

    return result

def call_derive(call_derived_id, info_code, request_id, data, if_log=True):
    derived_result = {}
    derive = call_derived_data_interface(
        call_derived_id, info_code, request_id, data, if_log=if_log)
    if derive is None or not derive["Status"]:
        derived_result["code"] = ErrorCodeType.ERROR_CALL_DERIVED_DATA.value
        derived_result["data"] = None
    else:
        derived_data = derive.get("Result")
        if derived_data is None:
            derived_result["code"] = ErrorCodeType.EMPTY_CALL_DERIVED_DATA.value
            derived_result["data"] = None
        else:
            derived_result["code"] = ErrorCodeType.SUCCESS.value
            derived_result["data"] = derived_data
    return derived_result, derive

# region get_lines
def get_lines(pdf_path,json_path_page_map):
    def _clean_page_lines(page_lines, page_1based):
        for line in page_lines:
            line['page_number'] = page_1based
            if 'type' in line:
                del line['type']
            if 'bbox' in line:
                del line['bbox']
            if 'angle' in line:
                del line['angle']
            if 'content' in line:
                del line['content']
        return page_lines

    # 本地回测：有 MinerU JSON 且 PDF 不存在时，直接按 JSON 页序建 lines
    if json_path_page_map and (not pdf_path or not os.path.isfile(pdf_path)):
        lines = []
        for page_1based in sorted(json_path_page_map.keys()):
            with open(json_path_page_map[page_1based], 'r', encoding='utf-8-sig') as fp:
                original_page_lines = json.loads(fp.read())
                page_lines = parse_mineru_result_to_lines(original_page_lines, page_1based)
            lines = lines + _clean_page_lines(page_lines, page_1based)
        return lines

    with ExtendPlumber.open(pdf_path) as pdf:
        lines = []

        for page_number, pdf_page in enumerate(pdf.pages):
            page_lines = []
            if page_number + 1 in json_path_page_map:
                with open(json_path_page_map[page_number + 1], 'r', encoding='utf-8-sig') as fp:
                    original_page_lines = json.loads(fp.read())
                    page_lines = parse_mineru_result_to_lines(original_page_lines,page_number+ 1)

            # 加上页码
            lines = lines + _clean_page_lines(page_lines, page_number + 1)

    return lines

def fullwidth_to_halfwidth(s):
    s = s.replace('戶', '户')
    # 全角转半角
    return "".join((unicodedata.normalize("NFKC", char) if unicodedata.east_asian_width(char) in ["F", "W"] else char) for char in s)
# endregion

# region process debug dump
# 回测时 configs["debug_dir"] = batch_runs/HKCO_FN_PRODUCT/<stamp>/debug
# 每篇写 {infocode}_debug.txt，便于 AI/人工逐阶段定位问题。
_PROCESS_DEBUG = {"path": None, "lines": [], "enabled": False}


def _dbg_reset(info_code, configs=None):
    """仅 configs.debug_enabled=True 时落盘；回测默认关闭，避免并发写文件。"""
    _PROCESS_DEBUG["lines"] = []
    enabled = bool(isinstance(configs, dict) and configs.get("debug_enabled"))
    _PROCESS_DEBUG["enabled"] = enabled
    if not enabled:
        _PROCESS_DEBUG["path"] = None
        return
    debug_dir = None
    if isinstance(configs, dict):
        debug_dir = configs.get("debug_dir") or ""
    if not debug_dir:
        debug_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "PDF_BASELINE_BACKTEST",
            "batch_runs",
            "HKCO_FN_PRODUCT",
            "_adhoc",
            "debug",
        )
    os.makedirs(debug_dir, exist_ok=True)
    _PROCESS_DEBUG["path"] = os.path.join(debug_dir, f"{info_code}_debug.txt")
    _dbg(f"infocode={info_code}")
    _dbg(f"debug_file={_PROCESS_DEBUG['path']}")


def _dbg(msg=""):
    if not _PROCESS_DEBUG.get("enabled"):
        return
    _PROCESS_DEBUG["lines"].append(str(msg))


def _dbg_section(title):
    if not _PROCESS_DEBUG.get("enabled"):
        return
    _dbg("")
    _dbg("=" * 72)
    _dbg(title)
    _dbg("=" * 72)




def _dbg_flush():
    if not _PROCESS_DEBUG.get("enabled"):
        return
    path = _PROCESS_DEBUG.get("path")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(_PROCESS_DEBUG.get("lines") or []))
            fp.write("\n")
    except Exception as ex:
        logger.warning("process debug 落盘失败: %s", ex)


# endregion

def _is_backtest(request_id, task_info_list=None) -> bool:
    """run_backtest 传 request_id='backtest' 且 task_info_list=None。"""
    if str(request_id or "").strip().lower() == "backtest":
        return True
    return not task_info_list


_BACKTEST_RECORD_FIELDS = (
    "STARTDATE",
    "REPORTDATE",
    "CURRENCY",
    "PRODUCTNAME",
    "MBREVENUE",
    "MBCOST",
    "GROSS_PROFIT",
    "UNIT",
)


def _result_data_to_records(result_data):
    """DataTableParam 行 → run_backtest schema.fields 行。"""
    records = []
    for row in result_data or []:
        if not isinstance(row, dict):
            continue
        records.append({f: row.get(f) for f in _BACKTEST_RECORD_FIELDS})
    return records


def _build_extract_result(
    info_code,
    request_id,
    result_data,
    target_tables=None,
    reason_arr=None,
    err="",
    debug_meta=None,
):
    """组装 run_backtest 认的 extract_init 返回结构。

    selected_count：定表实际来源表数（单表=1；多表合并>1），供回测金额子集豁免。
    """
    records = _result_data_to_records(result_data)
    meta = dict(debug_meta or {})
    source_pages = list(meta.get("source_pages") or [])
    if not source_pages:
        for t in target_tables or []:
            pn = t.get("page") if isinstance(t, dict) else None
            if pn is not None and pn not in source_pages:
                source_pages.append(pn)
    if "selected_count" in meta:
        selected_count = int(meta.get("selected_count") or 0)
    else:
        selected_count = len(target_tables or [])

    reasons = list(reason_arr or [])
    if err:
        stage = "exception"
        status = "failed"
        msg = err
    elif "无ocr" in reasons:
        stage = "locate_fail"
        status = "no_data"
        msg = "无ocr"
    elif not target_tables:
        stage = "locate_fail"
        status = "no_data"
        msg = "未定位到目标表"
    elif not records:
        stage = "format_fail" if not reasons else "empty_output"
        status = "no_data"
        msg = "/".join(dict.fromkeys(reasons)) if reasons else "直接空提"
    else:
        stage = "success"
        status = "success"
        msg = f"{len(records)} 条"

    return {
        "status": status,
        "infocode": info_code,
        "segment_id": str(request_id or ""),
        "data": {
            "records": records,
            "debug": {
                **meta,
                "stage": stage,
                "stage_label": stage,
                "message": msg,
                "source_pages": source_pages,
                "selected_count": selected_count,
            },
        },
        "error_message": msg if status != "success" else "",
    }


def get_last_period_data(info_code, request_id, task_id):
    # 诊断开关：验证当前公告主流程是否能在无历史数据时完整运行。
    # 默认关闭，不改变生产行为，也不读取 GT。
    if str(os.environ.get("HKCO_FN_PRODUCT_DISABLE_LAST_PERIOD", "")).lower() in ("1", "true", "yes"):
        return []
    path = os.path.join(os.path.dirname(__file__), "..", "..", "tasks", "HKCO_FN_PRODUCT", "last_data.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f).get(info_code)
            if data:
                _dbg(f"[last_period] loaded from last_data.json rows={len(data)}")
                return data
    return []


def _prior_context(last_period_data, document_period_text):
    prior_names = []
    required_metrics = []
    end_dates = []
    for item in last_period_data or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("PRODUCTNAME") or "").strip()
        if name and name not in {"合计", "合計"}:
            prior_names.append(name)
        for metric in ("MBCOST", "GROSS_PROFIT"):
            if str(item.get(metric) or "").strip() and metric not in required_metrics:
                required_metrics.append(metric)
        match = re.search(r"20\d{2}[/\-](\d{1,2})[/\-](\d{1,2})", str(item.get("REPORTDATE") or ""))
        if match:
            end_dates.append(tuple(map(int, match.groups())))
    fiscal_month_day = max(set(end_dates), key=end_dates.count) if end_dates else ()
    return {
        "prior_product_names": prior_names,
        "prior_fiscal_month_day": fiscal_month_day,
        "required_metrics": required_metrics,
        "document_period_text": document_period_text,
    }

# region process_pdf_file
def process_pdf_file(pdf_path, info_code, request_id, task_info_list, ocr_result_info, configs):
    if platform.system().lower() == "windows" and config.profile == "dev":
        if_callback = False
    else:
        if_callback = True

    table_cname = "港股主营业务收入产品分布表"
    backtest = _is_backtest(request_id, task_info_list)
    extract_result = None
    title_find_page = 0
    report_path = ""

    start_time = int(time.time() * 1000)
    if not backtest:
        logger.info("【%s】任务处理开始, request_id=%s, info_code=%s" % ("港股主营业务收入产品分布表", request_id, info_code))

    # 回测无 task_info：注入占位，只跑抽取，不衍生不入库
    if backtest and not task_info_list:
        task_info_list = [{
            "task_id": "backtest",
            "table_name": "HKCO_FN_PRODUCT",
            "table_cname": table_cname,
            "notice_date": "",
            "is_auto_publish": 0,
            "attach_path": "",
            "column_codes": [],
        }]

    try:
        for task_info in task_info_list:
            if table_cname == task_info.get("table_cname"):
                derived_id = "346440"
                task_id = task_info.get("task_id")
                table_name = task_info.get("table_name")
                notice_date = task_info.get("notice_date")
                column_codes = task_info.get("column_codes")
                is_publish = task_info.get("is_auto_publish") == 1
                notice_title = task_info.get("attach_path")

                sql = "DELETE FROM pdfjx.HKCO_FN_PRODUCT WHERE RELINFOCODE = '" + str(info_code) + "'"
                result, data = delete_sql_ein1(sql)
                sql = "DELETE FROM NEWSADMIN.HKCO_FN_PRODUCT WHERE RELINFOCODE = '" + str(info_code) + "'"
                result, data = delete_sql_ein1(sql)

                if not backtest:
                    base_info = get_basic_info_by_task(task_info_list)
                    if base_info == {}:
                        base_info = get_basic_info(info_code)

                title_find_page = 0
                report_path = ""
                reason_arr = []
                debug_meta = {"selected_count": 0, "source_pages": []}
                pdf_path, json_path_page_map = get_all_paths(pdf_path, configs)

                _dbg_reset(info_code, configs)
                try:
                    # 上期数据决定主表连续性和本期需要延续的指标字段；没有上期时使用固定表类优先级。
                    last_period_data = get_last_period_data(info_code, request_id, task_id)
                    _dbg(
                        f"[last_period] rows={len(last_period_data) if isinstance(last_period_data, list) else type(last_period_data)}"
                    )

                    # 获取文档流
                    lines = get_lines(pdf_path, json_path_page_map)
                    _dbg_section("get_lines")
                    _dbg(f"lines={len(lines or [])} pages_json={len(json_path_page_map or {})}")

                    # 1. 正则切章。
                    sections = split_into_sections(lines)
                    document_period_text = get_document_period_text(lines)
                    context = _prior_context(last_period_data, document_period_text)
                    _dbg_section("sections")
                    _dbg(f"sections={len(sections)} tables={sum(len(s['tables']) for s in sections)}")

                    # 2. 遍历章节，只选择一张主表。
                    main_table, selection_debug = select_main_table(sections, context["prior_product_names"])
                    _dbg_section("main_table_selection")
                    for item in selection_debug:
                        _dbg(json.dumps({
                            key: value for key, value in item.items() if key != "table"
                        } | {
                            "table_id": item["table"]["id"],
                            "section_title": item["table"]["section_title"],
                        }, ensure_ascii=False))

                    # 3. 分析主表结构并抽取产品、收入。
                    main_result = extract_main_table(main_table, context)
                    _dbg_section("main_table_extraction")
                    _dbg(json.dumps(main_result["debug"], ensure_ascii=False))

                    # 4. 必要时从其他物理表补成本、毛利。
                    metric_facts, metric_debug = enrich_metrics(
                        sections,
                        main_table,
                        main_result["facts"],
                        context["required_metrics"],
                    )
                    _dbg_section("metric_enrichment")
                    _dbg(json.dumps(metric_debug, ensure_ascii=False))

                    # 5. 格式化最终入库字段。
                    result_data = format_records(main_result["facts"], metric_facts)
                    if not result_data:
                        reason_arr.append("主表抽取为空")
                    target_items = [main_table] if main_table else []
                    source_pages = []
                    if main_table and main_table.get("page") is not None:
                        source_pages.append(main_table["page"])
                    for section in sections:
                        for table in section["tables"]:
                            if table["id"] in metric_debug.get("source_tables", []) and table.get("page") not in source_pages:
                                source_pages.append(table.get("page"))
                    debug_meta = {
                        "selected_count": len(target_items) + len(metric_debug.get("source_tables", [])),
                        "source_pages": source_pages,
                        "selected_table": main_table.get("id", "") if main_table else "",
                        "classification": main_result["classification"],
                        "selection_debug": [
                            {key: value for key, value in item.items() if key != "table"}
                            | {"table_id": item["table"]["id"]}
                            for item in selection_debug
                        ],
                        "extraction_debug": main_result["debug"],
                        "metric_debug": metric_debug,
                    }

                    _dbg(f"reason_arr={reason_arr} debug_meta={debug_meta}")
                finally:
                    _dbg_flush()

                extract_result = _build_extract_result(
                    info_code,
                    request_id,
                    result_data,
                    target_tables=target_items,
                    reason_arr=reason_arr,
                    debug_meta=debug_meta,
                )

                # 回测：只返回抽取结果，不衍生不入库
                if backtest:
                    return extract_result

                if '无ocr' in reason_arr:
                    call_task_center_single_taskid(request_id, info_code, task_info, '无ocr', ErrorCodeType.ERROR_SPECIAL_LOGIC.value, if_callback, report_path, page=title_find_page,)
                    continue

                if '直接空提' in reason_arr:
                    call_task_center_single_taskid(request_id, info_code, task_info, "空提交", ErrorCodeType.ERROR_MATCH_NONE_AUTO_COMMIT.value, if_callback, report_path, page=title_find_page)
                    continue

                if reason_arr:
                    reason_arr = list(set(reason_arr))
                    reason = '/'.join(reason_arr)
                    call_task_center_single_taskid(request_id, info_code, task_info, reason, ErrorCodeType.ERROR_SPECIAL_LOGIC.value, if_callback, report_path, page=title_find_page,)
                    continue

                with open("HKCO_FN_PRODUCT_before_derived.json", "w", encoding="utf-8") as json_file:
                    json.dump(result_data, json_file, ensure_ascii=False)

                data_request = {"InfoCode": info_code, "DataTableParam": result_data}
                upload_data = "衍生前的数据：\r\n" + json.dumps(data_request, ensure_ascii=False, indent=4)
                data_response, derive_data = call_derive(derived_id, info_code, request_id, data_request)
                upload_data = upload_data + "\r\n衍生后的数据：\r\n" + json.dumps(derive_data, ensure_ascii=False, indent=4)
                report_path = upload_derived_data(upload_data, info_code, table_name, call_derived_id=derived_id)
                if data_response["code"] != ErrorCodeType.SUCCESS.value:
                    logger.error("【%s】调用衍生失败, request_id=%s, info_code=%s, task_id=%s, derived_id=%s" % (table_cname, request_id, info_code, task_id, derived_id))
                    call_task_center_single_taskid(request_id, info_code, task_info, "调用衍生接口报错", data_response["code"], if_callback=if_callback, report_path=report_path, page=title_find_page)
                    continue
                else:
                    logger.info("【%s】调用衍生成功, request_id=%s, info_code=%s, task_id=%s, derived_id=%s" % (table_cname, request_id, info_code, task_id, derived_id))

                derived_data = data_response["data"]

                # with open("PUBLIC_FN_ISUPERVISENEW_after_derived.json", "w", encoding="utf-8") as json_file:
                #     json.dump(derived_data, json_file, ensure_ascii=False, indent=4)

                if not derived_data:
                    call_task_center_single_taskid(request_id, info_code, task_info, '文本不处理', ErrorCodeType.ERROR_SPECIAL_LOGIC.value, if_callback, report_path, page=title_find_page,)
                    continue

                # 入库解析
                pdfjx_success, msg = insert_pdfjx_and_return_detail(info_code, task_id, table_name, derived_data)
                if not pdfjx_success:
                    call_task_center_single_taskid(request_id, info_code, task_info, msg, ErrorCodeType.ERROR_INSERT_PDFJX.value, if_callback=if_callback, report_path=report_path, page=title_find_page,)
                    continue
                else:
                    logger.info("【%s】入解析库成功, request_id=%s, info_code=%s, task_id=%s" % (table_cname, request_id, info_code, task_id))

                if is_publish:
                    # 去掉校验点
                    checkout_sql = '''SELECT B.CONTENT,TO_CHAR(A.RULEID) RULEID,A.CHECKFIELD 
                                        FROM CKADMIN.CHK_RULE A
                                        JOIN CKADMIN.CHK_MONITORTABLE B
                                        ON A.RULEID = B.RULEID
                                        AND A.TABLENAME = B.MONITORTABLENAME
                                        JOIN PERMIT.CTI_TABLEINFO C
                                        ON A.TABLECODE = C.TABLECODE
                                        WHERE A.RULETYPE in (0,2)
                                        AND A.ISINSERTDATA = 0
                                        AND CONFIGTYPE = 1
                                        AND A.ERRORLEVEL in (${ERRORLEVEL})
                                        AND A.USESTATE = 0
                                        AND A.TYPE in (1,2)
                                        AND A.RULEID NOT IN (900023789)
                                        AND C.SOURDATA = 'EIN1'
                                        AND C.SOURCESCHEMA = 'NEWSADMIN'
                                        AND C.TABLENAME = ${ATNAME}'''

                    # 前端校验 + 入发布库
                    newsadmin_success, result, result_code = insert_newsadmin_and_check(info_code, task_id, table_name, derived_data,checkout_sql=checkout_sql)
                    logger.info("入库成功,derived_data=%s" % derived_data)
                    if newsadmin_success:
                        logger.info("【%s】自动发布成功, request_id=%s, info_code=%s, task_id=%s" % (table_cname, request_id, info_code, task_id))
                    else:
                        logger.error("【%s】自动发布失败, request_id=%s, info_code=%s, task_id=%s, msg=%s" % (table_cname, request_id, info_code, task_id, result))
                    call_task_center_single_taskid(request_id, info_code, task_info, result, result_code, if_callback=if_callback, report_path=report_path, page=title_find_page,)
                else:
                    logger.info("%s只解析不发布, info_code=%s, task_id=%s" % (table_cname + "其他", info_code, task_id))
                    call_task_center_single_taskid(request_id, info_code, task_info, "只解析不发布", ErrorCodeType.ERROR_ONLY_PARSING_NO_PUBLISH.value, if_callback, report_path, page=title_find_page)

    except Exception as ex:
        end_time = int(time.time() * 1000)
        logger.error("处理未知异常, request_id=%s, info_code=%s, cost_time=%sms, err_msg=%s" % (request_id, info_code, (end_time - start_time), traceback.format_exc()))
        if backtest:
            return _build_extract_result(info_code, request_id, [], err=str(ex))
        call_task_center_multi_taskid(request_id, info_code, task_info_list, f"未知异常:{ex}", ErrorCodeType.ERROR_UNKNOWN.value, if_callback=if_callback, report_path=report_path, page=title_find_page, )
    finally:
        end_time = int(time.time() * 1000)
        if not backtest:
            logger.info("任务处理结束, request_id=%s, info_code=%s, cost_time=%sms" % (request_id, info_code, (end_time - start_time)))

    return extract_result

def get_all_paths(src_file_path, configs):
    if '##' in src_file_path:
        src_file_path = src_file_path.split('##')[0]

    # 如果 src_file_path 是目录，进去找 PDF 文件
    if os.path.isdir(src_file_path):
        for item in os.listdir(src_file_path):
            if item.lower().endswith('.pdf'):
                pdf_path = os.path.join(src_file_path, item)
                break
        else:
            pdf_path = src_file_path
    else:
        pdf_path = src_file_path

    json_path_page_map = {}

    # 与 ISUPERVISENEW1 一致：mu 文件夹在 pdf 所在目录
    pdf_dir = os.path.dirname(pdf_path)
    if pdf_dir and os.path.isdir(pdf_dir):
        for item in os.listdir(pdf_dir):
            item_path = os.path.join(pdf_dir, item)
            if item.endswith('mu') and os.path.isdir(item_path):
                for f in os.listdir(item_path):
                    if not f.endswith('.json') or 'over' in f:
                        continue
                    m = re.compile(r"AN\d{18}_(.*?)(.json)").search(f)
                    if m:
                        json_path_page_map[int(m.group(1))] = os.path.join(item_path, f)

    # 显式目录输入：{mineru_json_base_dir}/{infocode}/。
    if not json_path_page_map and configs and configs.get("mineru_json_base_dir"):
        info_code = os.path.splitext(os.path.basename(pdf_path))[0]
        json_dir = os.path.join(str(configs["mineru_json_base_dir"]), info_code)
        if os.path.isdir(json_dir):
            for f in os.listdir(json_dir):
                if not f.endswith('.json') or 'over' in f:
                    continue
                m = re.compile(r"AN\d{18}_(.*?)(.json)").search(f)
                if m:
                    json_path_page_map[int(m.group(1))] = os.path.join(json_dir, f)

    json_path_page_map = dict(sorted(json_path_page_map.items(), key=lambda x: x[0]))
    logger.info("get_all_paths pdf=%s json_pages=%s" % (pdf_path, len(json_path_page_map)))
    return pdf_path, json_path_page_map

# endregion


# 任务中心接口
def extract_init(pdf_path, info_code, request_id, configs=None, task_info_list=None):
    """生产：带 task_info → 抽取后衍生/入库。

    回测：request_id='backtest' 或无 task_info → 衍生前落盘 + 返回 data.records，不入库。
    """
    ocr_result_info = None
    if configs is not None and configs.get("ocr_result_info") is not None:
        ocr_result_info = configs.get("ocr_result_info")

    result = process_pdf_file(pdf_path, info_code, request_id, task_info_list, ocr_result_info, configs)
    
    if isinstance(result, dict):
        return result
    # 生产路径未组装返回时，给跑批一个可识别的空壳
    return {
        "status": "failed",
        "infocode": info_code,
        "segment_id": str(request_id or ""),
        "data": {"records": [], "debug": {"stage": "empty_output", "message": "process_pdf_file 无返回"}},
        "error_message": "process_pdf_file 无返回",
    }

# 本地跑批方法
def process_pdf_file_batch(pdfs):
    for pdf in pdfs:
        info_code = os.path.basename(pdf)
        src_file_path = ""
        info_code_list = os.listdir(pdf)
        for tmp_path in info_code_list:
            if re.compile(r"(overview.json)|(multi_attach_completed)").search(tmp_path):
                pass
            else:
                src_file_path += "##" + os.path.join(pdf, tmp_path)
        src_file_path = re.sub(r"^##", "", src_file_path)

        hw_dir = os.path.join(pdf, "hw")
        hw_list = os.listdir(hw_dir)
        hw_list = [os.path.join(hw_dir, hw) for hw in hw_list]
        sql = f"select COLUMNCODE from newsadmin.ANN_RELCOLUMN WHERE INFOCODE = '{info_code}'"
        result, data = select_sql_ein1(sql)

        request_id = "TEST12345"
        task_info_list = [{'task_id': '10100764959', 'notice_title': '人寿','table_name': 'HKCO_FN_PRODUCT', 'table_cname': '港股主营业务收入产品分布表', 'business_id': '301988', 'is_auto_publish': 1, 'notice_date': '2025/04/30', 'end_date': '2025/12/31', 'column_codes': [data[0]['COLUMNCODE']], 'source_type': '551', 'source_ip': '10.149.216.121:8001', 'db_name': 'EIN1TEST', 'attach_path': '/ANNOUNCE/COMP/2025/04/30/上交所-债券/N/人寿联储证券股份有限公司公司债券年度报告（2024年）.pdf', 'callback_url': 'http://10.149.216.121:8001/etms/Edms/RefreshReportFinishedTestFlow', 'configids': '301988'}]
        for tmp_task_info in task_info_list:
            tmp_task_info["is_auto_publish"] = 1
        info_code = os.path.splitext(os.path.basename(pdf))[0]
        configs = {'notice_title': '英大证券有限责任公司2024年半年度财务报表', 'column_codes': [data[0]['COLUMNCODE']], 'ocr_result_info': None,
                   'hw_ocr_result_info': {'error_code': 0, 'error_msg': '', 'error_page_numbers': [], 'image_page_number_list': ['1', '2', '3']},
                   'hw_json_file_full_path_list':hw_list,
                   'hehe_ocr_result_info': None, 'hehe_local_file_full_path_list': None}
        process_pdf_file(pdf, info_code, request_id, task_info_list, None, configs)


if __name__ == "__main__":
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    code = "AN202603201820676603"
    pdf_path = os.path.join(root, "pdf", f"{code}.pdf")
    result = process_pdf_file(pdf_path, code, "debug", None, None, {
        "mineru_json_base_dir": os.path.join(root, "pdf_json"),
        "debug_enabled": True,
    })
    print(result)
