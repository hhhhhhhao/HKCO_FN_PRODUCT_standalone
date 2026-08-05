# -*- coding: utf-8 -*-
"""HKCO_FN_PRODUCT 正式选表。

lines_grouped 中的每个 inner_lines 是一个完整章节单元，标题、正文、页码和
物理表始终一起参与判断。每个 inner_lines 只解析一次，再按历史产品、完整损益
和收入语义缩小章节范围，最终返回唯一主章节。

正式选表不使用 GT、当前期产品、GT 金额、页码排序或物理表 ID。
"""
import re
import unicodedata
from custom.service.HKCO_FN_PRODUCT_document import match_patterns


def fullwidth_to_halfwidth(s):
    s = s.replace('戶', '户')
    # 全角转半角
    return "".join((unicodedata.normalize("NFKC", char) if unicodedata.east_asian_width(char) in ["F", "W"] else char) for char in s)
    
def historical_product_name_matches(prior_names, tables):
    """返回 prior_names 中有多少个名字能命中 tables 表格文本。"""
    table_rows = []
    for item in (tables or []):
        table = item.get("table") if isinstance(item, dict) and "table" in item else item
        if isinstance(table, (list, tuple)):
            table_rows.extend(table)
    tables_flatten = [
        str(cell).replace(" ", "").replace("\\n", "").replace("\n", "")
        for row in table_rows
        for cell in row
    ]
    table_text = str(tables_flatten).strip().lower()

    # text匹配
    matched_count = 0
    for left in prior_names:
        left_key = left.strip().lower()
        orig_left_key = left_key
        if '-' in left_key:
            left_key = left_key.split('-')[-1]
        if ':' in left_key:
            left_key = left_key.split(':')[0]
        if not left_key or not table_text:
            continue
        if left_key in table_text:
            matched_count += 1
            continue
        # 长产品名：所有字符都出现在 table_text 中即算命中（不管顺序）
        if len(orig_left_key) > 10 and '-' in orig_left_key:
            if all(ch in table_text for ch in orig_left_key):
                matched_count += 1

    # arr匹配
    matched_count_arr = 0
    for left in prior_names:
        left_key = left.strip().lower()
        orig_left_key = left_key
        if '-' in left_key:
            left_key = left_key.split('-')[-1]
        if ':' in left_key:
            left_key = left_key.split(':')[0]

        if not left_key or not table_text:
            continue
        if left_key in tables_flatten:
            matched_count_arr += 1
            continue


    return matched_count,matched_count_arr


NUMBER = re.compile(r"^\s*\(?-?\d[\d,]*(?:\.\d+)?\)?\s*$")
REVENUE_KEYWORDS = ["收入", "收益", "營業額", "营业额", "銷售額", "销售额", "revenue", "turnover", "sales"]
PROFIT_LOSS_KEYWORDS = ["損益表", "损益表", "虧損表", "亏损表", "全面虧損表", "全面亏损表", "利潤表", "利润表", "全面收益表", "全面損益表", "全面损益表", "income statement", "statement of profit", "profit or loss", "profit and loss"]
COST_KEYWORDS = ["銷售成本", "销售成本", "營業成本", "营业成本", "收入成本", "服務成本", "服务成本", "cost of sales", "cost of revenue", "cost of services"]
GROSS_PROFIT_KEYWORDS = ["毛利", "毛利潤", "毛利润", "gross profit", "gross loss", "gross margin"]

# 地区/区域划分关键词 — 表格内出现这些内容说明按地理维度拆分，不应作为产品收入分布表
# 港股公告繁/简/混排均覆盖
_REGION_SPLIT_PATTERNS = [
    (r"按.{1,20}?(?:地區|地区|地域|區域|区域|所在地)\s*.{0,10}?\s*(?:劃分|划分|分類|分类|分部)", 0),
    (r"按.{1,15}?(?:經營|经营|營運|营运)\s*(?:地區|地区)", 0),
    (r"(?:地區|地区)\s*分部", 0),
    (r"(按.*(地區|地区|地域|區域|区域|所在地).*劃分)", 0),
]

# 每一行是一个正则优先级（(pattern, group) 元组），从上到下依次匹配章节标题。
_TABLE_CLASS_PATTERNS = [
    [(r"經營分部|经营分部|收入及分部|分部資料|收益及業績|分部收入", 0)],
    [(r"外部客戶收入|外部客户收入", 0)],
    [
        (r"按.*收入.*業績", 0),
        (r"產品收入|产品收入|服務收入|服务收入|收益、其他收入及收益", 0),
        (r"收入構成|收入构成|收入分拆|收入明細|收入明细|收益及分部|類別分析|类别分析|收益分類|收入分類|收益分类|收入分类|銷售貨品|销售货品|按產品|按产品|按主要產品|按主要产品", 0),
        (r"^\d+\.收入$", 0),
    ],
    # [(r"收入|收益", 0)],
    [(r"收入、資本支出及實現價格", 0)],
    [(r"損益表|损益表|虧損表|亏损表|損益賬", 0)],
]

def _rows(table):
    """读取原始 table line 中的二维表格。"""
    return [list(row) for row in table.get("table", []) if isinstance(row, (list, tuple))]


def _has_numbers(tables):
    """判断章节中的表格是否至少包含一个数字单元格。"""
    for table in tables:
        for row in _rows(table):
            for cell in row:
                if NUMBER.match(cell):
                    return True
    return False


def select_main_table(lines_grouped, prior_names=()):
    """按章节顺序选择唯一主章节，并返回基础过滤后的相关章节。"""
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

    # 每个 inner_lines 只解析一次；标题、正文、页码和物理表始终属于同一组。
    for inner_lines in lines_grouped:
        first_line = inner_lines[0]["text"]
        inner_lines_text = "/".join(line["text"] for line in inner_lines)

        if '6.營業收入/營業成本(續)' == first_line:
            print

        # 严格标题排除只检查章节第一行，不使用正文或表格内容触发排除。
        # '綜合全面收益表' AN202501201642376981 8
        # 淨額 净额 AN202503211645567140
        # AN202604301821868292 8
        exlcude_words = ( "分類資產及負債","財務數字", "财务数字","合同負債", "合同负债", "合約負債", "合约负债","員工人數", "员工人数", "僱員人數", "雇员人数","銷量", "销量", "產量", "产量","賬齡", "账龄","現金流量", "现金流量",'綜合全面收益表','財務摘要','财务摘要','財務回顧','财务回顾','主要客户的資料')
        if any( keyword in first_line for keyword in exlcude_words ):
            debug_a = next((keyword for keyword in exlcude_words if keyword in first_line), None)
            continue

        # 基础候选必须含有非空，并且至少存在一个数字单元格。
        tables = [line for line in inner_lines if line.get("is_table") and _rows(line)]
        if not tables:
            continue

        # 地区/区域拆分排除 — 表格内容出现地理维度拆分关键词则跳过
        if match_patterns(first_line, _REGION_SPLIT_PATTERNS):
            continue
        if not _has_numbers(tables):
            continue

        related_inner_lines.append(inner_lines)

        # 历史产品名匹配
        # 全部上期产品命中才进入 full_history
        matched_count, matched_count_arr = historical_product_name_matches(prior_names, tables)
        history_groups[matched_count].append(inner_lines)
        if prior_names and matched_count == len(prior_names):
            full_history.append(inner_lines)
        if prior_names and matched_count_arr == len(prior_names):
            full_history_arr.append(inner_lines)

    if not related_inner_lines:
        return None, []

    # 只有一个全量历史命中章节时，直接选择。
    if len(full_history) == 1:
        return full_history[0], related_inner_lines

    # 只有一个全量历史命中章节时，直接选择。(table命中版)
    if len(full_history_arr) == 1:
        return full_history_arr[0], related_inner_lines

    # 有全量历史命中章节时只保留它们，否则降级到历史产品命中数最高的章节。
    candidate_tables = []
    if full_history:
        candidate_tables.extend(full_history)
    else:
        for hit_count in range(len(prior_names), -1, -1):
            if history_groups[hit_count]:
                candidate_tables.extend(history_groups[hit_count])
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
                return inner_lines, related_inner_lines

    # 仍然并列时，按正则优先级循环，找到第一个命中章节直接返回。
    if len(candidate_tables) > 1:
        for patterns in _TABLE_CLASS_PATTERNS:
            for inner_lines in candidate_tables:
                if match_patterns(inner_lines[0]["text"], patterns):
                    return inner_lines, related_inner_lines

    # 完全同分时不看页码或物理表 ID，保留最先出现的章节。
    return candidate_tables[0], related_inner_lines
