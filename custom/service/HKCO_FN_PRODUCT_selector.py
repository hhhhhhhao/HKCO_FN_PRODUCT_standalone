# -*- coding: utf-8 -*-
"""HKCO_FN_PRODUCT 正式选表。

lines_grouped 中的每个 inner_lines 是一个完整章节单元，标题、正文、页码和
物理表始终一起参与判断。每个 inner_lines 只解析一次，再按历史产品、完整损益
和收入语义缩小章节范围，最终返回唯一主章节。

正式选表不使用 GT、当前期产品、GT 金额、页码排序或物理表 ID。
"""
import re

def historical_product_name_matches(left, right):
    """判断去除首尾空白并统一小写后的上期产品名能否在章节文本中命中。"""
    left_key = str(left or "").strip().lower()
    right_key = str(right or "").strip().lower()
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    shorter, longer = sorted((left_key, right_key), key=len)
    if len(shorter) <= 4:
        return False
    longer_chars = iter(longer)
    return all(any(char == candidate for candidate in longer_chars) for char in shorter)


NUMBER = re.compile(r"^\s*\(?-?\d[\d,]*(?:\.\d+)?\)?\s*$")
REVENUE_KEYWORDS = ["收入", "收益", "營業額", "营业额", "銷售額", "销售额", "revenue", "turnover", "sales"]
PROFIT_LOSS_KEYWORDS = ["損益表", "损益表", "虧損表", "亏损表", "全面虧損表", "全面亏损表", "利潤表", "利润表", "全面收益表", "全面損益表", "全面损益表", "income statement", "statement of profit", "profit or loss", "profit and loss"]
COST_KEYWORDS = ["銷售成本", "销售成本", "營業成本", "营业成本", "收入成本", "服務成本", "服务成本", "cost of sales", "cost of revenue", "cost of services"]
GROSS_PROFIT_KEYWORDS = ["毛利", "毛利潤", "毛利润", "gross profit", "gross loss", "gross margin"]

# 每一行是一个关键词优先级，从上到下依次匹配。
TABLE_KEYWORDS = [
    ["經營分部", "经营分部"],
    ["外部客戶收入", "外部客户收入"],
    ["產品收入", "产品收入", "服務收入", "服务收入"],
    ["收入構成", "收入构成", "收入分拆", "收入明細", "收入明细"],
    ["收入", "收益"],
    ["損益表", "损益表", "虧損表", "亏损表"],
]

def _rows(table):
    """读取原始 table line 中的二维表格。"""
    return [list(row) for row in table.get("table", []) if isinstance(row, (list, tuple))]


def select_main_table(lines_grouped, prior_names=()):
    """按章节顺序选择唯一主章节，并返回基础过滤后的相关章节。"""
    # 上期产品名直接去重。
    prior_names = list(set(prior_names))
    # 合计项不参与历史产品命中。
    prior_names = [name for name in prior_names if not any(keyword in name for keyword in ['合計', '合计', '總計', '总计', '總額', '总额', 'total'])]

    # 下标就是命中的上期产品数量。
    history_groups = [[] for _ in range(len(prior_names) + 1)]
    full_history = []

    # 相关章节整体保留，供后续读取表格、单位和币种。
    related_inner_lines = []

    # 每个 inner_lines 只解析一次；标题、正文、页码和物理表始终属于同一组。
    for inner_lines in lines_grouped:
        first_line = inner_lines[0]["text"]
        inner_lines_text = "/".join(line["text"] for line in inner_lines)
        page_number = inner_lines[0]["page_number"]

        # 严格标题排除只检查章节第一行，不使用正文或表格内容触发排除。
        if any( keyword in first_line for keyword in ( "分類資產及負債","財務數字", "财务数字","合同負債", "合同负债", "合約負債", "合约负债","資產負債", "资产负债","員工人數", "员工人数", "僱員人數", "雇员人数","銷量", "销量", "產量", "产量","賬齡", "账龄","現金流量", "现金流量","淨額", "净额",)):
            continue

        # 基础候选必须含有非空，并且至少存在一个数字单元格。
        tables = [line for line in inner_lines if line.get("is_table") and _rows(line)]
        if not tables:
            continue
        if not any(NUMBER.match(cell) for table in tables for row in _rows(table) for cell in row):
            continue

        # 将所属章节信息写回 table line，后续抽取无需重新查找标题和页码。
        for table in tables:
            table["section_title"] = first_line
            table["section_text"] = inner_lines_text
            table["section_page_number"] = page_number
        related_inner_lines.append(inner_lines)

        # 历史产品名匹配
        # 全部上期产品命中才进入 full_history
        matched_count = sum(historical_product_name_matches(prior_name, inner_lines_text) for prior_name in prior_names)
        history_groups[matched_count].append(inner_lines)
        if prior_names and matched_count == len(prior_names):
            full_history.append(inner_lines)

    if not related_inner_lines:
        return None, []

    # 只有一个全量历史命中章节时，直接选择。
    if len(full_history) == 1:
        return full_history[0], related_inner_lines

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
            if (
                any(keyword in inner_lines_text for keyword in PROFIT_LOSS_KEYWORDS)
                and any(keyword in inner_lines_text for keyword in REVENUE_KEYWORDS)
                and any(keyword in inner_lines_text for keyword in COST_KEYWORDS)
                and any(keyword in inner_lines_text for keyword in GROSS_PROFIT_KEYWORDS)
            ):
                return inner_lines, related_inner_lines

    # 仍然并列时，按关键词优先级循环，找到第一个命中章节直接返回。
    if len(candidate_tables) > 1:
        for keywords in TABLE_KEYWORDS:
            for inner_lines in candidate_tables:
                inner_lines_text = "/".join(line["text"] for line in inner_lines)
                if any(keyword in inner_lines_text for keyword in keywords):
                    return inner_lines, related_inner_lines

    # 完全同分时不看页码或物理表 ID，保留最先出现的章节。
    return candidate_tables[0], related_inner_lines
