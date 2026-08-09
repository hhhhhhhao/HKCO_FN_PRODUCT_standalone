# -*- coding: utf-8 -*-
"""HKCO_FN_PRODUCT 正式选表。

lines_grouped 中的每个 inner_lines 是一个完整章节单元，标题、正文、页码和
物理表始终一起参与判断。每个 inner_lines 只解析一次，再按历史产品、完整损益
和收入语义缩小章节范围，最终返回唯一主章节。

正式选表不使用 GT、当前期产品、GT 金额、页码排序或物理表 ID。
"""
import json
import os
import re
from custom.extend.pdfplumber_extend_object import ExtendPlumber
from custom.service.HKCO_FN_PRODUCT_document import match_patterns
from custom.service.HKCO_FN_PRODUCT_utils import (
    contains_chinese,
    flatten_arr,
    format_prior_names,
    historical_product_last_name_matches,
    is_number,
)
from custom.utils.pdfplumber_extend_util import (
    clear_rect_edges,
    generate_extend_plumber_page,
)
from collections import defaultdict

NUMBER = re.compile(r"^\s*\(?-?\d[\d,]*(?:\.\d+)?\)?\s*$")
REVENUE_KEYWORDS = ["收入", "收益", "營業額", "营业额", "銷售額", "销售额", "revenue", "turnover", "sales"]
PROFIT_LOSS_KEYWORDS = ["損益", "损益", "虧損", "亏损", "全面虧損表", "全面亏损表", "全面損益表", "全面损益表",  "profit or loss", "profit and loss"]
COST_KEYWORDS = ["銷售成本", "销售成本", "營業成本", "营业成本", "收入成本", "服務成本", "服务成本", "cost of sales", "cost of revenue", "cost of services"]
GROSS_PROFIT_KEYWORDS = ["毛利", "毛利潤", "毛利润", "gross profit", "gross loss", "gross margin"]

# 地区/区域划分关键词 — 表格内出现这些内容说明按地理维度拆分，不应作为产品收入分布表
# 港股公告繁/简/混排均覆盖
_REGION_SPLIT_PATTERNS = [
    (r"按.{1,20}?(?:地區|地区|地域|區域|区域|所在地|地理|位置)\s*.{0,10}?\s*(?:劃分|划分|分類|分类|分部)", 0),
    (r"按.*(地區|地区)", 0),
    (r"(?:地區|地区)\s*分部", 0),
    (r"(按.*(地區|地区|地域|區域|区域|所在地).*劃分)", 0),
    (r"^(地區資料)$", 0),
]

# 每一行是一个正则优先级（(pattern, group) 元组），从上到下依次匹配章节标题。
_TABLE_CLASS_PATTERNS = [
    [(r"分部.*業績|收益及業績|業務分部|分拆.*(收入|收益)|收入明細|收益明細|收入分類|收益分類|收入構成|收益構成|分部業績|財務回顧|分部.*經營|reportableandoperatingsegment|客户合約收益|客戶合約收益|客户合約收入|客戶的合約.*收益", 0)],
    [(r"經營分部|经营分部|收入及分部|分部資料|分部報告|分部信息|分部报告|收益及業績|分部收入|營業額及業績|分部資料|分部資料|分拆收入|分類.*資料|業務單位|业务单位|分部.*如下|分部收入", 0)],
    [(r"外部客戶收入|外部客户收入|收入、其他收益|^收入:$", 0)],
    
    [
        (r"按.*收入.*業績", 0),
        (r"按服務|按服务|按類別|按类别", 0),
        (r"產品收入|产品收入|服務收入|服务收入|收益、其他收入及收益|收入資料|收益資料|收益明細", 0),
        (r"收入構成|收入构成|收入分拆|收入明細|收入明细|收益及分部|類別分析|类别分析|收益分類|收入分類|收益分类|收益及|收入分类|銷售貨品|销售货品|按產品|按产品|按主要產品|按主要产品|按.*劃分|按.*划分|收入、其他收入", 0),
        (r"收益分析|收入分析", 0),
        (r"^\d+\.收入$", 0),
        (r"^[\(|\（]*[、|\)|）|.|．|。|\)]*[一二三四五六七八九十0123456789①②③④⑤⑥⑦⑧⑨A-Da-d]+[、|\)|）|.||．|。|\)]*(收益|收入)$", 0),
    ],
    # [(r"收入|收益", 0)],
    [(r"收益淨額", 0)],
    [(r"收入、資本支出及實現價格|客户合約收入", 0)],
    [(r"按業務類型分類", 0)],
    [(r"損益表|损益表|收益表|虧損表|亏损表|損益賬|損益.*表", 0)],
    # (r"附註|附注", 0)],
]


def _words_cache_path(pdf_path):
    from pathlib import Path

    pdf_name = os.path.basename(str(pdf_path))
    stem = os.path.splitext(pdf_name)[0]
    return Path(__file__).resolve().parents[2] / "pdf_json" / f"{stem}_page_words_cache.json"


def _load_words_cache(pdf_path):
    """读整页 extract_words 缓存；PDF 变化或缓存损坏时返回空。

    Returns (page_words, page_dims, page_count) — page_dims/page_count 可能为空。
    page_words: {page_number: [word_dict, ...]}
    page_dims:  {page_number: [width, height]}
    """
    cache_path = _words_cache_path(pdf_path)
    if not cache_path.is_file():
        return {}, {}, 0
    try:
        stat = os.stat(str(pdf_path))
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            payload.get("source") == os.path.basename(str(pdf_path))
            and payload.get("size") == stat.st_size
            and payload.get("mtime_ns") == stat.st_mtime_ns
            and isinstance(payload.get("pages"), dict)
        ):
            pages_raw = payload["pages"]
            page_words = {}
            page_dims = {}
            for key, value in pages_raw.items():
                page_num = int(key)
                if isinstance(value, dict) and "words" in value:
                    # 新格式：{width, height, words}
                    page_words[page_num] = list(value["words"])
                    page_dims[page_num] = [value["width"], value["height"]]
                elif isinstance(value, list):
                    # 旧格式（仅 words 列表，无尺寸）— 首次命中后会自动升级
                    page_words[page_num] = list(value)
            page_count = payload.get("page_count", 0)
            return page_words, page_dims, page_count
    except Exception:
        return {}, {}, 0
    return {}, {}, 0


def _save_words_cache(pdf_path, page_words, page_dims=None, page_count=0):
    cache_path = _words_cache_path(pdf_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    stat = os.stat(str(pdf_path))
    pages_payload = {}
    for page_number, words in page_words.items():
        dims = (page_dims or {}).get(page_number)
        if dims:
            pages_payload[str(page_number)] = {
                "width": dims[0],
                "height": dims[1],
                "words": words,
            }
        else:
            # 无尺寸时退化为旧格式列表，保证兼容
            pages_payload[str(page_number)] = words
    payload = {
        "source": os.path.basename(str(pdf_path)),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "page_count": page_count,
        "pages": pages_payload,
    }
    tmp_path = cache_path.with_name(cache_path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(str(tmp_path), str(cache_path))


def get_inner_words(pdf, page_words, inner_lines, page_dims=None, page_count=0):
    """按 inner_lines 的跨页范围取 words；每页 extract_words 只做一次并写缓存。

    当 page_dims/page_count 提供且所有页均已缓存时，不需要 pdf 对象；
    否则通过 pdf.pages 补提取缺失页的 words 和尺寸。
    返回 (words, missing_pages) — missing_pages 是需要补提取的页码集合。
    """
    if not inner_lines:
        return [], set()

    lines_by_page = defaultdict(list)
    for line in inner_lines:
        page_number = line.get("page_number")
        if page_number is not None:
            lines_by_page[page_number].append(line)

    total_pages = page_count or (len(pdf.pages) if pdf else 0)
    words = []
    missing_pages = set()

    for page_number, page_lines in sorted(lines_by_page.items()):
        if page_number < 1 or page_number > total_pages:
            continue

        # 获取页面尺寸：优先用缓存，否则从 pdf 对象取
        dims = (page_dims or {}).get(page_number)
        if dims:
            page_w, page_h = dims[0], dims[1]
        elif pdf is not None:
            page = pdf.pages[page_number - 1]
            page_w, page_h = page.width, page.height
        else:
            missing_pages.add(page_number)
            continue

        crop_x0 = max(0.0, min(line["x0"] for line in page_lines))
        crop_y0 = max(0.0, min(line["top"] for line in page_lines))
        crop_x1 = min(page_w, max(line["x1"] for line in page_lines))
        crop_y1 = min(page_h, max(line["bottom"] for line in page_lines))

        if crop_y1 <= crop_y0 or crop_x1 <= crop_x0:
            continue

        if page_number not in page_words:
            if pdf is not None:
                page = pdf.pages[page_number - 1]
                page_words[page_number] = [dict(word) for word in page.extract_words()]
            else:
                missing_pages.add(page_number)
                continue

        words.extend(
            word
            for word in page_words[page_number]
            if (
                word["x0"] >= crop_x0 - 1e-6
                and word["x1"] <= crop_x1 + 1e-6
                and word["top"] >= crop_y0 - 1e-6
                and word["bottom"] <= crop_y1 + 1e-6
            )
        )
    return words, missing_pages

def is_table(inner_words_flatten, inner_lines, inner_lines_text):
    number_sum = 0
    for word in inner_words_flatten:
        word['text'] = re.sub(r"[\(\（]\d[\)\）]", "", word['text'])
        if len(word['text']) >= 3 and is_number(word['text']) and not contains_chinese(word['text']):
            number_sum = number_sum + 1
    if number_sum > 3:
        return True

    if any(keyword for keyword in ['千港元','万港元'] if keyword in inner_lines_text):
        return True

    # fallback: 附註等章节文字多但内嵌表格，words 提取不到足够数字
    # if inner_lines:
    #     text = ' '.join(str(l.get('text') or '') for l in inner_lines)
    #     amounts = re.findall(r'\d{1,3}(?:,\d{3})+|\d{5,}', text)
    #    if len(amounts) >= 5:
    #         return True
    return False

def select_main_table(pdf_path, lines_grouped, prior_names=()):
    """按章节顺序选择唯一主章节，并返回基础过滤后的相关章节。

    返回 (selected_inner_lines, related_inner_lines, from_full_history)
    - from_full_history: 选表来自 full_history（全部上期产品名命中），选表高度可信。
    """
    prior_names = format_prior_names(prior_names)

    # 下标就是命中的上期产品数量。
    history_groups = [[] for _ in range(len(prior_names) + 1)]
    full_history = []
    full_history_arr = []

    # 相关章节整体保留，供后续读取表格、单位和币种。
    related_inner_lines = []

    def _in_full_history(inner):
        """选中的 inner_lines 是否来自 full_history 或 full_history_arr（对象同一性检查）。"""
        return any(inner is fh for fh in full_history) or any(inner is fh for fh in full_history_arr)

    # 每个 inner_lines 只解析一次；标题、正文、页码和物理表始终属于同一组。
    page_words, page_dims, page_count = _load_words_cache(pdf_path)
    _all_pages_cached = bool(page_dims and page_count and page_count > 0)

    # 先尝试不带 PDF 跑一轮，收集缺失页；缓存完整则完全不需要打开 PDF。
    _pdf = None
    try:
        if not _all_pages_cached:
            _pdf = ExtendPlumber.open(pdf_path).__enter__()
            page_count = len(_pdf.pages)

        for inner_lines in lines_grouped:
            first_line = inner_lines[0]["text"]
            inner_lines_text = "/".join(line["text"] for line in inner_lines)

            if '收入:' == first_line:
                print

            # 严格标题排除只检查章节第一行
            exlcude_words = (
                "分類資產及負債","財務數字", "财务数字","合同負債", "合同负债", "合約負債", "合约负债",
                "員工人數", "员工人数", "僱員人數", "雇员人数","銷量", "销量", "產量", "产量",
                "賬齡", "账龄","現金流量", "现金流量",'財務摘要','财务摘要',
                '主要客户的資料','概不','比較數字','網絡','公佈','政府','股息','紅線','性別','地區劃分','股權','僱傭','年齡','每股收益','變現',
                '季度比較', # AN202502271643556315 40
                '股本', # AN202602271820108796
                '註釋', '壞賬', # AN202504031650851715
                '業務回顧', # AN202603121820526849
                '分部資產及負債',
                '基準', # AN202503281648794423
                '虧損', # AN202603301820876262
            )
            if any(keyword in first_line for keyword in exlcude_words):
                continue
            if any(keyword in first_line for keyword in ['資產負債表','资产负债表','資產及負債']) and not (
                '分部' in first_line or '分部' in inner_lines_text
            ):
                continue
            if ('財務回顧' in first_line or '财务回顾' in first_line) and not any(
                keyword in inner_lines_text
                for keyword in ('收入', '收益', '分部', '產品', '服務', '銷售')
            ):
                continue

            include_words =  (
                "收入", "收益", "分部", "資料", "經營", "業務", "產品", "服務",
                "銷售", "分類", "客户合約", "明細", "分拆", "類別", "營業額",
                "合同", "客户合同", "營收", "業績",
                "利潤", "利润", "營運", "营运", "虧損", "亏损", "虧損表", "损益表", "損益表",
                "利潤表", "利润表", "營運報表", "营运报表",
                "附註", "附注",  # 产品收入表常在财务报表附注里
                # "管理層討論", "管理层讨论",  # 管理层讨论章节常有产品收入汇总
                "劃分", "划分",  # 按產品劃分/按業務劃分等
            )
            if not any(keyword in first_line for keyword in include_words):
                continue

            inner_words, missing_pages = get_inner_words(
                _pdf, page_words, inner_lines, page_dims, page_count,
            )
            # 缓存完整时不会有缺失页；如有说明缓存过期需补打开 PDF
            if missing_pages and _pdf is None:
                _pdf = ExtendPlumber.open(pdf_path).__enter__()
                page_count = len(_pdf.pages)
                # 补全缺失页的尺寸
                if page_dims is None:
                    page_dims = {}
                for pn in missing_pages:
                    if pn not in page_dims:
                        p = _pdf.pages[pn - 1]
                        page_dims[pn] = [p.width, p.height]
                inner_words, _ = get_inner_words(
                    _pdf, page_words, inner_lines, page_dims, page_count,
                )
            inner_words_flatten = flatten_arr(inner_words)

            # 地区/区域拆分排除 — 表格内容出现地理维度拆分关键词则跳过
            if match_patterns(first_line, _REGION_SPLIT_PATTERNS):
                continue

            if not is_table(inner_words_flatten, inner_lines, inner_lines_text):
                continue


            related_inner_lines.append(inner_lines)

            # 历史产品名匹配
            # 全部上期产品命中才进入 full_history
            matched_count, matched_count_arr, blur_matched_count = historical_product_last_name_matches(
                prior_names,
                inner_words_flatten,
            )
            max_matched_count = max(matched_count,matched_count_arr,blur_matched_count,0)
            history_groups[max_matched_count].append(inner_lines)
            if prior_names and max_matched_count == len(prior_names):
                full_history.append(inner_lines)
            if prior_names and matched_count_arr == len(prior_names):
                full_history_arr.append(inner_lines)
    finally:
        # 保存缓存并关闭 PDF
        try:
            if _pdf is not None and not page_dims:
                page_dims = {}
                for pn in range(1, page_count + 1):
                    p = _pdf.pages[pn - 1]
                    page_dims[pn] = [p.width, p.height]
            _save_words_cache(pdf_path, page_words, page_dims, page_count)
        except Exception:
            pass
        if _pdf is not None:
            try:
                _pdf.__exit__(None, None, None)
            except Exception:
                pass
            _pdf = None

    if not related_inner_lines:
        return None, [], False

    # 只有一个全量历史命中章节时，直接选择。
    if len(full_history) == 1:
        return full_history[0], related_inner_lines, True

    # 只有一个全量历史命中章节时，直接选择。(table命中版)
    # if len(full_history_arr) == 1:
    #     return full_history_arr[0], related_inner_lines, True

    # 有全量历史命中章节时只保留它们，否则降级到历史产品命中数最高的前两组。
    candidate_tables = []
    if full_history:
        candidate_tables.extend(full_history)
        # if len(prior_names) - 1 >= 0:
        #     candidate_tables.extend(history_groups[len(prior_names) - 1])
    else:
        collected_groups = 0
        for hit_count in range(len(prior_names), -1, -1):
            if history_groups[hit_count]:
                candidate_tables.extend(history_groups[hit_count])
                collected_groups += 1
                if collected_groups >= 1:
                    break

    # 只有多张全量历史命中章节并列时，才检查完整损益。
    if len(full_history) > 1:
        for inner_lines in full_history:
            inner_lines_text = "/".join(line["text"] for line in inner_lines)
            first_line = inner_lines[0]["text"]
            if (
                any(keyword in first_line for keyword in PROFIT_LOSS_KEYWORDS)
                and any(keyword in inner_lines_text for keyword in REVENUE_KEYWORDS)
                and any(keyword in inner_lines_text for keyword in COST_KEYWORDS)
                and any(keyword in inner_lines_text for keyword in GROSS_PROFIT_KEYWORDS)
            ):
                return inner_lines, related_inner_lines, True

    # prior_names 只有一个时，优先选首行含「损益」的章节（损益表 > 附注等其他章节）。
    if len(prior_names) == 1 and len(candidate_tables) > 1:
        for inner_lines in related_inner_lines:
            first_line = inner_lines[0]["text"]
            if any(keyword in first_line for keyword in PROFIT_LOSS_KEYWORDS):
                return inner_lines, related_inner_lines, _in_full_history(inner_lines)

    # 仍然并列时，按正则优先级循环，找到第一个命中章节直接返回。
    if len(candidate_tables) > 1:
        for patterns in _TABLE_CLASS_PATTERNS:
            for inner_lines in candidate_tables:
                if match_patterns(inner_lines[0]["text"], patterns):
                    return inner_lines, related_inner_lines, _in_full_history(inner_lines)

    # 完全同分且没有表型 pattern 命中时，优先选更小的表格章节，避免选到整段业务回顾/附注。
    # if len(candidate_tables) > 1:
    #     best = min(
    #         candidate_tables,
    #         key=lambda group: (
    #             len({line.get("page_number") for line in group}),
    #             len(group),
    #         ),
    #     )
    #     return best, related_inner_lines, _in_full_history(best)

    return candidate_tables[0], related_inner_lines, _in_full_history(candidate_tables[0])
