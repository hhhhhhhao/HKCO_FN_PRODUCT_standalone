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
    fullwidth_to_halfwidth,
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
PROFIT_LOSS_KEYWORDS = ["損益表", "损益表", "虧損表", "亏损表", "全面虧損表", "全面亏损表", "利潤表", "利润表", "全面收益表", "全面損益表", "全面损益表", "income statement", "statement of profit", "profit or loss", "profit and loss"]
COST_KEYWORDS = ["銷售成本", "销售成本", "營業成本", "营业成本", "收入成本", "服務成本", "服务成本", "cost of sales", "cost of revenue", "cost of services"]
GROSS_PROFIT_KEYWORDS = ["毛利", "毛利潤", "毛利润", "gross profit", "gross loss", "gross margin"]

# 地区/区域划分关键词 — 表格内出现这些内容说明按地理维度拆分，不应作为产品收入分布表
# 港股公告繁/简/混排均覆盖
_REGION_SPLIT_PATTERNS = [
    (r"按.{1,20}?(?:地區|地区|地域|區域|区域|所在地|地理|位置)\s*.{0,10}?\s*(?:劃分|划分|分類|分类|分部)", 0),
    (r"按.*(地區|地区)", 0),
    (r"(?:地區|地区)\s*分部", 0),
    (r"(按.*(地區|地区|地域|區域|区域|所在地).*劃分)", 0),
]

# 每一行是一个正则优先级（(pattern, group) 元组），从上到下依次匹配章节标题。
_TABLE_CLASS_PATTERNS = [
    [(r"分部收入及業績", 0)],
    [(r"經營分部|经营分部|收入及分部|分部資料|收益及業績|分部收入|營業額及業績|分部資料|分拆收入|分類.*資料", 0)],
    [(r"外部客戶收入|外部客户收入", 0)],
    [
        (r"按.*收入.*業績", 0),
        (r"產品收入|产品收入|服務收入|服务收入|收益、其他收入及收益|收入資料|收益資料|收益明細", 0),
        (r"收入構成|收入构成|收入分拆|收入明細|收入明细|收益及分部|類別分析|类别分析|收益分類|收入分類|收益分类|收入分类|銷售貨品|销售货品|按產品|按产品|按主要產品|按主要产品|收入、其他收入", 0),
        (r"^\d+\.收入$", 0),
        (r"^[\(|\（]*[、|\)|）|.|．|。|\)]*[一二三四五六七八九十0123456789①②③④⑤⑥⑦⑧⑨A-Da-d]+[、|\)|）|.||．|。|\)]*(收益|收入)$", 0),
    ],
    # [(r"收入|收益", 0)],
    [(r"收入、資本支出及實現價格", 0)],
    [(r"損益表|损益表|虧損表|亏损表|損益賬", 0)],
    [(r"收益淨額", 0)],
]


def _words_cache_path(pdf_path):
    from pathlib import Path

    pdf_name = os.path.basename(str(pdf_path))
    stem = os.path.splitext(pdf_name)[0]
    return Path(__file__).resolve().parents[2] / "pdf_json" / f"{stem}_page_words_cache.json"


def _load_words_cache(pdf_path):
    """读整页 extract_words 缓存；PDF 变化或缓存损坏时返回空。"""
    cache_path = _words_cache_path(pdf_path)
    if not cache_path.is_file():
        return {}
    try:
        stat = os.stat(str(pdf_path))
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if (
            payload.get("source") == os.path.basename(str(pdf_path))
            and payload.get("size") == stat.st_size
            and payload.get("mtime_ns") == stat.st_mtime_ns
            and isinstance(payload.get("pages"), dict)
        ):
            return {int(key): list(value) for key, value in payload["pages"].items()}
    except Exception:
        return {}
    return {}


def _save_words_cache(pdf_path, page_words):
    cache_path = _words_cache_path(pdf_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    stat = os.stat(str(pdf_path))
    payload = {
        "source": os.path.basename(str(pdf_path)),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "pages": {str(page_number): words for page_number, words in page_words.items()},
    }
    tmp_path = cache_path.with_name(cache_path.name + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(str(tmp_path), str(cache_path))


def get_inner_words(pdf, page_words, inner_lines):
    """按 inner_lines 的跨页范围取 words；每页 extract_words 只做一次并写缓存。"""
    if not inner_lines:
        return [], []

    lines_by_page = defaultdict(list)
    for line in inner_lines:
        page_number = line.get("page_number")
        if page_number is not None:
            lines_by_page[page_number].append(line)

    words = []
    for page_number, page_lines in sorted(lines_by_page.items()):
        if page_number < 1 or page_number > len(pdf.pages):
            continue
        page = pdf.pages[page_number - 1]

        crop_x0 = max(0.0, min(line["x0"] for line in page_lines))
        crop_y0 = max(0.0, min(line["top"] for line in page_lines))
        crop_x1 = min(page.width, max(line["x1"] for line in page_lines))
        crop_y1 = min(page.height, max(line["bottom"] for line in page_lines))

        if crop_y1 <= crop_y0 or crop_x1 <= crop_x0:
            continue

        if page_number not in page_words:
            page_words[page_number] = [dict(word) for word in page.extract_words()]

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
    return words, []

def is_table(inner_words_flatten):
    number_sum = 0
    for word in inner_words_flatten:
        if len(word['text']) >= 3 and is_number(word['text']) and not contains_chinese(word['text']):
            number_sum = number_sum + 1
    if number_sum > 3:
        return True
    return False

def select_main_table(pdf_path, lines_grouped, prior_names=()):
    """按章节顺序选择唯一主章节，并返回基础过滤后的相关章节。

    返回 (selected_inner_lines, related_inner_lines, from_full_history)
    - from_full_history: 选表来自 full_history（全部上期产品名命中），选表高度可信。
    """
    # 上期产品名直接去重。
    prior_names = list(dict.fromkeys(prior_names))
    # 合计项不参与历史产品命中。
    prior_names = [fullwidth_to_halfwidth(name) for name in prior_names if not any(keyword in name for keyword in ['合計', '合计', '總計', '总计', '總額', '总额', 'total','公司'])]

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
    page_words = _load_words_cache(pdf_path)
    with ExtendPlumber.open(pdf_path) as pdf:
        for inner_lines in lines_grouped:
            first_line = inner_lines[0]["text"]
            inner_lines_text = "/".join(line["text"] for line in inner_lines)

            if '下表載列二零二六財年的收益明細,連同二零二五財年的比較業績:' == first_line:
                print

            # 严格标题排除只检查章节第一行
            exlcude_words = (
                "分類資產及負債","財務數字", "财务数字","合同負債", "合同负债", "合約負債", "合约负债",
                "員工人數", "员工人数", "僱員人數", "雇员人数","銷量", "销量", "產量", "产量",
                "賬齡", "账龄","現金流量", "现金流量",'綜合全面收益表','財務摘要','财务摘要',
                '財務回顧','财务回顾','主要客户的資料','概不','比較數字','管理層','網絡','公佈','政府','股息','紅線',
                '季度比較', # AN202502271643556315 40
                '股本', # AN202602271820108796
            )
            if any( keyword in first_line for keyword in exlcude_words ):
                continue

            include_words =  (
                "收入", "收益", "分部", "資料", "經營", "業務", "產品", "服務",
                "銷售", "分類", "客户合約", "明細", "分拆", "類別", "營業額",
                "合同", "客户合同", "營收",'業績',
            )
            if not any(keyword in first_line for keyword in include_words):
                continue

            inner_words = get_inner_words(pdf, page_words, inner_lines)
            inner_words_flatten = flatten_arr(inner_words)

            # 地区/区域拆分排除 — 表格内容出现地理维度拆分关键词则跳过
            if match_patterns(first_line, _REGION_SPLIT_PATTERNS):
                continue

            if not is_table(inner_words_flatten):
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
            if prior_names and matched_count == len(prior_names):
                full_history.append(inner_lines)
            if prior_names and matched_count_arr == len(prior_names):
                full_history_arr.append(inner_lines)

    try:
        _save_words_cache(pdf_path, page_words)
    except Exception:
        pass

    if not related_inner_lines:
        return None, [], False

    # 只有一个全量历史命中章节时，直接选择。
    if len(full_history) == 1:
        return full_history[0], related_inner_lines, True

    # 只有一个全量历史命中章节时，直接选择。(table命中版)
    if len(full_history_arr) == 1:
        return full_history_arr[0], related_inner_lines, True

    # 有全量历史命中章节时只保留它们，否则降级到历史产品命中数最高的前两组。
    candidate_tables = []
    if full_history:
        candidate_tables.extend(full_history)
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

    # 仍然并列时，按正则优先级循环，找到第一个命中章节直接返回。
    if len(candidate_tables) > 1:
        for patterns in _TABLE_CLASS_PATTERNS:
            for inner_lines in candidate_tables:
                if match_patterns(inner_lines[0]["text"], patterns):
                    return inner_lines, related_inner_lines, _in_full_history(inner_lines)

    # 完全同分时不看页码或物理表 ID，保留最先出现的章节。
    return candidate_tables[0], related_inner_lines, _in_full_history(candidate_tables[0])
