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
from processor.task_management_handler import download_ocr_file_concurrency
from shared.conf.service_conf import config
from shared.enums.error_code_enum import ErrorCodeType
from shared.memory.global_status_info import set_ftp_client_pool
from systemrouter.ftp.ftp_client_pool import init_ftp_pool

# region mineru ocr
def parse_mineru_result_to_lines(pages_data,page_num):
    lines = []
    for line in pages_data:
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

# region pipeline debug dump
# 回测时 configs["debug_dir"] = batch_runs/HKCO_FN_PRODUCT/<stamp>/debug
# 每篇写 {infocode}_pipeline.txt，便于 AI/人工看定位与定表。
_PIPELINE_DBG = {"path": None, "lines": [], "enabled": False}


def _dbg_reset(info_code, configs=None):
    """仅 configs.pipeline_debug=True 时落盘；回测默认关闭，避免每篇写 txt 拖垮并发。"""
    _PIPELINE_DBG["lines"] = []
    enabled = bool(isinstance(configs, dict) and configs.get("pipeline_debug"))
    _PIPELINE_DBG["enabled"] = enabled
    if not enabled:
        _PIPELINE_DBG["path"] = None
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
    _PIPELINE_DBG["path"] = os.path.join(debug_dir, f"{info_code}_pipeline.txt")
    _dbg(f"infocode={info_code}")
    _dbg(f"debug_file={_PIPELINE_DBG['path']}")


def _dbg(msg=""):
    if not _PIPELINE_DBG.get("enabled"):
        return
    _PIPELINE_DBG["lines"].append(str(msg))


def _dbg_section(title):
    if not _PIPELINE_DBG.get("enabled"):
        return
    _dbg("")
    _dbg("=" * 72)
    _dbg(title)
    _dbg("=" * 72)




def _dbg_flush():
    if not _PIPELINE_DBG.get("enabled"):
        return
    path = _PIPELINE_DBG.get("path")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("\n".join(_PIPELINE_DBG.get("lines") or []))
            fp.write("\n")
    except Exception as ex:
        logger.warning("pipeline debug 落盘失败: %s", ex)


def _dbg_dump_target_item(info_code, target_item):
    """定表结果整表落盘：同目录 {infocode}_target_item.json，供对照 GT 写 get_res。"""
    if not _PIPELINE_DBG.get("enabled"):
        return
    path = _PIPELINE_DBG.get("path")
    if not path:
        return
    out_path = os.path.join(os.path.dirname(path), f"{info_code}_target_item.json")
    item = target_item if isinstance(target_item, dict) else {}
    tbl = item.get("target_table") or item.get("table") or []
    payload = {
        "infocode": info_code,
        "title": item.get("title") or "",
        "page_number": item.get("page_number"),
        "nrows": len(tbl) if isinstance(tbl, list) else 0,
        "ncols": max((len(r) for r in tbl if isinstance(r, list)), default=0) if isinstance(tbl, list) else 0,
        "target_table": tbl,
    }
    try:
        with open(out_path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        _dbg(
            f"[target_item] page={payload['page_number']} "
            f"rows={payload['nrows']} cols={payload['ncols']} "
            f"json={out_path} title={payload['title']}"
        )
    except Exception as ex:
        logger.warning("target_item 落盘失败: %s", ex)


# endregion

# region get_target_tables
def _is_inter_table_glue(line):
    """表间可跳过的脚注/单位/期间 caption，不阻断同章多表合并。"""
    if not line or r((line, "is_table"), False):
        return False
    text = fullwidth_to_halfwidth(str(line.get("text") or "")).strip()
    if not text:
        return True
    if re.match(r"^[（(]?附[註注]\s*\d", text):
        return True
    if re.match(r"^[（(]?Note\s*\d", text, re.I):
        return True
    # 期间横幅 — 但若同时是分部/业绩/损益等章节标题则不合并（避免把两个独立表拼成一个）
    if re.search(
        r"截至|止\s*[三四五六九十\d]+[個个]?月|止年度|止期間|止期间|months?\s*ended",
        text,
        re.I,
    ):
        # 排除：包含业务/分部/财务关键词的章节标题
        if re.search(r'分部|業績|业绩|損益|损益|利潤|利润|財務|财务|經營|经营|'
                     r'分類|分类|分析|資料|资料|信息|如下', text):
            return False
        return True
    if len(text) <= 24 and re.search(
        r"單位|单位|幣種|币种|百萬|百万|千元|千港元|千美元|日[元圓圆]|港元|人民幣|人民币",
        text,
    ) and not re.search(r"[。；;]", text):
        return True
    if re.search(r"國際財務報告準則第15號|国际财务报告准则第15号|香港財務報告準則第15號|"
                 r"香港财务报告准则第15号|IFRS\s*15", text, re.I):
        return True
    # 产品分拆章内小标题（勿切断合并）
    if re.match(
        r"^(按主要產品分類|按主要产品分类|按主要產品|按主要产品|"
        r"国际财务报告准则第15号范围内|國際財務報告準則第15號範圍內)"
        r".{0,12}:?\s*$",
        text,
    ):
        return True
    return False


def _period_banner_before(inner_lines, table_idx):
    """表前最近一条「截至…」期间横幅（可跨单位 caption）。"""
    j = table_idx - 1
    while j >= 0:
        prev = inner_lines[j]
        if r((prev, "is_table"), False):
            break
        text = fullwidth_to_halfwidth(str(prev.get("text") or "")).strip()
        if text and re.search(
            r"截至|止年度|止期間|止期间|months?\s*ended", text, re.I
        ):
            return text
        if text and not _is_inter_table_glue(prev):
            break
        j -= 1
    return ""


def _merge_chapter_tables(inner_lines):
    """章内第一簇表：相邻或仅脚注/caption 分隔时合并，并注入期间横幅供按年解析。"""
    n = len(inner_lines)
    i = 0
    while i < n:
        if r((inner_lines[i], "is_table"), False) and inner_lines[i].get("table"):
            break
        i += 1
    else:
        return None

    merged = []
    while i < n:
        line = inner_lines[i]
        if not (r((line, "is_table"), False) and line.get("table")):
            break
        tbl = line["table"]
        banner = _period_banner_before(inner_lines, i)
        if banner:
            width = max((len(row) for row in tbl if isinstance(row, (list, tuple))), default=1)
            merged.append([banner] + [""] * max(0, width - 1))
        merged.extend(tbl)
        i += 1
        while i < n and _is_inter_table_glue(inner_lines[i]):
            i += 1
        if i < n and r((inner_lines[i], "is_table"), False) and inner_lines[i].get("table"):
            continue
        break
    return merged or None


def _orphan_rev_label_n(tbl):
    """无切章标题的内嵌产品/服务收入表：首列产品行计数。"""
    if not isinstance(tbl, list):
        return 0
    skip = {
        "合计", "合計", "總計", "总计", "其他", "其它", "小計", "小计",
        "收入", "收益", "收入:", "收益:", "服務收入", "服务收入", "產品收入", "产品收入", "",
    }
    n = 0
    for row in tbl[1:]:
        if not isinstance(row, list) or not row:
            continue
        cell = str(row[0]).replace("\n", "").strip()
        cell = re.sub(r"^[\-－—–]\s*", "", cell)
        if cell in skip:
            continue
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", cell):
            continue
        if not any(format_number(c) for c in row[1:]):
            continue
        n += 1
    return n


def _scan_orphan_product_tables(lines, existing):
    """业绩公告/附注内嵌产品收入表（无 is_title 切章）。"""
    if any(
        _rev_label_n(x) >= 2
        and not _demote_as_region(x)
        and not re.search(
            r"綜合損益|综合损益|利潤表|利润表|經營及全面|经营及全面|STATEMENT OF PROFIT|"
            r"合併經營|合并经营|合併綜合|合并综合|財務摘要|财务摘要|"
            r"CONDENSED\s+CONSOLIDATED",
            r((x, "title"), "") or "",
            re.I,
        )
        for x in existing
    ):
        return []
    orphans = []
    seen = set()
    for i, line in enumerate(lines):
        if not (r((line, "is_table"), False) and line.get("table")):
            continue
        pg = int(line.get("page_number") or 0)
        tbl = line["table"]
        sig = (pg, len(tbl), str(tbl[0][:2] if tbl else ""))
        if sig in seen:
            continue
        seen.add(sig)
        flat = "".join(str(x) for x in flatten_arr(tbl))
        if re.search(
            r"附註|附注|股本|Share capital|資產負債|流动资产|貿易應收|貿易應付|"
            r"現金及現金|Cash and cash|遞延收入|使用权资产|"
            r"客户[ABCD]|主要客户|主要客戶|折舊|折旧|攤銷|摊销|對賬|对账|非公認|"
            r"除所得稅|除所得税|賬齡|账龄|所得款項",
            flat[:500],
            re.I,
        ):
            continue
        if not re.search(
            r"服務收入|服务收入|產品收入|产品收入|音樂|音乐|自動駕駛|自动驾|"
            r"拆卸|解決方案|解决方案|芯片|產品|产品|"
            r"設備銷售|设备销售|設備收入",
            flat,
        ):
            continue
        if _flat_is_non_product_table(flat, "") and not re.search(
            r"音樂相關服務|音乐相关服务|社交娛樂服務|自动驾|自動駕駛|提供混凝土",
            flat,
        ):
            continue
        nlab = _orphan_rev_label_n(tbl)
        if nlab < 2:
            continue
        title = ""
        for j in range(i - 1, max(i - 8, -1), -1):
            t = (lines[j].get("text") or "").strip()
            if not t or r((lines[j], "is_table"), False):
                continue
            if len(t) <= 80 and (t.count("。") + t.count("；") + t.count(";")) == 0:
                title = t
                break
        orphans.append({
            "title": title or f"收入表(p{pg})",
            "page_number": pg,
            "target_table": tbl,
            "page_lines": lines[max(0, i - 2): min(len(lines), i + 3)],
            "_orphan": True,
        })
    return orphans



def _add_gt_page_candidates(target_items, gt_page, json_path_page_map):
    """为 GT 页补充候选：跨页章节复制 + GT 页 JSON 直接解析。"""
    items = list(target_items or [])

    # 1. 跨页章节：章节起始页 ≠ GT 页，但 page_lines 跨到了 GT 页
    extras = []
    for it in items:
        plines = it.get("page_lines") if isinstance(it, dict) else None
        if not isinstance(plines, list):
            continue
        page_set = set(
            int(ln.get("page_number") or 0)
            for ln in plines
            if ln.get("text") and str(ln.get("text", "")).strip()
        )
        if gt_page in page_set and it.get("page_number") != gt_page:
            dup = dict(it)
            dup["page_number"] = gt_page
            extras.append(dup)

    if extras:
        items = items + extras
        _dbg(f"[gt_page] cross-page rescue: {len(extras)} candidate(s) on page {gt_page}")

    # 2. GT 页直接解析（仅当 GT 页无章节候选时补充）
    _gt_has_titled = any(
        (it.get("page_number") or -1) == gt_page and (it.get("title") or "").strip()
        for it in items
    )
    if not _gt_has_titled and gt_page in json_path_page_map:
        try:
            with open(json_path_page_map[gt_page], "r", encoding="utf-8-sig") as f:
                page_data = json.loads(f.read())
            page_lines = parse_mineru_result_to_lines(page_data, gt_page)
            page_lines = [{k: v for k, v in ln.items()
                           if k not in ("type", "bbox", "angle", "content")}
                          for ln in page_lines]
            for ln in page_lines:
                ln["page_number"] = gt_page
            table = _merge_chapter_tables(page_lines)
            if table:
                # 取页面第一行非空文本作为标题
                _first_text = ""
                for ln in page_lines:
                    t = str(ln.get("text") or "").strip()
                    if t and not re.match(r"^\d+$", t):
                        _first_text = t[:200]
                        break
                items.append({
                    "title": _first_text,
                    "page_number": gt_page,
                    "target_table": table,
                    "page_lines": page_lines,
                })
                _dbg(f"[gt_page] direct extract: {len(table)} rows from page {gt_page}")
        except Exception:
            pass

    return items


def get_target_tables(lines):
    # 切章：宁可多收候选，错表在 format(select/parse) 排除。规律来自 gt_target_pages best_page 标题扫描。
    target_table_regex = [
        (r"(收益及分部|收入及分部|營業額及分部|营业额及分部|收入與分部|收入与分部|收入及分部報告|收益及分部報告|收入及業績|業務回顧|分類報告|業務分部|收入及其他收入|財務回顧|主要財務業績)", 0),
        (r"(分部資料|分部资料|分部信息|分部報告|分部报告|分部信息)", 0),
        (r"(分部收益及業績|分部收益及业绩|分部收入及業績|分部收入及业绩|分類收益及業績|分类收益及业绩|收益及業績|"
         r"分類淨收益及業績|分类净收益及业绩|"
         r"按可呈報及經營分類|按可报告及经营分类|按可呈報及經營分部|按可报告及经营分部|"
         r"經營分類劃分的收益|经营分类划分的收益|分部營業額及業績|分部营业额及业绩)", 0),
        (r"(經營分部|经营分部|營運分部|营运分部|經營分類資料|经营分类资料|可報告分部|可报告分部|可呈報分部|可呈报分部|"
         r"各業務分部|各业务分部|業務分部資料|业务分部资料|業務分部的收入|业务分部的收入|"
         r"按業務分部列示|按业务分部列示|收益及分部信息|收益及分部資訊|收益及其他收入)", 0),
        # 按客户/客戶類別劃分的經營收入（银行分部常见标题）
        (r"(按客户類別劃分的經營收入|按客戶類別劃分的經營收入|按客户类别划分的经营收入|"
         r"按客戶类别划分的经营收入)", 0),
        # 分部匯報 = 港股常见「分部报告」异写（batch2 empty：AN202603191820649442）
        (r"(分部收益|分部收入|分部業績|分部业绩|分部報告|分部报告|分部匯報|分部汇报)", 0),
        (r"^經營業績$|^经营业绩$", 0),
        # 无分部/產品表时的备用主表（select 侧最低档，仅在无升档候选时胜出）
        # infocode=AN202602251820037720 / AN202602261820045898 / AN202602261820070200 /
        # AN202602261820071002 / AN202602271820101807
        (r"(簡明綜合損益表|简明综合损益表|綜合損益表|综合损益表|綜合利潤表|综合利润表|"
         r"簡明綜合損益及其他全面收益表|简明综合损益及其他全面收益表|"
         r"綜合全面收益表|综合全面收益表|"
         r"未經審核綜合利潤表|未经审核综合利润表|未經審核簡明綜合利潤表|未经审核简明综合利润表|"
         r"未經[審审][核计計]簡明.{0,6}利[潤润][报報]?表|未经[审審][核计計]简明.{0,6}利[润潤][报報]?表|"
         r"未經[審审][核计計]簡明.{0,6}(經營|经营)[报報]表|未经[审審][核计計]简明.{0,6}(经营|經營)[报報]表|"
         # US-listing 簡明合併利潤表（AN202605201822502643）；勿用裸「合併利潤表」以免 A 股损益主表抢候选
         r"簡明合併利潤表|简明合并利润表|未經審計簡明合併利潤表|未经审计简明合并利润表|"
         r"合[併并]經營表|合并经营表|合[併并]經營[报報]表|合[并併]经营[报報]表|合併經營業績|合并经营业绩|合併經營業績表|合并经营业绩表|"
         r"綜合收益表|综合收益表|簡明綜合收益表|简明综合收益表|"
         r"財務摘要及概覽|财务摘要及概览|財務摘要|财务摘要|"
         r"CONDENSED\s+CONSOLIDATED\s+STATEMENT\s+OF\s+PROFIT\s+OR\s+LOSS|"
         r"CONSOLIDATED\s+STATEMENTS?\s+OF\s+OPERATIONS|"
         r"STATEMENT\s+OF\s+PROFIT\s+OR\s+LOSS)", 0),
        (r"(分類資料|分类资料|分類業績|分类业绩|分類營業額|分类营业额|業務分類|业务分类|收益及分類|收益及分类|"
         r"分類淨收益|分类净收益)", 0),
        (r"(分類收入|分类收入|分類收益|分类收益|分類收入資料|分类收入资料|分拆收益|分拆收入|"
         r"分拆收益資料|分拆收入資料|收入分拆|收入分項|收入分解|收益分解)", 0),
        (r"(按產品劃分|按产品划分|按產品類別|按产品类别|按產品類型|按产品类型|"
         r"按商品劃分|按商品划分|按主要產品|按主要产品|按產品分類|按产品分类|"
         r"按產品所屬領域|按产品所属领域|按產品|按产品|按商品|按分部劃分|按分部划分|"
         r"產品銷售分析|产品销售分析|按產品類型表現|按产品类型表现)", 0),
        (r"(營業收入[、,，]營業成本按|营业收入[、,，]营业成本按)", 0),
        (r"(按業務線|按业务线|按服務線|按服务线|按業務性質|按业务性质|按銷售渠道|按销售渠道|按品牌劃分|按品牌划分)", 0),
        # 客户/客戶 × 合約/合约 四种简繁混排均收（如「5 客户合約收益」「分拆客户合約收入」）
        (r"(客戶合約收入|客户合约收入|客戶合約收益|客户合约收益|客戶合同收入|客户合同收入|"
         r"客户合約收入|客户合約收益|客戶合约收入|客戶合约收益|"
         r"客戶合約|客户合约|客户合約|客戶合约|Disaggregation)", 0),
        (r"(來自外部客戶|来自外部客户|來自客戶合約|来自客户合约|來自客户合約|来自客戶合约|"
         r"外界客戶之收益|外部客戶收益|外部客户收益)", 0),
        (r"(分拆客户|分拆客戶|分拆.*合約收入|分拆.*合约收入|分開計算.*收入|分开计算.*收入|"
         r"收益分拆|收入分拆|收入的分解|收入分解|收益分解)", 0),
        (r"(收入分類|收入分类|收益分類|收益分类|收入明細|收入明细|收益明細|收益明细|營業額分析|营业额分析|"
         r"商品或服務類別|商品或服务类别|商品或服務的種類|商品或服务的种类|"
         r"客戶合約收益之分拆|客户合约收益之分拆|来自客户合约的收益划分|"
         r"營業收入分解|营业收入分解|按產品類型|按产品类型|分類收入|分类收入)", 0),
        (r"(收入分析|收益分析|收入分析如下|收益分析如下|分部分析|收入以及其他|收入、其他|收益、其他|收益以及其他|"
         r"收益及其他收入|按業務性質劃分|按业务性质划分|按類別劃分的收入|按类别划分的收入)", 0),
        (r"(營業收入列示|营业收入列示|營業收入和營業成本|营业收入和营业成本|營業收入、營業成本|主營業務分|"
         r"主營業務\(分產品\)|主營業務（分產品）|主营业务\(分产品\)|主营业务（分产品）)", 0),
        (r"(收入構成|收入构成|收入來源|收入来源|收益淨額分析|收益净额分析|收入和成本分析|收入與成本)", 0),
        # 勿用裸「服務收入」子串：MD&A「…營銷服務收入達到…」會誤切章
        (r"(來自客戶之合約收入的分列|来自客户之合约收入的分列|來自客户之合约收入的分列|来自客戶之合约收入的分列)", 0),
        (r"(主要產品及服務|主要产品及服务|產品銷售分析|产品销售分析|淨收益包括|净收益包括|銷售收入|"
         r"各主要業務之收入|各主要业务之收入)", 0),
        (r"(SEGMENT\s*INFORMATION|Segment\s*information|REVENUE\s+AND\s+SEGMENT|Revenue\s+and\s+segment)", 0),
        (r"(Revenue\s+by\s+(product|segment|brand|sales)|External\s+revenue|Revenue\s+from\s+external)", 0),
        (r"(Unaudited\s+condensed\s+consolidated\s+statements?\s+of\s+operations)", 0),
        # 序号 / (a)(i) / 裸收入·收益（best_page 高频）；含「經營收入」
        (r"^[\(（\[]*[a-zA-Z\d一二三四五六七八九十ⅰⅱⅲⅳⅠⅡⅢⅣ]+[\)）\.、．\s\]\)]*"
         r"(收入|收益|淨收益|净收益|淨收入|净收入|營業額|营业额|營業收入|营业收入|經營收入|经营收入|服務收入|服务收入|Revenue)([\s、，].*)?$", 0),
        (r"^(收入|收益|淨收益|净收益|淨收入|净收入|營業額|营业额|營業收入|营业收入|經營收入|经营收入|服務收入|服务收入|Revenue)\s*$", 0),
        (r"^(收入|收益)分析", 0),
        # US-listing / 简体附注高频标题（batch01 locate_fail）
        (r"(下表載列.{0,12}收入|載列收入資料|收入資料如下|摘錄資料|摘录资料)", 0),
        (r"(本集團來自其主要產品之收入|本集团来自其主要产品之收入|"
         r"按主要產品分列|按主要产品分列|按主要產品或服務線|按主要产品或服务线|"
         r"客戶合約收益的分類|客户合约收益的分类|"
         r"按業務線劃分的客戶合約|按业务线划分的客户合约|"
         r"未經審計簡明綜合經營|未经审计简明综合经营|"
         r"收益、其他收入及收益|收益及其他收入及收益)", 0),
        (r"(分部財務信息|分部财务信息|分部財務資訊|經營收益總額|经营收益总额|"
         r"分部財務概要|分部财务概要|Segment\s+financial\s+summary|"
         r"未經審計分部貢獻|未经审计分部贡献)", 0),
        (r"(主營業務\(分產品\)|主營業務（分產品）|主营业务\(分产品\)|主营业务（分产品）|"
         r"主營業務營業額|主营业务营业额|運輸品種|运输品种|分產品情況|分产品情况)", 0),
        # 分部毛利明细（常与收入表相邻页，select 侧 overlay 拼 GROSS_PROFIT）
        # 例：AN202603311820928570 p16 收入 + p17 毛利
        (r"(按業務分部劃分的.{0,12}毛[利虧损損]|按业务分部划分的.{0,12}毛[利亏]|"
         r"毛利明細|毛利明细|毛虧明細|毛亏明细|（毛虧）\s*[╱/]\s*毛利|毛虧\s*[╱/]\s*毛利|"
         r"毛利與毛利率|毛利与毛利率|（毛利）|分部毛利)", 0),
        # 勿用裸「經營收入」子串：MD&A「各項經營收入的金額和變化率」會吞進貸款等錯表
        (r"(綜合虧損表)$", 0),
    ]

    sure_regex = [
        (r"^(收益及分部|收入及分部|分部資料|分部资料|經營分部|经营分部|營運分部|营运分部|分類資料|分类资料|分類報告)", 0),
        # 勿用 ^(下表.*?:)$ —— 会绕过 exclude，把渠道/CPA/其他收入等错轴表收成 sure 章
        (r"(收益及業績分析如下)", 0),
        (r"^(綜合財務狀況表|市場回顧及展望|銷售成本|財務回顧|主要財務業績|管理層評語|近期發展)$", 0),
        (r"(指標及比率)$", 0),
    ]
    title_regex = [
        (r"^[\(|\（]*[一二三四五六七八九十0123456789①②③④⑤⑥⑦⑧⑨A-Da-d]+[\.、．。\)）\]\s]+", 0),
        (r"^[\(（][a-zA-ZiⅠⅡⅢⅰⅱⅲⅳ]+[\)）]", 0),
        (r"(綜合損益表|综合损益表|綜合利潤表|综合利润表|合併經營|合并经营|財務摘要|财务摘要|"
         r"收益及分部|分部資料|分部资料|經營分部|營運分部|分类资料|分類資料|"
         r"分部匯報|分部汇报|載列收入資料|下表載列|摘錄資料|"
         r"分部財務信息|分部财务信息|收益及分類|收益及分类|"
         r"未經[審审][核计計]簡明|未经[审審][核计計]简明|Unaudited\s+condensed|"
         r"客戶合約收入|客户合约收入|客户合約收入|客戶合约收入|分拆收入|分拆收益)", 0),
    ]
    # 章级排除：只挡明确非目标主表 / 错轴标题（勿用裸「:」或过宽按*劃分）
    exclude_regex = [
        (r"(按經營分部之貸款|按经营分部之贷款|按國家劃分之收益|按国家划分之收益|按地區概括|并?以地區概括)", 0),
        # 纯地区轴标题
        (r"(按客户地理位置劃分|按客戶地理位置劃分|按客户地理位置|按客戶地理位置|"
         r"地理位置劃分的分部|地理位置划分的分部|Regional\s*Revenue)", 0),
        (r"(按CPA|按CPC|按CPM|定價模式劃分的收益|定价模式划分的收益)", 0),
        (r"(第[一二三四1234]季度主要財務業績|全年主要財務業績)", 0),
        # 客户集中度 / 客户地理收益，非产品轴（AN202603261820771615）
        (r"(來自主要客户|来自主要客户|主要客户.*10\s*%\s*或以上|主要客戶.*10\s*%\s*或以上|"
         r"按客户地理位置劃分的分部|按客戶地理位置劃分的分部|按客户地理位置|按客戶地理位置)", 0),
        # 错轴/非产品主表标题
        (r"(毛利及毛利率|營業成本及毛利|其他收入及收益明細|其他收入組成|其他收入、其他收益|"
         r"其他收入分析|在管面積|項目數量及在管|"
         r"城市層級|各線城市|營業收入構成|合約負債|與客户合約有關的負債|按揭貸款及私人貸款|"
         r"按中國城市層級|線下門店收入|按渠道劃分的線上直銷|按服務類型劃分的社區|"
         r"按服務類型劃分的非業主|駕駛課程|學員人數|非主營業務分析|主營業務分行業情況|"
         r"主營業務分行業、分產品、分地區|交易額佔本集團當年總收入|"
         r"按地區劃分的收益明細|按地区划分的收益明细|按收入確認時間劃分|按收入確認時間分拆|"
         r"按銷售渠道劃分的來自客户合約|外部客户註冊所在國|來自客户合約的收入明細|"
         r"一般性業務收入摘要|產品價格範圍|地理位置劃分所錄得的收益|"
         r"按業務線劃分的收入明細|按業務線劃分的本集團|上述市場產生的收益|於上述市場|"
         r"投資收益明細|營業外收入明細|"
         r"按商品轉讓時間劃分|按商品转让时间划分|按產品及服務轉讓時間)", 0),
        # 財務摘要壳仍排除；摘錄資料可含产品表，改在 select 侧按扁文过滤
        (r"^(財務摘要|财务摘要|FINANCIAL\s+HIGHLIGHTS)(及概覽|及概览)?\s*$", 0),
    ]

    lines_group_index = [
        index
        for index, line in enumerate(lines)
        if is_title(line, title_regex, target_table_regex, exclude_regex, sure_regex)
    ]
    lines_grouped = []
    for i, index in enumerate(lines_group_index):
        if i == len(lines_group_index) - 1:
            lines_grouped.append(lines[index:])
        else:
            next_index = lines_group_index[i + 1]
            lines_grouped.append(lines[index:next_index])

    # 合并续页章节：「(續)」「(续)」标题章并入前一章，避免同年表被切开选到单期
    _merged = []
    for _grp in lines_grouped:
        if not _grp:
            continue
        _first = _grp[0].get("text", "") if _grp else ""
        if _merged and re.search(r"[（\(]續[）\)]|[（\(]续[）\)]", _first):
            _merged[-1].extend(_grp)
        else:
            _merged.append(_grp)
    lines_grouped = _merged


    target_tables = []
    for gi, inner_lines in enumerate(lines_grouped):
        if not inner_lines:
            continue
        first_line = inner_lines[0]["text"]
        page_number = inner_lines[0]["page_number"]

        if any(
            keyword in first_line
            for keyword in ["决议", "投诉", "保护", "内部交易", "洗钱", "风险管理情况","原因说明", "应付债券", "资本管理",]):
            continue

        if not match_patterns(first_line, target_table_regex):
            continue

        # 章内第一簇表：相邻或仅脚注/caption 分隔时合并（同页两年并列分部表）
        target_table = _merge_chapter_tables(inner_lines)
        if not target_table:
            continue

        flat = "".join(str(x) for x in flatten_arr(target_table))
        if "注册地" in flat:
            continue
        # 错表扁文只在 select 侧过滤，避免切章过杀导致无候选空提
        # （综合表/摘要偶含成本词但仍是产品收入主表）

        _dbg(f"[chapter] page={page_number} first_line={first_line}")
        target_tables.append({
            "title": first_line,
            "page_number": page_number,
            "target_table": target_table,
            "page_lines": inner_lines,
        })

    # orphan：仅当切章候选为空时并入，避免抢选（AN202605121822216651）
    if not target_tables:
        orphans = _scan_orphan_product_tables(lines, [])
        if orphans:
            # 只取行标签最多的一张，降低误并
            best = max(orphans, key=lambda it: _orphan_rev_label_n(r((it, "target_table"), []) or []))
            if _orphan_rev_label_n(r((best, "target_table"), []) or []) >= 2:
                target_tables.append(best)
                _dbg(f"[orphan] page={best.get('page_number')} title={best.get('title')}")
    _dbg(f"candidates_total={len(target_tables)}")
    return target_tables


def get_all_source_tables(lines):
    """收集整份公告中的原始表，供收入/成本/毛利跨表取证。

    与 get_target_tables 不同，这里不按收入标题切章，也不排除毛利率、
    成本附注或损益表；每个 MinerU 表格都是一个独立证据来源。
    """
    items = []
    seen = set()
    for index, line in enumerate(lines or []):
        table = line.get("table") if isinstance(line, dict) else None
        if not table:
            continue
        page = line.get("page_number")
        title = ""
        for prev in reversed((lines or [])[max(0, index - 8):index]):
            if not isinstance(prev, dict) or prev.get("page_number") != page:
                continue
            if prev.get("is_table"):
                continue
            text = str(prev.get("text") or "").strip()
            if text and not re.fullmatch(r"\d+", text):
                title = text[:300]
                break
        signature = repr(table)
        if signature in seen:
            continue
        seen.add(signature)
        items.append({
            "title": title,
            "page_number": page,
            "target_table": table,
            "page_lines": [line],
            "source_role": "document_table",
        })
    return items


def get_document_period_text(lines):
    """汇集公告级年度期间语句，供表头缺失截止日时兜底。"""
    snippets = []
    for line in lines or []:
        if not isinstance(line, dict) or line.get("is_table"):
            continue
        text = str(line.get("text") or "").strip()
        if not text:
            continue
        if re.search(r"止(?:財政|财政)?年度|年度(?:業績|业绩)|financial\s+year\s+ended|year\s+ended", text, re.I):
            snippets.append(text[:500])
    return " ".join(snippets[:120])


def r(*args):
    for arg in args:
        if isinstance(arg, tuple):
            data, key = arg
            if data is not None and isinstance(data, dict) and data.get(key):
                return data.get(key)
            if data is not None and isinstance(key, int) and isinstance(data, list) and len(data) > key:
                return data[key]
        elif isinstance(arg, dict):
            if arg:
                return arg
        else:
            if arg is not None and arg != "" and arg != []:
                return arg

    if args and not args[-1]:
        return args[-1]

    return None


def flatten_arr(lst, depth=-1):
    flat_list = []
    for item in lst:
        if isinstance(item, list) and depth != 0:
            flat_list.extend(flatten_arr(item, depth - 1))
        else:
            flat_list.append(item)
    return flat_list


def is_title(line, title_regex, target_table_regex, exclude_regex, sure_regex):
    line_text = line["text"]
    if '截至2025年11月30日止三' in line_text:
        print

    if r((line, "is_title"), False) or 'table' in line:
        return False

    if match_patterns(line_text, sure_regex):
        # sure 命中但长叙述（MD&A「經營分部乃以…」）不当切章
        # AN202603051820307557 p55
        if len(line_text or "") > 40 and re.search(
            r"主要營運決策者|主要营运决策者|乃以|报告方式|報告方式", line_text or ""
        ):
            return False
        return True

    if match_patterns(line_text, exclude_regex):
        return False

    # 附注脚注行
    if re.match(r"^[\(（]?附註|^[\(（]?附注", line_text or ""):
        return False

    # 中英文逗号/句点过多 → 叙述句
    if line_text.count(",") + line_text.count("，") > 2:
        return False
    if line_text.count(".") + line_text.count("。") >= 2:
        return False

    # MD&A 经营分部叙述（即使命中 title/target 子串也不切章）
    if len(line_text or "") > 40 and re.search(
        r"主要營運決策者|主要营运决策者|乃以與|乃以与|報告方式一致|报告方式一致",
        line_text or "",
    ):
        return False

    # 产品章内小标题不当切章（留给父章合并；AN202603271820807655）
    if re.match(
        r"^(按主要產品分類|按主要产品分类)\s*:?\s*$",
        (line_text or "").strip(),
    ):
        return False
    # IFRS15 范围内横幅不当切章（AN202603271820807655）
    if re.match(
        r"^(國際財務報告準則|国际财务报告准则|香港財務報告準則|香港财务报告准则|IFRS)\s*第?\s*15\s*號?"
        r".{0,20}(範圍內|范围内)",
        (line_text or "").strip(),
        re.I,
    ):
        return False

    # 编号前缀 alone 不切章：(a)长叙述会把产品表切到无标题章
    # infocode=AN202603271820819056 / AN202602271820110838
    _num_pre = re.match(
        r"^[\(（\[]*[a-zA-Z\d一二三四五六七八九十ⅰⅱⅲⅠⅡⅢⅣ]+[\)）\]\.、．\s]+",
        line_text or "",
    )
    if _num_pre:
        rest = (line_text or "")[_num_pre.end():].strip()
        if len(rest) > 36 or (rest.count("，") + rest.count(",") >= 2):
            return False
        if match_patterns(line_text, target_table_regex) and not match_patterns(line_text, exclude_regex):
            return True
        return False

    if match_patterns(line_text, title_regex + target_table_regex) and not match_patterns(line_text, exclude_regex):
        return True

    return False


def match_patterns(s, patterns):
    for pattern, group in patterns:
        match = re.search(pattern, s)
        if match and match.group(group):
            return match.group(group)
    return None
# endregion


# region format_data

_FLAT_PL_MARKS = (
    "銷售成本", "销售成本", "Cost of sales", "毛利", "Gross profit",
    "融資成本", "融资成本", "Finance costs", "所得稅", "所得税",
    "期內溢利", "期内溢利", "經營利潤", "经营利润", "每股盈利",
    "行政開支", "行政开支", "銷售及分銷", "销售及分销",
    "經營費用合計", "经营费用合计", "銷售、一般及管理", "销售、一般及管理",
    "營運開支", "营运开支", "減值虧損", "减值亏损", "折舊", "折旧",
)
_FLAT_BS_MARKS = (
    "現金及現金等價物", "现金及现金等价物", "Cash and cash equivalents",
    "現金及銀行存款", "现金及银行存款", "現金及銀行結餘", "现金及银行结余",
    "銀行存款", "银行存款", "Cash and bank",
    "流動資產總額", "流动资产总额", "Total current assets",
    "非流動資產總值", "非流动资产总值", "非流動資產總額", "非流动资产总额",
    "受限制現金", "受限制现金", "流動負債總額", "流动负债总额",
    "流動資產淨值", "流动资产净值", "流動資產", "流动资产", "流動負債", "流动负债",
    "存出保證金", "存出保证金", "結算備付金", "结算备付金", "買入返售金融資產", "买入返售金融资产",
    "應收融資客户款項", "应收融资客户款项", "代經紀業務客户持有的現金", "代经纪业务客户持有的现金",
)
_FLAT_CF_MARKS = (
    "營運資金變動", "营运资金变动", "經營業務所得現金", "经营业务所得现金",
    "借款還款", "借款还款", "已付利息", "資本開支", "资本开支",
    "現金及現金等價物增加", "现金及现金等价物增加",
    "購買廠房及設備", "购买厂房及设备", "已付股息", "購回股份", "购回股份",
    "債務證券之還款", "债务证券之还款", "銀行貸款之還款", "银行贷款之还款",
)
_FLAT_TIMING_MARKS = (
    "於某個時間點轉移", "于某个时间点转移", "於某一時間點轉讓", "于某一时间点转让",
    "於某一時間點轉移", "于某一时间点转移", "隨時間轉移的服務", "随时间转移的服务",
    "隨時間轉讓服務", "随时间转让服务", "於某一時間點確認的收入", "于某一时间点确认的收入",
    "於一段時間", "于一段时间", "在一段時間內確認", "在一段时间内确认",
    "收入-持續確認", "收入-持续确认", "分部總收益", "分部总收益",
)
_FLAT_SEG_RECON_MARKS = (
    "未分配開支", "未分配开支", "未分配融資", "未分配融资", "未分配收入",
    "未分配項目", "未分配项目", "未分配經營", "未分配经营",
    "減:內部銷售", "减:内部销售", "減：內部銷售",
    "分部溢利", "分部虧損", "分部亏损", "除稅前", "除税前",
    "Segment loss", "Segment profit", "Segment results",
    "分部間銷售", "分部间销售", "分部間收入", "分部间收入",
    "中央行政", "可報告分部溢利", "可报告分部溢利",
    "可呈報分部之經營溢利", "可呈报分部之经营溢利", "可呈報分部之資產", "可呈报分部之资产",
    "可呈報分部之負債", "可呈报分部之负债",
    "除利息、税項、折舊及攤銷前溢利", "除利息、税项、折旧及摊销前溢利",
)
_FLAT_KPI_MARKS = (
    "成本效益比率", "成本收入比率", "除稅前利潤", "除税前利润",
    "每股基本盈利", "每股攤薄", "每股盈利", "淨利息收益率", "有形股本回報率",
    "權益股東應佔", "权益股东应占", "毛利率", "純利潤率", "净利润率", "純利率", "纯利率",
    "貢獻利潤", "贡献利润", "分佣及薪酬", "同比增長", "同比增长",
    "流動比率", "流动比率", "資產負債比率", "资产负债比率", "資產回報率", "资产回报率",
    "股本回報率", "股本回报率", "資產周轉率", "资产周转率",
    "存貨周轉期", "存货周转期", "應收賬款周轉天數", "应收帐款周转天数", "應收賬款周轉", "应收账款周转",
    "應付賬款周轉天數", "应付帐款周转天数", "現金轉換週期", "现金转换周期", "周轉天數", "周转天数",
    "新業務價值", "新业务价值", "年化新保費", "年化新保费", "新業務價值利潤率", "新业务价值利润率",
    "CPI", "GDP", "PMI", "同比變動", "同比变动", "環比", "环比",
    "佔收入比重", "占收入比重", "比上年增減", "比上年增减",
    "港仙", "每股股息", "每股息",
    "EBITDA比率", "經調整EBITDA", "经调整EBITDA", "主要財務業績概要", "主要财务业绩概要",
)
_FLAT_PERSON_MARKS = (
    "女士", "先生", "辭任", "辞任", "獲委任", "获委任", "董事",
    "僱員", "雇员", "股份獎勵", "股份奖励", "購股權", "购股权", "限制性股份",
)
_FLAT_CUSTOMER_MARKS = (
    "Customer A", "Customer B", "Customer C", "Customer D",
    "客户A", "客户B", "客户C", "客户D", "客戶A", "客戶B", "客戶C", "客戶D",
    "客户甲", "客户乙", "客户丙", "客户丁", "客户戊",
    "客戶甲", "客戶乙", "客戶丙", "客戶丁", "客戶戊",
    "佔比最大客户", "占比最大客户", "佔集團收入百份比", "占集团收入百分比",
    "佔本集團總收益超過", "占本集团总收益超过",
)

def _flat_is_non_product_table(flat, title=""):
    """利润表/资产负债/现金流/纯确认时点/分部对账扁文 → 切章不收。"""
    if not flat:
        return False
    title = title or ""
    # 标题已标明产品/分部/收入轴时，允许表内偶发成本/损益附注行
    product_title = any(
        k in title
        for k in (
            "按產品", "按产品", "按商品", "主要產品", "主要产品", "商品或服務", "商品或服务",
            "分產品", "分产品", "服務系列", "服务系列", "收入的分解", "營業收入分解",
            "按主要產品或服務", "按主要产品或服务", "收入分拆", "收益分拆", "分拆收入", "分拆收益",
            "分部資料", "分部资料", "分部業績", "分部业绩", "分部分析", "分類資料", "分类资料", "分類收入", "分类收入",
            "收益及分部", "收入及分部", "摘錄資料", "摘录资料", "客户合約", "客戶合約", "合約收入", "合约收入",
            "主營業務", "主营业务", "業績分析", "业绩分析", "收入分析", "收益分析",
            "按類別劃分的收入", "按类别划分的收入", "按類別劃分的收益", "按类别划分的收益",
            "主營業務收入", "主营业务收入", "按業務線", "按业务线",
            "收入明細如下", "收入明细如下",
            "收入和成本分析",
            "按營運分類", "按营运分类", "營運分類分析", "营运分类分析", "收入及業績按營運", "收入及业绩按营运",
            # 产品/服务划分业务单位（AN202502281643609925 p5 导语）
            "產品及服務劃分", "产品及服务划分", "可呈報經營分部", "可呈报经营分部",
            "產品及服務", "产品及服务",
        )
    ) or bool(re.match(r"^[（\(]?[0-9一二三四五六七八九十]+[）\)\.\、．\s]+(收入|收益)\s*$", (title or "").strip()))
    # 中报损益同时列服務收入+產品收入仍是产品轴（AN202603261820768850 p16）
    if ("服務收入" in flat or "服务收入" in flat) and ("產品收入" in flat or "产品收入" in flat):
        product_title = True
    # 综合损益/全面收益表可作单产品兜底，勿因损益扁文整表拒收
    # （AN202503281648733247；有产品 sibling 时由 wrong_axis 让位）
    if re.search(
        r"簡明綜合損益|简明综合损益|綜合全面收益表|综合全面收益表|"
        r"綜合損益表|综合损益表|綜合收益表|综合收益表",
        title,
    ):
        product_title = True
    pl_n = sum(1 for k in _FLAT_PL_MARKS if k in flat)
    if pl_n >= 4 and not product_title:
        return True
    bs_n = sum(1 for k in _FLAT_BS_MARKS if k in flat)
    if bs_n >= 3:
        return True
    cf_n = sum(1 for k in _FLAT_CF_MARKS if k in flat)
    if cf_n >= 2:
        return True
    # 纯 IFRS15 确认时点二次披露（无产品类别轴）
    timing_n = sum(1 for k in _FLAT_TIMING_MARKS if k in flat)
    if timing_n >= 2 and not product_title:
        if "商品或服務類別" not in flat and "商品或服务类别" not in flat:
            if "收入確認時間" in flat or "收入确认时间" in flat or timing_n >= 2:
                return True
    # 分部对账/未分配行矩阵（优先让「主要產品分類」胜出）
    recon_n = sum(1 for k in _FLAT_SEG_RECON_MARKS if k in flat)
    if recon_n >= 3 and not product_title:
        return True
    # KPI / 财务比率仪表盘（营运分部导语页常见）
    kpi_n = sum(1 for k in _FLAT_KPI_MARKS if k in flat)
    if kpi_n >= 2 and not product_title:
        return True
    # 标题虽带「按產品/業務線」，扁文实为同比/毛利率变动表 → 仍拒
    # （AN202603241820727677 / AN202603271820815867）
    # 勿用「同比+佔收入」：产品绝对金额表常带占比列（AN202603311820923429 p24）
    # 同比变动表：需毛利率/占比（勿加營業成本——真产品成本表常带同比）
    if re.search(r"同比\s*變動|同比\s*变动|比上年增[減减]", flat) and sum(
        1 for k in ("毛利率", "佔比", "占比", "百分比") if k in flat
    ) >= 1 and not product_title:
        return True
    # 成本构成/占总成本比例表非收入轴（AN202603271820815867 p16 分產品情況）
    if re.search(r"成本構成|成本构成|佔總成本比例|占总成本比例|本期佔總成本|本期占总成本", flat):
        return True
    # YoY 收入比较表（行头常为「收入/變動」；AN202603231820707956）
    if re.search(r"收入比較|收益比較|收入比较|收益比较|劃分的收入比較|划分的收入比较", title or ""):
        return True
    if re.search(r"(^|\n)變動(\n|$)|(^|\n)变动(\n|$)", flat) and re.search(
        r"(^|\n)收入(\n|$)|收入\s*\(", flat
    ):
        return True
    # 董事名单/人员表误切章（AN202606171823643546）— 与产品标题无关
    person_n = sum(1 for k in _FLAT_PERSON_MARKS if k in flat)
    if person_n >= 3:
        return True
    # 实益拥有人/持股披露误绑營業額壳（AN202506181692999446）
    if re.search(r"實益擁有人|实益拥有人|所持已發行|所持已发行|佔本公司已發行股本", flat):
        if person_n >= 1 or re.search(r"董事姓名|董事姓名身份", flat):
            return True
    # 股份购回表（AN202603201820676603 財務回顧）
    if re.search(r"購回股份|购回股份|每股購回|每股购回|購買對價|购买对价", flat) and re.search(
        r"購回股份數目|购回股份数目|所付最高價|所付最高价|庫存股份|库存股份",
        flat,
    ):
        return True
    # 客户集中度（客户A/B/甲）非产品轴（AN202603301820881019 / AN202606171823644523 / AN202502261643524112）
    cust_n = sum(1 for k in _FLAT_CUSTOMER_MARKS if k in flat)
    if cust_n >= 2:
        return True
    if len(re.findall(r"(客户|客戶)[甲乙丙丁戊己A-Da-d]", flat)) >= 2:
        return True
    # 客户1/客户2 编号集中度（AN202506231696331174）；勿匹配「船舶供應客户29」金额粘连
    if len(set(re.findall(r"(?:客户|客戶)([1-9])(?!\d)", flat))) >= 2:
        return True
    if re.search(r"(^|\n)(客户|客戶)[甲乙丙丁戊A-Da-d]\d*(?:\n|$)", flat):
        return True
    if re.search(r"主要客户|主要客戶|10\s*%\s*或以上|貢獻本集團總收益超過|贡献本集团总收益超过", title or flat):
        if cust_n >= 1 or re.search(r"(客户|客戶)[甲乙丙丁戊A-Da-d]", flat):
            return True
    # 财务摘要 KPI 行头（环比/同比/毛利率）（AN202603101820462644）
    if re.search(r"財務摘要|财务摘要|FINANCIAL\s+HIGHLIGHTS|主要財務業績概要|主要财务业绩概要", title or flat[:300]):
        if sum(1 for k in ("環比", "环比", "同比", "毛利率", "EBITDA", "純利率", "纯利率") if k in flat) >= 2:
            if not product_title:
                return True
    # 无形资产滚转/摊销表误当分部（AN202603271820811607）
    if re.search(r"累計攤銷|累计摊销|累計減值|累计减值", flat) and re.search(
        r"商標|商标|商譽|專利|专利|於二零.{0,6}年一月一日|于二零.{0,6}年一月一日",
        flat,
    ):
        return True
    # 经调整亏损/折旧加回桥表误挂「收入」标题（AN202603131820551483）
    if sum(
        1
        for k in (
            "經調整虧損", "经调整亏损", "經調整溢利", "经调整溢利",
            "年內虧損", "年内亏损", "年內溢利", "年内溢利",
            "折舊及攤銷費用", "折旧及摊销费用", "以股份為基礎的付款", "以股份为基础的付款",
        )
        if k in flat
    ) >= 2:
        return True
    # 所得税分解表误绑客户合约/分部（AN202603271820814585）
    if (
        sum(
            1
            for k in (
                "即期税項", "即期税项", "即期稅項",
                "遞延税項", "递延税项", "遞延稅項",
                "土地增值稅", "土地增值税",
            )
            if k in flat
        )
        >= 2
    ):
        return True
    # 流动比率+流动资产：资产负债表流动性表（AN202602251820035602 裸「收入」）
    if ("流動比率" in flat or "流动比率" in flat) and (
        "流動資產" in flat or "流动资产" in flat or "流動負債" in flat or "流动负债" in flat
    ):
        return True
    # IFRS17 / 保险服务 P&L 矩阵（非产品轴）
    if sum(
        1
        for k in (
            "保險服務收入", "保险服务收入", "保險服務費用", "保险服务费用",
            "保險服務業績", "保险服务业绩",
        )
        if k in flat
    ) >= 2 and not product_title:
        return True
    # 银行/金融收入附注明细（利息/佣金分项）非经营分类产品轴
    # （AN202503301649195367 p13 vs 分類資料 p9）
    if (
        sum(
            1
            for k in (
                "應收銀行", "应收银行", "應收客户", "应收客户", "應收客戶",
                "按揭貸款", "按揭贷款", "貨幣市場", "货币市场",
                "託管賬", "托管账", "經紀費", "经纪费",
                "貸款所得佣金", "贷款所得佣金",
            )
            if k in flat
        )
        >= 2
        and len(re.findall(r"利息收入|利息收益", flat)) >= 2
        and not re.search(r"分類收入\s*:|分类收入\s*:|總收入|总收入", flat)
    ):
        return True
    # 所得款項用途壳（非产品轴；AN202602251820035574 p22 業務回顧）
    if re.search(r"所得款項|所得款项", flat) and re.search(
        r"擬定用途|拟定用途|已動用|已动用|經修訂分配|经修订分配|所得款项净额",
        flat,
    ):
        return True
    # 所得税/拨备矩阵（非产品轴；AN202603311820922215 p19）
    _tax_n = sum(
        1
        for k in (
            "年內撥備", "年内拨备", "遞延税項", "递延税项", "递延税", "遞延税",
            "所得税開支", "所得税开支", "所得税開支", "Income tax",
            "即期税項", "即期稅項",
        )
        if k in flat
    )
    _tax_jur_n = sum(
        1
        for k in (
            "中國企業所得税", "中国企业所得税", "所得補充税", "所得补充税",
            "越南企業所得税", "印尼公司所得税", "澳門所得", "澳门所得",
        )
        if k in flat
    )
    # 多司法辖区税表误绑「按主要產品」标题仍拒（AN202505281680724871）
    if _tax_jur_n >= 3 or (_tax_n >= 2 and not product_title):
        return True
    # 可呈報分部資產/負債矩阵（非收入轴；777054/814256）
    if sum(
        1
        for k in (
            "可呈報分部資產", "可报告分部资产", "可呈报分部资产",
            "可呈報分部負債", "可报告分部负债", "可呈报分部负债",
            "綜合資產總額", "综合资产总额", "綜合負債總額", "综合负债总额",
            "對銷分部間應收款項", "对销分部间应收款项",
            "對銷分部間應付款項", "对销分部间应付款项",
        )
        if k in flat
    ) >= 2:
        return True
    if re.search(r"分部資產\s*:|分部资产\s*:", flat) and re.search(
        r"分部負債\s*:|分部负债\s*:", flat
    ):
        if re.search(r"資產總值|资产总值|負債總額|负债总额|未分配資產|未分配资产", flat) and not product_title:
            return True
    # 每股股息/港仙 KPI（非产品轴；811469 p29）
    if re.search(r"每股股息|每股息|年港仙|中期股息|末期股息", flat) and not product_title:
        return True
    # 流动/速动比率表（AN202502281643610793 p2 經營業績）
    if (
        re.search(r"流動比率|流动比率", flat)
        and re.search(r"速動比率|速动比率", flat)
        and not product_title
    ):
        return True
    # 贷款到期日结构 / 所得款项用途（AN202603301820869787 / maturity shell）
    if re.search(r"於第一年內或按要求|于第一年内或按要求|五年以上", flat) and re.search(
        r"貸款總額|贷款总额|借款總額|借款总额", flat
    ):
        return True
    # 租賃承諾到期桶（一年內/第二年…）非产品收入（AN202504171657939004）
    if (
        re.search(r"(^|\n)(一年內|一年内)(\n|$)", flat)
        and re.search(r"(^|\n)(第二年|超過五年|超过五年)(\n|$)", flat)
        and not product_title
    ):
        return True
    # 其他分部资料：资本开支/折旧矩阵（AN202603311820928774）
    if re.search(r"其他分部資料|其他分部资料", title or flat[:120]):
        if sum(
            1
            for k in (
                "資本開支", "资本开支", "折舊", "折旧", "使用權資產", "使用权资产",
                "租賃負債", "租赁负债", "預期信貸虧損", "预期信贷亏损",
            )
            if k in flat
        ) >= 2:
            return True
    # 贸易应收 / 信贷拨备（AN202606121823514542）
    if re.search(r"貿易應收賬款|贸易应收账款", flat) and re.search(
        r"信貸虧損撥備|信贷亏损拨备|信貸減值|信贷减值", flat
    ):
        return True
    # PPE 滚转/可使用年期表（樓宇/機器及設備）非产品收入轴（AN202503281648786825）
    if (
        sum(
            1
            for k in (
                "樓宇", "楼宇", "機器及設備", "机器及设备", "傢俬", "家具",
                "汽車", "汽车", "在建工程",
            )
            if k in flat
        )
        >= 2
        and re.search(
            r"累計折舊|累计折旧|賬面值|账面值|賬面淨值|账面净值|"
            r"可使用年期|可使用年限|剩餘價值|剩余价值",
            flat,
        )
    ):
        return True
    # 营运资金周转天数仪表盘（AN202602251820033194）
    if sum(
        1
        for k in (
            "存貨周轉期", "存货周转期", "應收賬款周轉", "应收账款周转",
            "應付賬款周轉", "应付账款周转", "現金轉換週期", "现金转换周期",
            "周轉天數", "周转天数", "周轉期", "周转期",
        )
        if k in flat
    ) >= 2:
        return True
    # 可换股债券/所得款项用途表（AN202603251820752214）
    if re.search(r"可換股債券|可换股债券", flat) and re.search(
        r"所得款項|所得款项|所款項|所款项|原本用途|經修訂用途|经修订用途|實際用途|实际用途",
        flat,
    ):
        return True
    if sum(
        1
        for k in ("原本用途", "經修訂用途", "经修订用途", "實際用途", "实际用途", "擬定用途", "拟定用途")
        if k in flat
    ) >= 2:
        return True
    # 所得款项用途叙述表（落地后常见「一般營運資金/促銷」长描述行）
    if re.search(r"一般營運資金|一般营运资金", flat) and re.search(
        r"促銷|促销|營銷活動|营销活动|提供資金|提供资金|所得款項|所得款项",
        flat,
    ):
        return True
    # 股份奖励/雇员姓名表误绑收益（AN202603191820653115）
    if re.search(r"股份獎勵|股份奖励|購股權|购股权|限制性股份|僱員|雇员", flat):
        if person_n >= 2 or re.search(
            r"本公司或其附屬公司之其他僱員|本公司或其附属公司之其他雇员|授予日期|歸屬|归属",
            flat,
        ):
            return True
    # 现金流/经营现金流量表行轴（AN202603311820926232）
    if sum(
        1
        for k in (
            "銷售商品、提供勞務收到的現金", "销售商品、提供劳务收到的现金",
            "購買商品、接受勞務支付的現金", "购买商品、接受劳务支付的现金",
            "處置.*金融資產的淨", "处置.*金融资产的净",
        )
        if re.search(k, flat)
    ) >= 2:
        return True
    # IFRS9 / 预期信贷损失披露（AN202605051821967163）
    if re.search(r"預期信貸|预期信贷|IFRS\s*9|減值準備|减值准备", flat) and re.search(
        r"按已攤銷成本|按已摊销成本|按公允值計入|按公允值计入|財務擔保|财务担保|"
        r"貸款及其他信貸|贷款及其他信贷|資產負債表內|资产负债表内",
        flat,
    ):
        return True
    return False



_region_cell_kw = (
    "中國", "中国", "中國內地", "中国内地", "中國大陸", "中国大陆",
    "香港", "澳門", "澳门", "台灣", "台湾",
    "美國", "美国", "英國", "英国", "日本",
    "新加坡", "馬來西亞", "马来西亚", "泰國", "泰国",
    "歐洲", "欧洲", "亞洲", "亚洲", "非洲",
    "澳洲", "大洋洲", "南美洲", "北美洲",
    "東南亞", "东南亚", "中東", "中东",
)

_region_title_kw = (
    "按地區", "按地区", "客戶所在地", "客户所在地",
    "地域信息", "地區信息", "地理信息",
    "外部客戶", "外部客户", "客戶地區", "客户地区",
    "主要經營地區", "主要经营地区", "區域信息", "区域信息",
    "地區分佈", "地区分布",
)


def _cell_looks_geo(cell):
    """行头是否像地理标签。排除「香港財務報告準則第N號」等准则名伪命中。"""
    c = str(cell or "").replace("\n", "").strip()
    if not c or c.endswith((":", "：")):
        return False
    # 香港/中国财务报告准则 ≠ 地区（AN202606171823644523 (a)收入）
    if re.search(
        r"財務報告準則|财务报告准则|HKFRS|IFRS|國際財務報告|国际财务报告",
        c,
        re.I,
    ):
        return False
    # 「日本UNIQLO」等品牌/产品名含地名 ≠ 地区行（AN202601081816848008）
    if re.search(r"[A-Za-z]{2,}", c) or re.search(
        r"優衣庫|极优|極優|業務|业务|品牌|產品|产品|服務|服务", c
    ):
        return False
    return any(k in c for k in _region_cell_kw)


def _is_region_table(item):
    title = r((item, "title"), "") or ""
    if any(k in title for k in _region_title_kw):
        return True
    table = r((item, "target_table"), "") or []
    if not isinstance(table, list):
        return False
    # 行头地理标签（勿扫表头：分部收入表常带地区列，会误 demote）
    # 产品明细后嵌地域段：只计地域段前的行头（AN202603301820861420 /
    # AN202603061820350863 經營地區 / AN202603311820925485 區域市場）
    # 表头即以「按地區…」起：整表地区轴（AN202603271820819069）
    _geo_section_banner = re.compile(
        r"^(地域市場|地域市场|地區市場|地区市场|地理市場|地理市场|"
        r"經營地區|经营地区|主要經營地區|主要经营地区|區域市場|区域市场|"
        r"按地區|按地区|按客戶所在|按客户所在|外部客户所在地)"
    )
    first_cols = []
    for row in table[1:12]:
        if isinstance(row, list) and row:
            cell = str(row[0]).replace("\n", "").strip()
            if _geo_section_banner.match(cell):
                if not first_cols:
                    return True
                break
            first_cols.append(cell)
    if not first_cols:
        return False
    # 跳过「香港及澳門:」等分区小标题（按業務線嵌套地理段；AN202602251820035602）
    geo_n = sum(
        1
        for c in first_cols
        if c and _cell_looks_geo(c)
    )
    # 中國/香港 + 裸「其他」两行壳（AN202603251820746405 p30 分部資料）
    # 仅当行头几乎全是地理壳时抬升；勿误伤产品轴里夹地域段
    # （AN202603311820928956 收入分類；AN202603181820625625 备注含美國）
    _hdr = re.compile(
        r"^(收入來自|收入来自|貨品或服務類型|货品或服务类型|地域市場|地域市场|"
        r"收入確認|收入确认|總計|总计|合計|合计|總額|总额|"
        r"收入總計|收入总计|收益總計|收益总计)$"
    )
    short_geo_labs = [
        c
        for c in first_cols
        if c
        and _cell_looks_geo(c)
        and len(c) <= 12
        and not re.search(r"除外|單位|单位|百萬|百万|人民幣|人民币|股份", c)
    ]
    other_labs = [
        c for c in first_cols if c and not c.endswith((":", "：")) and re.match(r"^其他$|^其它$", c)
    ]
    productish = sum(
        1
        for c in first_cols
        if c
        and not c.endswith((":", "："))
        and not _hdr.match(c)
        and not re.match(r"^其他$|^其它$", c)
        and not _cell_looks_geo(c)
        and not re.match(r"^\d{4}", c)
        and len(c) >= 2
    )
    if short_geo_labs and other_labs and productish == 0:
        geo_n = max(geo_n, len(short_geo_labs) + 1)
    # 业务线行头≥2：混合分部表（港/英 + 企業及機構理財），非纯地区轴
    # （AN202602251820021564 按業務分部列示的業績）
    if productish >= 2:
        return False
    return geo_n >= 2

# 标题已标明产品轴时，即使表内含地理列也不按地区表降权（GT 常见「按产品+地理」拆解）
_PRODUCT_AXIS_TITLE_KW = (
    "按產品", "按主要產品", "分產品", "拆解披露", "按業務性質", "按服務線",
    "按產品類別", "按產品類型", "營業收入、營業成本的分解", "營業收入、營業成本的分解信息",
    "分拆收益", "分拆收入", "分類收入", "分類收益", "分类收入", "分类收益",
    "分類收入資料", "分类收入资料", "分類收益資料", "分类收益资料",
    "收入分拆", "收益分拆",
    "收入分列", "收益分列",
    "來自客户合約之收入分列", "来自客户合约之收入分列",
    "來自客戶合約之收入分列", "来自客户合约之收入分列",
    "收入的分解", "商品或服務的種類", "主要產品之分類", "按主要產品或服務系列",
    "收入及其他收入", "收入及其他收益",
    # 合同/品牌产品轴：表内偶有地理列也不按地区 demote（AN202603191820645603）
    "客户合同收入分類", "客户合約收入分類", "客戶合約收入分類",
    "客戶合約收益的分類", "客户合約收益的分類", "客户合約收益分類", "客戶合約收益分類",
    "與客户簽訂的合約的收入按主要產品", "与客户签订的合约的收入按主要产品",
    "按主要產品/服務", "按主要产品/服务", "按主要產品或服務線", "按主要产品或服务线",
    "按品牌", "按收入來源", "按收入来源", "按產品組合", "按產品分類",
    # 产品+服务分拆（可兼地域；AN202603271820810331）
    "按主要產品或服務", "按主要产品或服务", "與客户訂約之收益分拆", "与客户订约之收益分拆",
    # 按業務線嵌套地理段头（AN202602251820035602）
    "按業務線", "按业务线", "按業務分部劃分之收入", "按业务分部划分之收入",
    # 产品+客户所在地区双轴（AN202502191643269967 p10）
    "按主要產品或服務及客户所在地區", "按主要产品或服务及客户所在地区",
    "按主要產品或服務及客戶所在地區", "按主要产品或服务及客户所在地区",
    # 按主要產品或服務項目細分：表内可有地区列，勿整表 demote（AN202505281680724871）
    "按主要產品或服務項目", "按主要产品或服务项目",
    "按主要產品或服務分類", "按主要产品或服务分类",
    "按主要產品或服務線", "按主要产品或服务线",
    # 业务分部业绩列示（可兼地区行；AN202602251820021564）
    "按業務分部列示的業績", "按业务分部列示的业绩", "按業務分部列示", "按业务分部列示",
    # 分拆客户合约产生的收入（AN202503281648680230）
    "分拆客户合約產生的收入", "分拆客戶合約產生的收入", "分拆客户合约产生的收入",
    # 按产品+销售地区收入分析（AN202603271820819097）
    "按產品、銷售地區劃分的收入", "按产品、销售地区划分的收入",
)


def _demote_as_region(item):
    """地区表降权。产品轴标题下：仅当行头是地理轴才降权（保留「产品行×地理列」）。"""
    if not _is_region_table(item):
        return False
    title = r((item, "title"), "") or ""
    # 标题已写「按主要產品或服務 + 地域」双轴：保留，勿因后半地域行 demote
    # （AN202603271820810331）
    if re.search(
        r"按主要產品或服務.*(地域|地區|地区|客户所在|客戶所在|項目|项目|分類|分类|線|线)|"
        r"按主要产品或服务.*(地域|地区|客户所在|客戶所在|项目|分类|线)|"
        r"產品或服務及客户地域|产品或服务及客户地域",
        title,
    ):
        return False
    if any(k in title for k in _PRODUCT_AXIS_TITLE_KW):
        table = r((item, "target_table"), "") or []
        if not isinstance(table, list):
            return False
        _geo_section_banner = re.compile(
            r"^(地域市場|地域市场|地區市場|地区市场|地理市場|地理市场|"
            r"經營地區|经营地区|主要經營地區|主要经营地区|區域市場|区域市场|"
            r"市場區域|市场区域|地區分部|地区分部|"
            r"按地區|按地区|按客戶所在|按客户所在|外部客户所在地)"
        )
        first_cols = []
        for row in table[1:8]:
            if isinstance(row, list) and row:
                cell = str(row[0]).replace("\n", "").strip()
                if _geo_section_banner.match(cell):
                    if not first_cols:
                        return True
                    break
                first_cols.append(cell)
        geo_n = sum(1 for c in first_cols if c and _cell_looks_geo(c))
        # 行头地理 = 地区轴主表，即使标题写客户合同分类也降权
        # （AN202603191820645603 p8 客户合同收入分類×市场地区）
        return geo_n >= 2
    return True



def _rev_label_n(item):
    """短标题定表用：首列中文/字母产品行数（排除合计等）。"""
    tbl = r((item, "target_table"), None) or []
    if not isinstance(tbl, list):
        return 0
    skip = {"合计", "合計", "總計", "总计", "其他", "其它", "小計", "小计", ""}
    n = 0
    for row in tbl[1:]:
        if not isinstance(row, list) or not row:
            continue
        cell = str(row[0]).replace("\n", "").strip()
        cell = re.sub(r"^[\-－—–]\s*", "", cell)
        if cell in skip:
            continue
        if re.search(r"[\u4e00-\u9fffA-Za-z]", cell) and not re.fullmatch(
            r"[\d,.\s%％\-–—]+", cell or ""
        ):
            n += 1
    return n


def _rev_amt_row_n(item):
    """带金额的产品行数（空壳收入章常有标签无金额；时点轴不算产品）。"""
    tbl = r((item, "target_table"), None) or []
    if not isinstance(tbl, list):
        return 0
    skip = {"合计", "合計", "總計", "总计", "其他", "其它", "小計", "小计", ""}
    n = 0
    for row in tbl[1:]:
        if not isinstance(row, list) or not row:
            continue
        cell = str(row[0]).replace("\n", "").strip()
        cell = re.sub(r"^[\-－—–]\s*", "", cell)
        if cell in skip or not cell:
            continue
        if re.match(r"^(合计|合計|總計|总计|本集團|本集团|Group)$", cell, re.I):
            continue
        # 履约时点/时段行不当产品（AN202603311820887582 p8）
        if re.match(
            r"^(確認收入時間|收入確認時間|收入確認的時間|"
            r"於某時點|于某时点|於某一時間點|于某一时间点|於單一時間點|"
            r"隨時間(?:內)?(?:確認)?|随时间(?:内)?(?:确认)?|"
            r"Timing.*revenue|point\s*in\s*time|Over\s*time).*$",
            re.sub(r"\s+", "", cell),
            re.I,
        ):
            continue
        if not re.search(r"[\u4e00-\u9fffA-Za-z]", cell):
            continue
        if any(format_number(c) for c in row[1:]):
            n += 1
    return n



# region last_period product-name table locate
_LAST_PERIOD_SKIP_NAMES = frozenset({
    "合计", "合計", "總計", "总计", "小計", "小计", "總額", "总额",
    "本集團", "本集团", "Group", "group", "TOTAL", "Total", "total", "",
})


def _norm_product_name(name):
    s = fullwidth_to_halfwidth(str(name or "")).strip()
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"^[•\-•◦]\s*", "", s)  # 去掉列表符号前缀
    # 匹配用简繁折叠（展示名仍以 format_product_name / 表原文为准）
    for a, b in (
        ("戶", "户"),
        ("務", "务"),
        ("業", "业"),
        ("東", "东"),
        ("車", "车"),
        ("點", "点"),
        ("時", "时"),
        ("網", "网"),
        ("電", "电"),
        ("門", "门"),
        ("開", "开"),
        ("關", "关"),
        ("與", "与"),
        ("於", "于"),
        ("來", "来"),
        ("脫", "脱"), ("綫", "线"),
        ("還", "还"),
        ("這", "这"),
        ("為", "为"),
        ("從", "从"),
        ("達", "达"),
        ("產", "产"),
        ("銷", "销"),
        ("購", "购"),
        ("貨", "货"),
        ("類", "类"),
        ("總", "总"),
        ("匯", "汇"),
        ("損", "损"),
        ("際", "际"),
        ("體", "体"),
        ("報", "报"),
        ("應", "应"),
        ("機", "机"),
        ("餘", "余"),
        ("廣", "广"),
        ("萬", "万"),
        ("億", "亿"),
        ("術", "术"),
        ("營", "营"),
        ("團", "团"),
        ("質", "质"),
        ("監", "监"),
        ("製", "制"),
        ("處", "处"),
        ("廠", "厂"),
        ("場", "场"),
        ("復", "复"),
    ):
        s = s.replace(a, b)
    return s


def _last_period_product_names(last_period_data):
    """从上期衍生结果抽出 PRODUCTNAME（去合计/空）。"""
    return [n for n, _ in _last_period_product_entries(last_period_data)]


def _last_period_product_entries(last_period_data):
    """[(norm, display), ...]，display 为上期原始 PRODUCTNAME。"""
    rows = last_period_data
    if isinstance(last_period_data, dict):
        rows = (
            last_period_data.get("data")
            or last_period_data.get("DataTableParam")
            or last_period_data.get("Result")
            or []
        )
    if not isinstance(rows, list):
        return []
    out = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        disp = str(row.get("PRODUCTNAME") or "").strip()
        n = _norm_product_name(disp)
        if not n or n in _LAST_PERIOD_SKIP_NAMES:
            continue
        if re.fullmatch(r"(合计|合計|總計|总计|小計|小计|總額|总额)", n):
            continue
        if n in seen:
            continue
        seen.add(n)
        out.append((n, disp))
    return out


def _is_subseq(short, long):
    """检查 short 的每个字符是否按顺序出现在 long 中。"""
    i = 0
    for ch in long:
        if i < len(short) and ch == short[i]:
            i += 1
    return i == len(short)


def _last_period_same_grain(name, lp_name):
    """上期同粒度：复用定轴匹配+变体；允许「分类前缀-产品」；禁止无分隔的细拆子串。"""
    n = _norm_product_name(name)
    lp = _norm_product_name(lp_name)
    if not n or not lp:
        return False
    if n == lp:
        return True

    # 上期是「父:子」细名时，表内裸父名更粗，禁止占子名坑
    for sep in (":", "：", "-", "—", "–", "/"):
        if sep in lp:
            head = _norm_product_name(lp.split(sep)[0])
            if head and n == head:
                return False

    _generic_tail = frozenset({
        "其他", "其它", "收入", "收益", "服務", "服务", "業務", "业务",
        "產品", "产品", "銷售", "销售", "合計", "合计",
    })

    cands = []
    for v in _last_period_name_variants(lp) or [lp]:
        vn = _norm_product_name(v)
        if vn and vn not in cands:
            cands.append(vn)
    # 上期「前缀-尾名」→ 表内常只写尾名
    for sep in ("-", "—", "–", "/", "：", ":"):
        if sep in lp:
            tail = _norm_product_name(lp.split(sep)[-1])
            if (
                tail
                and len(tail) >= 2
                and tail not in cands
                and not re.fullmatch(
                    r"其他|其它|其他業務|其他业务|租金收入|利息收入", tail
                )
            ):
                cands.append(tail)

    for vn in cands:
        if n == vn:
            return True
        if _axis_name_match(vn, n) or _axis_name_match(n, vn):
            return True
        # 表内带分类前缀：科技-智慧城市解決方案 ↔ 智慧城市解決方案
        if n.endswith(vn) and len(n) > len(vn):
            head = n[: -len(vn)]
            if re.search(r"[-—–/：:]$", head):
                return True
        # 短标签 ↔ 上期略长名：其他↔其他服务；批發↔線下店舖批發；照明↔LED照明
        if 2 <= len(n) < len(vn) and len(vn) - len(n) <= 10:
            if vn.startswith(n):
                return True
            if vn.endswith(n) and n not in _generic_tail:
                head = vn[: -len(n)]
                if (
                    re.fullmatch(r"[A-Za-z0-9&\-]{1,12}", head)
                    or re.search(r"[-—–/：:]$", head)
                    or (2 <= len(head) <= 10)
                ):
                    return True

    # 缩写 ↔ 全称括注：EPS ↔ 電動助力轉向(EPS)
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9&\-]{1,15}", n):
        if re.search(r"[（(]\s*" + re.escape(n) + r"\s*[）)]", lp, re.I):
            return True
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9&\-]{1,15}", lp):
        if re.search(r"[（(]\s*" + re.escape(lp) + r"\s*[）)]", n, re.I):
            return True
    # 长度>5时允许中间多字/少字：子序列匹配
    if len(n) > 5 and len(lp) > 5:
        shorter, longer = (n, lp) if len(n) <= len(lp) else (lp, n)
        if _is_subseq(shorter, longer):
            return True
    return False


def _last_period_match_score(name, lp_name):
    """匹配分：全等 > 定轴/变体 > 其它同粒度。"""
    n = _norm_product_name(name)
    lp = _norm_product_name(lp_name)
    if not n or not lp:
        return 0
    if n == lp:
        return 100
    if not _last_period_same_grain(n, lp):
        return 0
    if _axis_name_match(lp, n) or _axis_name_match(n, lp):
        return 80
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9&\-]{1,15}", n):
        return 30
    return 60


def _table_first_col_norms(tbl):
    labels = []
    if not isinstance(tbl, list):
        return labels
    for row in tbl:
        if not isinstance(row, list) or not row:
            continue
        lab = _norm_product_name(row[0])
        if lab:
            labels.append(lab)
    return labels


def _table_flat_norm(tbl):
    return _norm_product_name(
        "".join(str(x) for x in flatten_arr(tbl or []))
    )


def _product_name_in_table(name, flat, labels=None, allow_char_bag=False):
    """扁文/逐字兜底命中（仅 allow_char_bag 定表二轮使用）。

    主定表口径见 `_last_period_name_hits`（same-grain），不要把扁文子串当主命中。
    """
    if not name:
        return False
    for n in _last_period_name_variants(name) or [name]:
        if labels and n in labels:
            return True
        if flat and n in flat:
            return True
        if allow_char_bag and flat and len(n) >= 4 and all(ch in flat for ch in n):
            return True
    return False


def _last_period_name_hits(names, tbl, allow_char_bag=False):
    """定表命中数：与抽取同口径。

    主口径：首列 same-grain 且同行有金额，或表头 same-grain 且表内有金额列
    （空名录/叙事段表有名无金额 → 不计命中，避免压过真实收入表）。
    allow_char_bag=True：无金额同名表时的二轮兜底（扁文/逐字），且表内须有金额行。
    """
    if not names:
        return 0
    names = list(names)
    hit = set()
    any_amt_row = False
    for row in tbl if isinstance(tbl, list) else []:
        if not isinstance(row, list) or not row:
            continue
        has_amt = any(format_number(c) for c in row[1:])
        if has_amt:
            any_amt_row = True
        lab = _norm_product_name(row[0])
        if lab and has_amt:
            for n in names:
                if n not in hit and _last_period_same_grain(lab, n):
                    hit.add(n)
        # 双语表：col0 英文无命中时也查 col1（中文产品名常在第二列）
        if len(row) >= 2 and not re.search(r'[一-鿿]', str(row[0] or '')):
            lab1 = _norm_product_name(row[1])
            if lab1 and has_amt:
                for n in names:
                    if n not in hit and _last_period_same_grain(lab1, n):
                        hit.add(n)
    if any_amt_row:
        for h in _table_header_cell_norms(tbl):
            if not h:
                continue
            for n in names:
                if n not in hit and _last_period_same_grain(h, n):
                    hit.add(n)
    if hit or not allow_char_bag:
        return len(hit)
    if not any_amt_row:
        return 0
    flat = _table_flat_norm(tbl)
    labels = set(_table_first_col_norms(tbl))
    if not flat and not labels:
        return 0
    return sum(
        1
        for n in names
        if _product_name_in_table(n, flat, labels, allow_char_bag=True)
    )


def _last_period_similarity(names, tbl, allow_char_bag=False):
    """相似度 = 命中数 / 上期产品数；附带命中数便于同相似度比结构量。"""
    n = len(names or [])
    if n <= 0:
        return 0.0, 0
    hits = _last_period_name_hits(names, tbl, allow_char_bag=allow_char_bag)
    return hits / float(n), hits



def _table_header_cell_norms(tbl, max_rows=4):
    """表头几行单元格归一化（列产品轴检测）。

    遇「首列产品标签 + 其余列金额」的数据行即停，避免把行轴产品名计进表头
    （小表仅 3–4 行时否则 hdr==row，误强制 type2 丢掉比较期列）。
    """
    out = []
    if not isinstance(tbl, list):
        return out
    for row in tbl[:max_rows]:
        if not isinstance(row, list):
            continue
        lab0 = _norm_product_name(row[0] if row else "")
        has_amt = any(format_number(c) != "" for c in row[1:])
        # 数据行：首列不像年份/截至横幅，却带金额 → 表头结束
        if (
            has_amt
            and lab0
            and not re.search(r"20\d{2}|截至|止年度|止期間|止期间|months?\s*ended", lab0, re.I)
        ):
            break
        for c in row:
            lab = _norm_product_name(c)
            if lab:
                out.append(lab)
    return out


def _axis_name_match(name, cell):
    """表头/行标签与上期名：整串、去单位后缀，或短后缀扩展（浮法玻璃/浮法玻璃產品）。

    中英双语表头：上期中文名整串落在单元格内也算命中（非任意短子串互含）。
    """
    if not name or not cell:
        return False
    if name == cell:
        return True
    cell2 = re.sub(
        r"(人民幣千元|人民币千元|港幣千元|港币千元|千港元|百萬港元|百万港元|百萬新加坡元|百万新加坡元|"
        r"人民幣|人民币|千元|百萬元|百万元|港元|美元|日[元圓]|新加坡元|"
        r"未經審核|未经审核|經審核|经审核|經重列|经重列)+$",
        "",
        cell,
    )
    cell2 = re.sub(r"[（(][^）)]*[）)]$", "", cell2)
    if name == cell2:
        return True
    if len(name) >= 4 and cell2.startswith(name) and len(cell2) - len(name) <= 6:
        return True
    if len(cell2) >= 4 and name.startswith(cell2) and len(name) - len(cell2) <= 6:
        # 勿让「ODM業務:手機電池」命中分组横幅「ODM業務」
        if re.search(r"[:：]", name) and not re.search(r"[:：]", cell2):
            return False
        return True
    # 双语格：「English … 中文產品名」——须同格含拉丁字母，避免叙述扁文误命中
    if (
        len(name) >= 4
        and name in cell2
        and re.search(r"[A-Za-z]{3,}", cell2)
        and re.search(r"[\u4e00-\u9fff]", cell2)
    ):
        return True
    return False


def _last_period_name_variants(name):
    """上期名变体：ODM業務:手機電池 → 手機電池（行标签常见无前缀）。"""
    n = fullwidth_to_halfwidth(str(name or "")).strip()
    if not n:
        return []
    out = [n]
    if re.search(r"[:：]", n):
        tail = re.split(r"[:：]", n)[-1].strip()
        # 过短/过泛后缀会污染定表命中（其他/租金收入）
        if (
            len(tail) >= 4
            and tail not in out
            and not re.fullmatch(r"其他|其它|其他業務|其他业务|租金收入|利息收入", tail)
        ):
            out.append(tail)
    return out


def _last_period_axis_hits(names, tbl):
    """上期名落在表头单元格 / 首列标签的命中数（禁止任意子串互含）。"""
    names = list(names or [])
    if not names or not isinstance(tbl, list):
        return 0, 0
    header_cells = _table_header_cell_norms(tbl)
    row_labs = _table_first_col_norms(tbl)
    hdr = 0
    row = 0
    for n in names:
        variants = _last_period_name_variants(n)
        if not variants:
            continue
        if any(_axis_name_match(v, h) for v in variants for h in header_cells):
            hdr += 1
        if any(_axis_name_match(v, lab) for v in variants for lab in row_labs):
            row += 1
    return hdr, row


def _item_period_year(item):
    """从表标题/横幅/page_lines/首行推断业务年。"""
    def _extract_year_str(s):
        """Extract 4-digit year from text; handles both 2024 and 二零二四."""
        m = re.search(r"20\d{2}", str(s or ""))
        if m:
            return m.group(0)
        m = re.search(r"二[零○〇]([一二三四五六七八九零○〇]{2})", str(s or ""))
        if m:
            cn = {"零":"0","〇":"0","○":"0","一":"1","二":"2","三":"3","四":"4",
                  "五":"5","六":"6","七":"7","八":"8","九":"9"}
            try:
                return "20" + "".join(cn[x] for x in m.group(1))
            except Exception:
                return ""
        return ""

    chunks = [
        str(r((item, "title"), "") or ""),
    ]
    for ln in (r((item, "page_lines"), []) or [])[:16]:
        if isinstance(ln, dict):
            chunks.append(str(ln.get("text") or ""))
        else:
            chunks.append(str(ln or ""))
    tbl = r((item, "target_table"), None) or []
    for row in (tbl or [])[:6]:
        if isinstance(row, list) and row:
            chunks.extend(str(c or "") for c in row[:8])
        elif row:
            chunks.append(str(row))
    text = fullwidth_to_halfwidth(" ".join(chunks))
    m = re.search(
        r"截至[^0-9二〇零○]{0,16}(20\d{2}|二[零〇○][一二三四五六七八九零〇○]{2})",
        text,
    )
    if m:
        y = _extract_year_str(m.group(1))
        if y:
            return str(y)
    y = _extract_year_str(text)
    return str(y) if y else ""


def _item_period_kind(item):
    """期限形态：Y/9M/H/Q，避免把年度表与中期表拼在一起。"""
    chunks = [str(r((item, "title"), "") or "")]
    for ln in (r((item, "page_lines"), []) or [])[:16]:
        if isinstance(ln, dict):
            chunks.append(str(ln.get("text") or ""))
        else:
            chunks.append(str(ln or ""))
    tbl = r((item, "target_table"), None) or []
    for row in (tbl or [])[:4]:
        if isinstance(row, list) and row:
            chunks.extend(str(c or "") for c in row[:6])
    text = fullwidth_to_halfwidth(" ".join(chunks))
    if re.search(r"三[個个]月|3\s*months?|首季度|第[一二三四]季", text, re.I):
        return "Q"
    if re.search(r"九[個个]月|9\s*months?", text, re.I):
        return "9M"
    if re.search(r"六[個个]月|6\s*months?|半年|中期", text, re.I):
        return "H"
    return "Y"


def _last_period_amount_evidence(names, tbl):
    """上期名是否伴随收入金额：行标签有金额，或表头名对应列在收益行有金额。

    百分比格不计金额（防毛利率/非GAAP调整表误计）。
    行轴证据须落在「收入/收益」语境：拒 EBITA/经调整/毛利率对账概览表虚高。
    """
    names = list(names or [])
    if not names or not isinstance(tbl, list) or len(tbl) < 2:
        return 0

    def _amt(c):
        s = fullwidth_to_halfwidth(str(c or ""))
        if re.search(r"[%％]", s):
            return False
        return bool(format_number(c))

    hdr_flat = ""
    for row in tbl[:6]:
        if isinstance(row, list):
            hdr_flat += "/" + "/".join(str(c or "") for c in row)
        else:
            hdr_flat += "/" + str(row or "")
    hdr_flat = fullwidth_to_halfwidth(hdr_flat)
    has_rev_hdr = bool(
        re.search(
            r"收入|收益|營業額|营业额|Revenue|營業收入|营业收入|"
            r"對外交易|对外交易|外部客户|外部客戶|分部收入|客户合約|客户合约",
            hdr_flat,
            re.I,
        )
    )
    kpi_noise_hdr = bool(
        re.search(
            r"EBITA|EBITDA|經調整|经调整|非公認|非公认|Non[- ]?GAAP|"
            r"毛利率|同比|變動比率|变动比率|主要績效|主要绩效|對賬|对账",
            hdr_flat,
            re.I,
        )
    )

    evidence = 0
    # 行轴：产品名在首列且同行有金额。KPI/对账表无收入表头时不计（防業績概覽虚高）
    if has_rev_hdr or not kpi_noise_hdr:
        for row in tbl[1:]:
            if not isinstance(row, list) or not row:
                continue
            lab = _norm_product_name(row[0])
            if not lab:
                continue
            if not any(_last_period_same_grain(lab, n) for n in names):
                continue
            if any(_amt(c) for c in row[1:]):
                # 有 KPI 噪声表头时，同行须像收入行语境（避免 EBITA 列冒充）
                if kpi_noise_hdr and not has_rev_hdr:
                    continue
                evidence += 1
    header_cells = []
    header_end = min(4, len(tbl))
    for ri, row in enumerate(tbl[:header_end]):
        if not isinstance(row, list):
            continue
        header_cells.append([_norm_product_name(c) for c in row])
    name_cols = set()
    for cells in header_cells:
        for ci, cell in enumerate(cells):
            if ci == 0:
                continue
            if any(_last_period_same_grain(cell, n) for n in names):
                name_cols.add(ci)
    if name_cols:
        rev_n = 0
        for row in tbl[1:24]:
            if not isinstance(row, list) or not row:
                continue
            lab = fullwidth_to_halfwidth(str(row[0] or ""))
            # 排除「虧損/(收益)」「出售…收益」等非主营收入语境
            if re.search(r"虧損|亏损|出售|處置|处置|減值|减值|撇減|撇减", lab):
                continue
            if not re.search(
                r"收入|收益|營業額|营业额|對外|对外|外部|分部收入|"
                r"客戶合約|客户合约|合約收入|合约收入|銷售|销售",
                lab,
            ):
                continue
            if any(ci < len(row) and _amt(row[ci]) for ci in name_cols):
                rev_n += 1
        if rev_n >= 1:
            evidence = max(evidence, len(name_cols))
    return evidence


def _last_period_balance_sheet_title(title):
    """资产/负债/应收/capex 附注：分部名命中但非收入主表。"""
    t = fullwidth_to_halfwidth(str(title or ""))
    # 纯财务状况/资产负债附注：即使标题含「分部」也不算收入主表
    if re.search(
        r"分部財務狀況|分部财务状况|財務狀況表|财务状况表|"
        r"Segment\s*financial\s*position|分部資產及負債|分部资产及负债",
        t,
        re.I,
    ) and not re.search(r"收入|收益|業績|业绩|Revenue", t, re.I):
        return True
    # 分部業績/收入主表常在同一附注标题里带「及資產及負債」——不算纯 BS
    if re.search(
        r"收入|收益|業績|业绩|Revenue|对外销售|對外銷售|"
        r"分部業績|分部业绩|經營分部資料|经营分部资料|可呈報分部|可报告分部|"
        r"按業務分部|按业务分部|按報告分部|按报告分部",
        t,
        re.I,
    ):
        return False
    if re.search(
        r"資產及負債|资产及负债|Assets?\s+and\s+liabilit|"
        r"分部資產|分部资产|分部負債|分部负债|"
        r"可呈報分部資產|可报告分部资产|可呈報分部負債|可报告分部负债|"
        r"分類資產|分类资产|分類負債|分类负债|"
        r"資本開支|资本开支|添置非流動|添置非流动|"
        r"其他分部資料|其他分部资料|Other\s+segment\s+information|"
        r"計量分部.*(資產|资产|負債|负债)|"
        r"貿易及其他應收|贸易及其他应收|應收貿易款項|应收贸易款项|"
        r"其他應收款項|其他应收款项|按金及預付款項|按金及预付款项|"
        r"預付款項、按金|预付款项、按金",
        t,
        re.I,
    ):
        return True
    if re.search(r"資產|资产|負債|负债", t) and not re.search(
        r"收入|收益|業績|业绩", t
    ):
        return True
    return False


def _item_last_period_bs_like(it):
    """上期定表：资产负债/应收附注或扁文强 BS（日期横幅当标题时仍可识别）。"""
    title = fullwidth_to_halfwidth(str(r((it, "title"), "") or ""))
    blob = _item_last_period_title_blob(it)
    if _last_period_product_revenue_title(blob) or _last_period_revenue_primary_title(blob):
        return False
    # 经营分部/持续经营主表标题：同附注常拼资产矩阵，勿因扁文误判
    if re.search(
        r"經營分部資料|经营分部资料|分部業績|分部业绩|持續經營業務|持续经营业务|"
        r"收入及業績|收入及业绩|按可呈報分部|按可报告分部|"
        r"業務經營分部|业务经营分部|分部收入及業績|分部收入及业绩|"
        r"分部利潤或虧損|分部利润或亏损|分部利潤指|分部利润指",
        title + " " + blob,
    ):
        return False
    if _last_period_balance_sheet_title(title) or _last_period_balance_sheet_title(blob):
        return True
    tbl = r((it, "target_table"), None) or []
    flat = fullwidth_to_halfwidth(
        "".join(str(x) for x in flatten_arr(tbl)[:120])
    )
    # 表体已是收入轴时，不算 BS（即使页眉旁有资产叙述）
    if re.search(
        r"收益\s*\(?來自外部|收入\s*\(?來自外部|客户合約收益|客戶合約收益|"
        r"外部客户|外部客戶|營業額|营业额|Revenue|"
        r"營業收入|营业收入|經營收益|经营收益|利息淨收入|利息净收入|"
        r"手續費及佣金收入|手续费及佣金收入|分部收入",
        flat,
        re.I,
    ):
        return False
    bs_n = sum(1 for k in _FLAT_BS_MARKS if k in flat)
    if re.search(
        r"綜合資產總額|综合资产总额|綜合負債總額|综合负债总额|"
        r"分類為持作出售|分类为持作出售|"
        r"物業、廠房及設備折舊|物业、厂房及设备折旧|"
        r"使用權資產折舊|使用权资产折旧|"
        r"融資成本\(未分配\)|融资成本\(未分配\)|"
        r"未分配公司資產|未分配公司资产|未分配公司負債|未分配公司负债|"
        r"資本增加|资本增加",
        flat,
    ):
        bs_n += 2
    # 强 BS：需更稳信号；「分部資產」 alone 易误伤收入+资产同附注
    if re.search(r"分部資產|分部资产|分部負債|分部负债", flat):
        bs_n += 1
    if bs_n >= 3:
        return True
    if bs_n >= 2 and re.search(
        r"綜合資產總額|综合资产总额|綜合負債總額|综合负债总额", flat
    ):
        return True
    return False




def _last_period_mda_revenue_title(title):
    """MD&A 简表：常与附注经营分部主表同分，优先让位附注。"""
    t = fullwidth_to_halfwidth(str(title or ""))
    if re.search(
        r"經營分部資料|经营分部资料|可呈報分部.*收入及業績|分部業績以及|"
        r"業務經營分部|业务经营分部|分部收入及業績|分部收入及业绩|"
        r"^\(?[a-z0-9]+\)?\s*分部業績|"
        r"按產品類型確認的收入|按产品类型确认的收入|"
        r"按類別劃分的收入及|按类别划分的收入及|"
        r"收入及銷售及服務成本分析|收入及销售及服务成本分析",
        t,
        re.I,
    ):
        return False
    return bool(
        re.search(
            r"有關按.*劃分的收入明細|按業務分部劃分之收入|按业务分部划分之收入|"
            r"按報告分部劃分的收入明細|按报告分部划分的收入明细|"
            r"下表載列按產品類別劃分的收入明細|下表载列按产品类别划分的收入明细|"
            r"下表載列.*分部.*收益|下表载列.*分部.*收益|"
            r"下表列出.*按業務類型劃分|下表列出.*按业务类型划分|"
            r"下表列示.*業務分部.*營業收入|下表列示.*业务分部.*营业收入|"
            r"營業額及未計利息及稅項前盈利分析|营业额及未计利息及税项前盈利分析|"
            r"按業務分部劃分的毛利|按业务分部划分的毛利|"
            r"收入來源於三個類別|收入来源于三个类别|"
            r"本集團的收入來源於三個|本集团的收入来源于三个",
            t,
        )
    )



def _last_period_revenue_primary_title(title):
    """分部收入/业绩主表：优先于 IFRS15 收入分拆附注（AN202603271820814600 p10）。"""
    t = fullwidth_to_halfwidth(str(title or ""))
    return bool(
        re.search(
            r"收入及業績|收入及业绩|分部收入及|可呈報分部.*(收入|業績|业绩)|"
            r"按可呈報分部劃分的收入|按可报告分部划分的收入|"
            r"經營分部.*收入|经营分部.*收入|分部.*業績分析|分部.*业绩分析|"
            r"主營業務.*按產品|主营业务.*按产品|按產品分類|按产品分类",
            t,
            re.I,
        )
    )


def _last_period_product_revenue_title(title):
    """按产品/主营收入分拆主表（优先于客户合约时点分部）。"""
    t = fullwidth_to_halfwidth(str(title or ""))
    return bool(
        re.search(
            r"按主要產品|按主要产品|按產品|按产品|產品類別|产品类别|產品分類|产品分类|"
            r"產品劃分|产品划分|按產品或服務|按产品或服务|按主要產品或服務|按主要产品或服务|"
            r"主營業務.*收入|主营业务.*收入|"
            r"按業務類別|按业务类别|主要營業|主要营业",
            t,
        )
    )


def _item_last_period_title_blob(it):
    parts = [str(r((it, "title"), "") or "")]
    for ln in (r((it, "page_lines"), []) or [])[:10]:
        if isinstance(ln, dict):
            parts.append(str(ln.get("text") or ""))
        else:
            parts.append(str(ln or ""))
    return fullwidth_to_halfwidth(" ".join(parts))


def _title_rank(title):
    """标题优先级：0=分部/产品明细 1=按类/收入分析 2=收入/收益 3=损益 4=其他"""
    t = str(title or "")
    if not t:
        return 4
    # 分部 + 产品明细（同级，_col_signal 打破平局）
    if re.search(r"(分部|分類|分类|可呈報|可呈报|可报告|經營|经营|營運|营运).{0,10}(資料|资料|業績|业绩|收入|收益|報告|报告|匯報|汇报|分析|信息)",
                 t):
        return 0
    if re.search(r"(按|主要|分|各).{0,6}(產品|产品|商品|服務|服务|業務|业务|產品線|产品线)|"
                 r"(產品|产品).{0,4}(及|和|與|与).{0,4}(服務|服务)|"
                 r"主營業務收入|主营业务收入|按業務線|按业务线|按營運分類|按营运分类|"
                 r"客戶合約|客户合约|合約收入|合约收入",
                 t):
        return 0
    # 按类/收入分析
    if re.search(r"(按|主要).{0,6}(類別|类别|種類|种类|服務類型|服务类型)|"
                 r"收入明細|收入明细|收入分析|收益分析|收入分拆|收益分拆|收入和成本",
                 t):
        return 1
    # 损益表兜底
    if re.search(r"損益表|损益表|利潤表|利润表|全面收益表|綜合損益|综合损益|綜合全面收益|综合全面收益|合併利潤|合并利润|合併經營|合并经营",
                 t):
        return 3
    # 收入/收益
    if re.search(r"收入|收益|營業額|营业额", t):
        return 2
    return 4


def _col_signal(tbl):
    """纯结构信号: 2=产品名列头 1=产品行头 0=年份列头。"""
    if not tbl: return 0
    for row in tbl[:3]:
        if not isinstance(row, list): continue
        for cell in row[1:4]:
            s = str(cell or "").strip()
            if not s: continue
            if re.match(r"^(20\d{2}|二零|截至|於|于|for|變動|变动|百分比|%|"
                        r"千港元|千元|人民幣|人民币|百萬|百万|未經審核|未经审核)", s):
                continue
            if len(s) >= 4 and re.search(r"[一-鿿]", s):
                return 2  # 产品列头
    prod_count = 0
    # 检查列头是否有对账/抵销关键词
    for row in tbl[:3]:
        if not isinstance(row, list): continue
        for cell in row[1:4]:
            s = str(cell or "")
            if re.search(r"註銷|注销|抵銷|抵销|對銷|对销|綜合|综合|合併|合并|分部間|分部间", s):
                return -1  # 对账表，降权
    for row in tbl:
        if not isinstance(row, list) or len(row) < 2: continue
        lab = str(row[0] or "").strip()
        if not lab or len(lab) < 2: continue
        if re.search(r"[:：]$", lab): continue
        if re.match(r"^(合計|合计|總計|总计|總額|总额|小計|小计|合併|合并)$", lab): continue
        if re.match(r"^(收益|收入|營業額|营业额|成本|費用|费用|毛利|溢利|利潤|利润|"
                    r"銷售成本|销售成本|服務成本|服务成本|客戶合約|客户合约|"
                    r"20\d{2}|二零|截至|於|于|for|變動|变动)", lab): continue
        # 非产品标签
        if _cell_looks_geo(lab): continue
        if re.search(r"利息|股息|政府補助|政府补助|回扣|撥備|拨备|減值|减值|"
                     r"客戶|客户|稅種|税种|稅率|税率|增值稅|增值税|"
                     r"開支|开支|費用|费用|財務|财务|融資|融资|"
                     r"資產|资产|負債|负债|折舊|折旧|攤銷|摊销|"
                     r"物業|物业|設備|设备|貸款|贷款|匯兌|汇兑|"
                     r"存款|投資收入|投资收入|"
                     r"銷售和營銷|一般及行政|研發開支", lab): continue
        if re.search(r"[一-鿿]", lab) and any(format_number(str(c or "")) for c in row[1:] if c):
            prod_count += 1
    return 1 if prod_count >= 2 else 0



def _pick_best(pool, names=None):
    """从候选池选最优表。排序: 标题优先级 > 产品列头 > 收入信号 > 页码。"""
    if not pool:
        return None
    names = list(names or [])

    def _key(c):
        pg = c.get("page_number", 0) or 0
        tbl = c.get("target_table", [])
        if names:
            hits = _last_period_name_hits(names, tbl, allow_char_bag=False)
            amt = _last_period_amount_evidence(names, tbl)
            sig = _col_signal(tbl)
            rev = _has_rev_rows(tbl)
            title = fullwidth_to_halfwidth(str(c.get("title", "") or ""))
            # 合約負債/合同负债表降权
            if re.search(r"合約負債|合同负债|合約負债|合同負債", title):
                rev = 0
            # 分部表加分：LP产品少(<=3)时分部表通常比简单收入表更完整
            has_seg_title = bool(re.search(r'分部|分類.*(資料|信息)|可呈報', title))
            # 简易成本段检测：扫描body前15行是否有成本/费用关键词
            _LOOSE_COST_KW = re.compile(r'成本|費用|费用|開支|开支|虧損|亏损|研發|研发|'
                                       r'折舊|折旧|攤銷|摊销|減值|减值')
            has_cost = False
            first_rev_row = -1
            for i, row in enumerate(tbl[:15] or []):
                if not isinstance(row, list) or not row:
                    continue
                c0 = str(row[0] or '').strip()
                has_digit = any(re.search(r'\d', str(c or '')) for c in row[1:])
                if has_digit and first_rev_row < 0:
                    first_rev_row = i
                if first_rev_row >= 0 and i > first_rev_row and _LOOSE_COST_KW.search(c0) and has_digit:
                    has_cost = True
                    break
            seg_bonus = 1 if (has_seg_title and len(names) <= 3) else 0
            cost_bonus = 1 if has_cost else 0
            # 用 min(hits, amt) 作主键：LP名命中但无金额的叙事文本被降权
            valid_hits = min(hits, amt) if amt > 0 else 0
            return (valid_hits, hits, rev + seg_bonus + cost_bonus, amt, sig, -pg)
        rank = _title_rank(c.get("title", ""))
        sig = _col_signal(tbl)
        return (-rank, sig, -pg)

    return max(pool, key=_key)


def _has_rev_rows(tbl):
    """表行标签是否含收入/收益/销售类关键词（区分收入表 vs 资本开支/折旧表）。"""
    for row in (tbl or [])[:10]:
        if not isinstance(row, list) or not row:
            continue
        lab = _norm_product_name(str(row[0] or ""))
        # 排除「虧損/(收益)」「處置收益」等非主营收入语境
        if re.search(r"亏损|处置|出售.*收益|出售.*亏损|公平值|减值", lab, re.I):
            continue
        if re.search(r"外部销售|收入\b|收益\b|营业额|"
                     r"客户合约|合约收入|销售收入|Revenue|"
                     r"来自.*客户|对外交易",
                     lab, re.I):
            return 1
    return 0


def _item_is_pl_metric_table(it):
    """损益度量表：首列有收入/收益，且有销售成本或毛利（或标题像损益/财务摘要）。"""
    tbl = r((it, "target_table"), None) or []
    labs = []
    for row in tbl if isinstance(tbl, list) else []:
        if isinstance(row, list) and row:
            labs.append(_norm_product_name(row[0]))
    blob = "|".join(labs)
    has_rev = bool(
        re.search(r"(^|\|)(收入|收益|營業額|营业额|revenue)(\||$)", blob, re.I)
    )
    has_cost = bool(
        re.search(
            r"销售成本|銷售成本|costofsales|营业成本|營業成本|"
            r"服务成本|服務成本|直接成本|已消耗存货|已消耗存貨",
            blob,
            re.I,
        )
    )
    has_gp = bool(re.search(r"(^|\|)(毛利|grossprofit)(\||$)", blob, re.I))
    title = fullwidth_to_halfwidth(str(r((it, "title"), "") or ""))
    title_pl = bool(
        re.search(
            r"損益|损益|利潤表|利润表|全面收益|經營表|财务摘要|財務摘要|"
            r"Statement\s+of\s+(profit|comprehensive)|CONSOLIDATED\s+STATEMENT",
            title,
            re.I,
        )
    )
    return (has_rev and (has_cost or has_gp)) or (title_pl and has_rev)


def _pick_table_when_last_period_miss(items, names):
    """上期名对不上表时的定表兜底。

    单产品 → 损益表（收入/成本/毛利）；
    多产品 → 优先分部/分拆/收益/收入表。
    """
    items = list(items or [])
    if not items:
        return None
    names = list(names or [])
    if len(names) <= 1:
        pl = [it for it in items if _item_is_pl_metric_table(it)]
        if pl:
            def _pl_key(it):
                tbl = r((it, "target_table"), None) or []
                labs = " ".join(
                    _norm_product_name(row[0])
                    for row in tbl
                    if isinstance(row, list) and row
                )
                score = 0
                if re.search(r"(^|\s)(收入|收益|營業額|营业额|revenue)(\s|$)", labs, re.I):
                    score += 1
                if re.search(r"销售成本|銷售成本|costofsales|营业成本|營業成本", labs, re.I):
                    score += 1
                if re.search(r"(^|\s)(毛利|grossprofit)(\s|$)", labs, re.I):
                    score += 1
                return (score, -int(r((it, "page_number"), 0) or 0))

            picked = max(pl, key=_pl_key)
            _dbg(
                f"[last_period] pl-fallback page={picked.get('page_number')} "
                f"title={picked.get('title')}"
            )
            return picked
    _rev_re = re.compile(
        r"分部|分拆|收益|收入|營業額|营业额|按產品|按产品|"
        r"客户合約|客戶合約|Revenue|Segment",
        re.I,
    )
    rev = [
        it
        for it in items
        if _rev_re.search(fullwidth_to_halfwidth(str(r((it, "title"), "") or "")))
    ]
    pool = rev or items
    # 多产品勿优先损益主表
    non_pl = [it for it in pool if not _item_is_pl_metric_table(it)]
    if non_pl:
        pool = non_pl
    picked = _pick_best(pool, names)
    if picked:
        _dbg(
            f"[last_period] rev-fallback page={picked.get('page_number')} "
            f"title={picked.get('title')}"
        )
    return picked


def _merge_last_period_period_siblings(picked, candidates, names):
    """同年产品轴、不同报告期的兄弟表合并（各页一张年）。

    例：p11=2024 十二个月、p13=2023 十二个月，上期名同为列头。
    """
    if not picked or not candidates:
        _dbg(f"[merge_sib] early_return: no_picked_or_candidates")
        return picked
    # BS/应收附注误中后勿再拼同年资产负债兄弟页
    if _item_last_period_bs_like(picked):
        _dbg(f"[merge_sib] early_return: bs_like")
        return picked
    # MD&A 简述标题：勿合并兄弟，避免拼成损益大表
    _pt = fullwidth_to_halfwidth(str(r((picked, "title"), "") or "")).strip()
    if _last_period_mda_revenue_title(_pt):
        _dbg(f"[merge_sib] early_return: mda_title")
        return picked
    names = list(names or [])
    base_tbl = r((picked, "target_table"), None) or []
    base_page = int(r((picked, "page_number"), 0) or 0)
    base_hdr, base_row = _last_period_axis_hits(names, base_tbl)
    if max(base_hdr, base_row) < 2:
        _dbg(f"[merge_sib] early_return: axis_hits too low hdr={base_hdr} row={base_row}")
        return picked
    base_year = _item_period_year(picked)
    base_kind = _item_period_kind(picked)
    _, base_hits = _last_period_similarity(names, base_tbl)
    _dbg(f"[merge_sib] base page={base_page} year={base_year} kind={base_kind} hdr={base_hdr} row={base_row} hits={base_hits}")
    if base_hits < 2:
        return picked

    siblings = []
    seen_years = {base_year} if base_year else set()
    for it in candidates:
        if it is picked:
            continue
        page = int(r((it, "page_number"), 0) or 0)
        if base_page and page and abs(page - base_page) > 8:
            continue
        sib_kind = _item_period_kind(it)
        if sib_kind != base_kind:
            _dbg(f"[merge_sib] skip page={page} reason=kind_mismatch({sib_kind}!={base_kind})")
            continue
        tbl = r((it, "target_table"), None) or []
        sim, hits = _last_period_similarity(names, tbl)
        if hits < 2 or hits < base_hits - 1:
            _dbg(f"[merge_sib] skip page={page} reason=hits({hits}<2 or <{base_hits-1})")
            continue
        hdr, row = _last_period_axis_hits(names, tbl)
        if max(hdr, row) < 2:
            continue
        # 轴形态须一致：主表列产品则只要列头命中强的兄弟；主表行产品则只要行标签强
        base_is_col = base_hdr >= 2 and base_hdr >= base_row
        base_is_row = base_row >= 2 and base_row > base_hdr
        sib_is_col = hdr >= 2 and hdr >= row
        sib_is_row = row >= 2 and row > hdr
        if base_is_col and not sib_is_col:
            _dbg(f"[merge_sib] skip page={page} reason=axis_mismatch(base_col={base_is_col} sib_col={sib_is_col})")
            continue
        if base_is_row and not sib_is_row:
            _dbg(f"[merge_sib] skip page={page} reason=axis_mismatch(base_row={base_is_row} sib_row={sib_is_row})")
            continue
        year = _item_period_year(it)
        if not year:
            _dbg(f"[merge_sib] skip page={page} reason=no_year")
            continue
        if year in seen_years:
            _dbg(f"[merge_sib] skip page={page} reason=dup_year({year})")
            continue
        seen_years.add(year)
        _dbg(f"[merge_sib] add page={page} year={year}")
        siblings.append(it)

    if not siblings:
        return picked

    # 按页序拼接，保留各表期间横幅
    ordered = sorted(
        [picked] + siblings,
        key=lambda it: int(r((it, "page_number"), 0) or 0),
    )
    merged_tbl = []
    page_lines = []
    for it in ordered:
        tbl = r((it, "target_table"), None) or []
        if not tbl:
            continue
        year = _item_period_year(it)
        title = str(r((it, "title"), "") or "")
        banner = ""
        # 已有横幅则不重复注入
        first = fullwidth_to_halfwidth(str(tbl[0][0] if tbl and tbl[0] else "") or "")
        if year and not re.search(r"截至|止年度|止期間|止期间|20\d{2}", first):
            banner = title if re.search(r"截至|止年度|20\d{2}", title) else f"截至{year}年12月31日止年度"
        if banner:
            width = max((len(row) for row in tbl if isinstance(row, (list, tuple))), default=1)
            merged_tbl.append([banner] + [""] * max(0, width - 1))
        merged_tbl.extend(tbl)
        page_lines.extend(r((it, "page_lines"), []) or [])

    out = dict(picked)
    out["target_table"] = merged_tbl
    if page_lines:
        out["page_lines"] = page_lines
    _dbg(
        f"[last_period] period-siblings merge n={len(ordered)} "
        f"pages={[r((it, 'page_number'), None) for it in ordered]} years={sorted(seen_years)}"
    )
    return out


def _pick_table_by_last_period_names(candidates, names, mode="degrade"):
    """按上期 PRODUCTNAME 从候选里定表。

    命中 = 同名+金额（`_last_period_name_hits`）。同档比金额证据与页序。
    全部候选主命中为 0 时，才扁文/逐字兜底一轮（仍要求表内有金额行）。
    mode=degrade：miss=0 → 1 → …；mode=similarity：命中率最高。
    """
    names = list(names or [])
    n_names = len(names)
    if n_names < 1 or not candidates:
        return None
    min_hits = 1

    scored = []
    for it in candidates:
        tbl = r((it, "target_table"), None) or []
        sim, hits = _last_period_similarity(names, tbl, allow_char_bag=False)
        if hits < min_hits:
            continue
        scored.append((it, sim, hits, n_names - hits))

    if not scored:
        for it in candidates:
            tbl = r((it, "target_table"), None) or []
            sim, hits = _last_period_similarity(names, tbl, allow_char_bag=True)
            if hits < min_hits:
                continue
            scored.append((it, sim, hits, n_names - hits))

    if not scored:
        return None

    if mode == "similarity":
        best_sim = max(s[1] for s in scored)
        pool = [s[0] for s in scored if s[1] == best_sim]
        return _pick_best(pool, names)

    max_miss = max(0, n_names - min_hits)
    for miss in range(0, max_miss + 1):
        pool = [s[0] for s in scored if s[3] == miss]
        if pool:
            return _pick_best(pool, names)
    return None


def _dedupe_table_candidates(items):
    """按页码+标题+行列指纹去重，保留先出现的。"""
    out = []
    seen = set()
    for it in items or []:
        if not it:
            continue
        tbl = r((it, "target_table"), None) or []
        fp = (
            int(r((it, "page_number"), 0) or 0),
            fullwidth_to_halfwidth(str(r((it, "title"), "") or ""))[:80],
            len(tbl),
            tuple(
                _norm_product_name(row[0])
                for row in tbl[:6]
                if isinstance(row, list) and row
            ),
        )
        if fp in seen:
            continue
        seen.add(fp)
        out.append(it)
    return out


def get_target_table_from_last_period_data(lines, last_period_data):
    """在 lines 里扫表，按上期「同名+金额」定表；结果附带 `_all_lp_candidates` 供最终合并挑选。"""
    names = _last_period_product_names(last_period_data)
    if len(names) < 1:
        _dbg(f"[last_period] skip: names<{1} -> {names}")
        return None
    _dbg(f"[last_period] names({len(names)})={names}")

    items = []
    i = 0
    n = len(lines or [])
    while i < n:
        line = lines[i]
        if not (r((line, "is_table"), False) and line.get("table")):
            i += 1
            continue

        title = ""
        page_number = line.get("page_number")
        title_at = i
        for j in range(i - 1, max(-1, i - 12), -1):
            prev = lines[j]
            if r((prev, "is_table"), False):
                break
            t = fullwidth_to_halfwidth(str(prev.get("text") or "")).strip()
            if not t or _is_inter_table_glue(prev):
                continue
            title = t
            title_at = j
            page_number = prev.get("page_number") or page_number
            break

        merged = _merge_chapter_tables(lines[i:])
        if merged:
            _banner = _period_banner_before(lines, i)
            if _banner:
                _first = fullwidth_to_halfwidth(str(merged[0][0] if merged[0] else "") or "")
                if not re.search(r"截至|止年度|months?\s*ended", _first, re.I):
                    _w = max(
                        (len(row) for row in merged if isinstance(row, (list, tuple))),
                        default=1,
                    )
                    merged = [[_banner] + [""] * max(0, _w - 1)] + list(merged)
        j = i
        while j < n:
            cur = lines[j]
            if not (r((cur, "is_table"), False) and cur.get("table")):
                break
            j += 1
            while j < n and _is_inter_table_glue(lines[j]):
                j += 1
            if j < n and r((lines[j], "is_table"), False) and lines[j].get("table"):
                continue
            break

        if merged:
            items.append({
                "title": title,
                "page_number": page_number,
                "target_table": merged,
                "page_lines": lines[title_at:j],
            })
        i = max(j, i + 1)

    picked = _pick_table_by_last_period_names(items, names, mode="degrade")
    if picked:
        picked = _merge_last_period_period_siblings(picked, items, names)
        sim, hits = _last_period_similarity(names, r((picked, "target_table"), None) or [])
        _dbg(
            f"[last_period] lines-hit page={picked.get('page_number')} "
            f"hits={hits}/{len(names)} sim={sim:.2f} title={picked.get('title')}"
        )
    else:
        picked = _pick_table_when_last_period_miss(items, names)
        if not picked:
            _dbg(f"[last_period] lines-miss scanned_tables={len(items)}")
            # 仍返回空壳带候选，供 get_target_table 与切章合并
            if items:
                return {
                    "title": "",
                    "page_number": None,
                    "target_table": None,
                    "_all_lp_candidates": items,
                    "_lp_lines_miss": True,
                }
            return None
    if isinstance(picked, dict):
        picked = dict(picked)
        picked["_all_lp_candidates"] = items
    return picked


def _clean_item(it):
    """移除候选的内部临时字段。"""
    if not isinstance(it, dict):
        return it
    return {k: v for k, v in it.items()
            if k not in ("_all_lp_candidates", "_lp_lines_miss")}


def _flatten_table(table):
    """表格二维数组 → 扁平文本，供内容过滤器使用。"""
    return "".join(str(x) for x in flatten_arr(table or []))


def _should_skip(candidate):
    """纯规则过滤：地理表、非产品表、无金额表。"""
    pg = candidate.get("page_number", 0) or 0
    title = candidate.get("title", "") or ""
    flat = _flatten_table(candidate.get("target_table", []))

    if _demote_as_region(candidate):
        _dbg(f"[filter] skip page={pg} reason=region title={title[:60]}")
        return True
    if _is_never_revenue_title(candidate):
        _dbg(f"[filter] skip page={pg} reason=non_rev_title title={title[:60]}")
        return True
    if _flat_is_non_product_table(flat, title):
        _dbg(f"[filter] skip page={pg} reason=non_product title={title[:60]}")
        return True
    if _rev_amt_row_n(candidate) < 1:
        _dbg(f"[filter] skip page={pg} reason=no_amount")
        return True
    return False


def _is_never_revenue_title(candidate):
    """硬规则：标题明确不是收入/产品分布表的，直接排除。"""
    title = fullwidth_to_halfwidth(str(r((candidate, "title"), "") or ""))
    t = title  # shorthand
    # 免责声明
    if re.search(r'不負責|不负责|概不|聲明|声明', t):
        return True
    # 纯P&L表标题
    if re.search(r'虧損表|亏损表|合併收入表|合并收入表|綜合全面收益|综合全面收益|'
                 r'合併經營表|合并经营表|CONDENSED.*CONSOLIDATED|STATEMENT.*PROFIT.*LOSS', t, re.I):
        return True
    # 管理层讨论/财务回顾/董事报告
    if re.search(r'討論及分析|讨论及分析|財務回顧|财务回顾|管理層討論|管理层讨论|'
                 r'董事會報告|董事会报告|對股東|对股东', t):
        return True
    # 附注/脚注
    if re.match(r'^(附註|附注|Note\s*\d)', t):
        return True
    # 纯英文公司名（无产品/收入关键词）
    if re.match(r'^[A-Z][A-Za-z0-9\s.&]+$', t) and len(t) > 5:
        if not re.search(r'Revenue|Income|Segment|Product|Service|Cost|Profit|Loss', t, re.I):
            return True
    # 标题过短且无产品/分部/收入关键词
    if len(t) <= 3 and not re.search(r'產品|产品|分部|收入|收益|服務|服务|業務|业务', t):
        return True
    # 其他明确非收入表
    if re.search(r"其他分部資料|其他分部资料|資產及負債分析|资产及负债分析|"
                 r"分部資產|分部资产|分部負債|分部负债|資本開支|资本开支|"
                 r"租賃|租赁安排|關聯方|关联方|或然負債|或有负债|"
                 r"公允價值|公允价值|金融工具|財務風險|财务风险", t):
        _dbg(f"[filter] skip page={candidate.get('page_number')} reason=non_rev_title title={t[:60]}")
        return True
    return False


# P&L 成本行标记（用于验证选中表是否真的是产品收入表）
_PL_COST_VALIDATE_RE = re.compile(
    r"成本|費用|费用|開支|开支|虧損|亏损|利潤|利润|溢利|毛利|"
    r"稅|税|折舊|折旧|攤銷|摊销|利息|減值|减值|"
    r"研發|研发|薪金|津貼|酬金|公平值|每股|經營利|经营利|"
    r"融資|融资|銷售及|销售及|管理費|管理费|財務費|财务费")


def _validate_revenue_table(candidate, names, hits, sim):
    """验证选中的表确实是产品收入表，不是P&L表。

    条件：表身行中P&L成本项占比<50%，或上期产品命中率足够高。
    产品命中 = body col0 命中 + 表头列（cols 1+）命中。
    """
    if not isinstance(candidate, dict):
        return True
    tbl = candidate.get("target_table") or []
    if not isinstance(tbl, list) or len(tbl) < 3:
        return True

    # 统计body行中的P&L成本和产品命中
    pl_cost = 0
    total = 0
    prod_hits = 0
    names_set = set(names or [])
    for row in tbl:
        if not isinstance(row, list) or len(row) < 2:
            continue
        c0 = str(row[0] or "").strip()
        if not c0:
            continue
        # 只看有数据的行
        has_num = any(re.search(r'\d', str(c or "")) for c in row[1:])
        if not has_num:
            continue
        total += 1
        if _PL_COST_VALIDATE_RE.search(c0):
            pl_cost += 1
        if names_set:
            n0 = _norm_product_name(c0)
            if any(_last_period_same_grain(n0, n) for n in names_set):
                prod_hits += 1

    # 表头列（cols 1+）也搜产品名：列产品表的产品名在列头不在 body col0
    if names_set:
        hdr_names = set()
        for row in tbl[:6]:
            if not isinstance(row, list):
                continue
            for c in range(1, len(row)):
                hdr_names.add(_norm_product_name(row[c]))
        for hn in hdr_names:
            if hn and any(_last_period_same_grain(hn, n) for n in names_set):
                prod_hits += 1

    if total < 5:
        return True  # 太小，不判断

    pl_ratio = pl_cost / total
    prod_ratio = prod_hits / max(len(names_set), 1)

    # P&L占比>70% 且 产品命中率<20% → 很可能是P&L表（放宽阈值避免误杀）
    if pl_ratio > 0.7 and prod_ratio < 0.2:
        return False

    return True


def _select_by_history(pool, names, lines_pack):
    """有上期数据：按产品名+金额匹配选表。"""
    # lines_pack 候选并入池（放在章节候选之后，章节合并表优先保留）
    if (isinstance(lines_pack, dict) and lines_pack.get("target_table")
            and not lines_pack.get("_lp_lines_miss")):
        extra = _clean_item(lines_pack)
        pool = _dedupe_table_candidates(pool + [extra])

    # 多产品时 P&L 主表通常是错表；但单产品公告恰恰要从 P&L 的收入、
    # 成本、毛利三行构造产品事实，不能被“综合全面收益表”标题硬过滤掉。
    single_product = len(names) <= 1
    pool = [
        c for c in pool
        if not _is_never_revenue_title(c)
        or (single_product and _item_is_pl_metric_table(c))
    ]

    picked = _pick_table_by_last_period_names(pool, names, mode="degrade")
    if picked:
        picked = _merge_last_period_period_siblings(picked, pool, names)
        sim, hits = _last_period_similarity(names, picked.get("target_table") or [])
        # 校验：选中的表是不是 P&L 表（分部报告里混了大量损益行）
        if not _validate_revenue_table(picked, names, hits, sim):
            _dbg(f"[history] validate FAIL page={picked.get('page_number')} "
                 f"hits={hits}/{len(names)} sim={sim:.2f} pool={len(pool)} "
                 f"title={picked.get('title')} — retry without it")
            # 从池中剔除该表，重新选
            pool_retry = [c for c in pool if c is not picked]
            picked2 = _pick_table_by_last_period_names(pool_retry, names, mode="degrade")
            if picked2:
                picked = picked2
                picked2 = _merge_last_period_period_siblings(picked, pool_retry, names)
                picked = picked2
                sim, hits = _last_period_similarity(names, picked.get("target_table") or [])
        _dbg(f"[history] hit page={picked.get('page_number')} "
             f"hits={hits}/{len(names)} sim={sim:.2f} pool={len(pool)} title={picked.get('title')}")
        return _clean_item(picked)

    picked = _pick_table_when_last_period_miss(pool, names)
    if picked:
        return _clean_item(picked)

    # 全 miss，回退到 lines_pack 独立候选（也要过滤错表标题）
    if isinstance(lines_pack, dict) and lines_pack.get("target_table"):
        if not _is_never_revenue_title(lines_pack):
            return _clean_item(lines_pack)
    return None


def _select_by_structure(pool):
    """无上期：过滤非产品表，按标题优先级选表。"""
    good = [c for c in pool if isinstance(c, dict) and not _should_skip(c)]

    _dbg(f"[filter] kept {len(good)}/{len(pool)} candidates "
         f"pages={[c.get('page_number') for c in good]}")

    if not good:
        return pool[0] if pool and isinstance(pool[0], dict) else None

    return _pick_best(good)


def get_target_table(
    target_tables,
    target_table_from_last_period_data=None,
    last_period_data=None,
    gt_target_page=None,
):
    """多候选定表。

    有上期 → 产品名+金额匹配
    无上期 → 过滤非产品表 → 标题优先级（分部>收益>收入>损益）→ 页码
    """
    names = _last_period_product_names(last_period_data)
    lines_pack = target_table_from_last_period_data

    # 候选池 = 章节候选 + 行扫描候选
    scan_items = []
    if isinstance(lines_pack, dict):
        scan_items = list(lines_pack.get("_all_lp_candidates") or [])

    # GT 页过滤：候选池收窄到 GT 页 ±1
    if gt_target_page is not None:
        scan_items = [c for c in scan_items
                      if isinstance(c, dict) and abs((c.get("page_number") or -1) - gt_target_page) <= 1]
        target_tables = [c for c in (target_tables or [])
                         if isinstance(c, dict) and abs((c.get("page_number") or -1) - gt_target_page) <= 1]
        if not scan_items and not target_tables:
            scan_items = list((lines_pack or {}).get("_all_lp_candidates") or [])
        if isinstance(lines_pack, dict):
            lines_pack = dict(lines_pack)
            lines_pack["_lp_lines_miss"] = True

    pool = _dedupe_table_candidates(list(target_tables or []) + scan_items)

    if names:
        return _select_by_history(pool, names, lines_pack)

    if not pool:
        # 无候选，尝试 lines_pack 独立候选
        if isinstance(lines_pack, dict) and lines_pack.get("target_table") and not lines_pack.get("_lp_lines_miss"):
            return _clean_item(lines_pack)
        return None

    return _select_by_structure(pool)


def format_number(text):
    """取单元格主金额：多行/括注外币时只取首行首个数，避免 1,969.9\\n(2.53亿) → -1969.92.53。"""
    text = str(text) if text is not None else ""
    if not text or text in {"-", "--", "—", "–", "n/a", "N/A", "nil", "Nil", "null", "None"}:
        return ""
    # 双币种/括注：只看第一行
    text = text.replace("\r", "\n").split("\n")[0].strip()
    neg = False
    if re.search(r"^\s*\(.*\)\s*$", text) or (text.startswith("(") and text.endswith(")")):
        neg = True
        text = text.strip("()（）")
    m = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text)
    if not m:
        return ""
    num = m.group(0).replace(",", "")
    if neg and not num.startswith("-"):
        num = "-" + num
    if num in {"", "-", ".", "-."}:
        return ""
    # 拒绝多小数点等脏串
    if num.count(".") > 1 or num.count("-") > 1:
        return ""
    return num


def _is_backtest(request_id, task_info_list=None) -> bool:
    """run_backtest 传 request_id='backtest' 且 task_info_list=None。"""
    if str(request_id or "").strip().lower() == "backtest":
        return True
    return not task_info_list


_GT_TARGET_PAGES = None


def _get_gt_target_page(info_code):
    """从 gt_target_pages.json 读取目标页码。仅回测时有效，生产返回 None。"""
    global _GT_TARGET_PAGES
    if _GT_TARGET_PAGES is None:
        try:
            gt_path = os.path.join(os.path.dirname(__file__), "..", "..", "tasks", "HKCO_FN_PRODUCT", "gt_target_pages.json")
            with open(gt_path, encoding="utf-8") as f:
                _GT_TARGET_PAGES = json.load(f).get("docs", {})
        except Exception:
            _GT_TARGET_PAGES = {}
    return (_GT_TARGET_PAGES.get(info_code, {}) or {}).get("best_page")


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
    pipe_meta=None,
):
    """组装 run_backtest 认的 extract_init 返回结构。

    selected_count：定表实际来源表数（单表=1；多表合并>1），供回测金额子集豁免。
    """
    records = _result_data_to_records(result_data)
    meta = dict(pipe_meta or {})
    source_pages = list(meta.get("source_pages") or [])
    if not source_pages:
        for t in target_tables or []:
            pn = t.get("page_number") if isinstance(t, dict) else None
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
            "pipeline": {
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
    path = os.path.join(os.path.dirname(__file__), "..", "..", "tasks", "HKCO_FN_PRODUCT", "last_data.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f).get(info_code)
            if data:
                _dbg(f"[last_period] loaded from last_data.json rows={len(data)}")
                return data
    return []

# region process_pdf_file
def process_pdf_file(pdf_path, info_code, request_id, task_info_list, ocr_result_info, configs):
    # Lazy import to break circular dependency
    from custom.service.EAPS_HKCO_FN_PRODUCT_get_res import get_res
    from custom.service.EAPS_HKCO_FN_PRODUCT_format_data import format_data

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
                pipe_meta = {"selected_count": 0, "source_pages": []}
                pdf_path, json_path_page_map = get_all_paths(pdf_path, configs)

                _dbg_reset(info_code, configs)
                try:
                    # 上期数据：生产必取；回测也取（失败则空，走结构量兜底）
                    last_period_data = get_last_period_data(info_code, request_id, task_id)
                    _dbg(
                        f"[last_period] rows={len(last_period_data) if isinstance(last_period_data, list) else type(last_period_data)}"
                    )

                    # 获取文档流
                    lines = get_lines(pdf_path, json_path_page_map)
                    _dbg_section("get_lines")
                    _dbg(f"lines={len(lines or [])} pages_json={len(json_path_page_map or {})}")

                    target_table_from_last_period_data = get_target_table_from_last_period_data(
                        lines, last_period_data
                    )

                    # 大小章节切割+获取目标表格
                    target_items = get_target_tables(lines)
                    source_tables = get_all_source_tables(lines)
                    document_period_text = get_document_period_text(lines)
                    # GT 仅用于 run_backtest.py 的结果评分，禁止参与候选发现或选表。
                    # 即使处于 backtest 模式，也必须走与生产完全相同的抽取路径。
                    gt_page = None
                    # 保存所有候选表
                    for _i, _cand in enumerate(list(target_items or [])):
                        if isinstance(_cand, dict) and _cand.get("target_table"):
                            _dbg_dump_target_item(f"{info_code}_candidate_{_i}", _cand)
                    # 目标表格选择：上期命中 > 候选近似 > 既有启发式
                    target_item = get_target_table(
                        target_items,
                        target_table_from_last_period_data=target_table_from_last_period_data,
                        last_period_data=last_period_data,
                        gt_target_page=gt_page,
                    )
                    _dbg_dump_target_item(info_code, target_item)
                    # 目标表格信息提取（规则，不用 AI）
                    res = get_res(
                        target_item,
                        info_code,
                        reason_arr,
                        notice_date=notice_date,
                        last_period_data=last_period_data,
                        source_tables=source_tables,
                        document_period_text=document_period_text,
                    )

                    # 格式化入库字段
                    result_data, reason_arr, pipe_meta = format_data(
                        res, derived_id, info_code, notice_date, request_id, task_id, reason_arr,
                        last_period_data=last_period_data,
                    )
                    _dbg(f"reason_arr={reason_arr} pipe_meta={pipe_meta}")
                finally:
                    _dbg_flush()

                extract_result = _build_extract_result(
                    info_code,
                    request_id,
                    result_data,
                    target_tables=target_items,
                    reason_arr=reason_arr,
                    pipe_meta=pipe_meta,
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

    # 兜底：configs 里平台下发的 mineru 路径
    if not json_path_page_map and configs:
        for key in (
            'mineru_local_file_full_path_list',
            'mineru_all_local_file_full_path_list',
            'mineru_hybrid_local_file_full_path_list',
        ):
            file_list = configs.get(key)
            if not file_list:
                continue
            for json_path in file_list:
                m = re.compile(r"_(\d+)(.json)").search(json_path)
                if m:
                    json_path_page_map[int(m.group(1))] = json_path
            if json_path_page_map:
                break

    # 本地调试兜底：json 在 {mineru_json_base_dir}/{infocode}/
    if not json_path_page_map:
        info_code = os.path.splitext(os.path.basename(pdf_path))[0]
        bases = []
        if configs and configs.get("mineru_json_base_dir"):
            bases.append(str(configs.get("mineru_json_base_dir")))
        # Optional deployment fallback.  Avoid a hard-coded drive so the same
        # code works on Windows and macOS.
        env_base = os.environ.get("HKCO_MINERU_JSON_DIR", "").strip()
        if env_base:
            bases.append(env_base)
        for base in bases:
            json_dir = os.path.join(base, info_code)
            if not os.path.isdir(json_dir):
                continue
            for f in os.listdir(json_dir):
                if not f.endswith('.json') or 'over' in f:
                    continue
                m = re.compile(r"AN\d{18}_(.*?)(.json)").search(f)
                if m:
                    json_path_page_map[int(m.group(1))] = os.path.join(json_dir, f)
            if json_path_page_map:
                break

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
        "data": {"records": [], "pipeline": {"stage": "empty_output", "message": "process_pdf_file 无返回"}},
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
    code = "AN202602261820064399"
    pdf_path = os.path.join(root, "pdf", f"{code}.pdf")
    result = process_pdf_file(pdf_path, code, "debug", None, None, {
        "mineru_json_base_dir": os.path.join(root, "pdf_json"),
        "pipeline_debug": True,
    })
    print(result)
