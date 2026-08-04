# -*- coding: utf-8 -*-
"""HKCO_FN_PRODUCT 正式选表规则。

完整顺序：
1. 每张物理表独立参加选择；只硬排除空表和没有数字的表。
2. 只用完整章节标题或完整表标题排除明确的非目标表；不扫描表头或正文。
3. 将表内全部单元格与上期产品名逐个比较；全部产品名都命中才算历史产品命中。
4. 存在全量历史命中表时，只保留这些表；一张都没有时才降级为命中数量最多。
5. 当前候选只有一张时直接选中，不再检查表族或页码。
6. 全量历史命中候选有多张时，若其中有同时披露收入、成本、毛利的亏损表/损益表，直接优先。
7. 仍有多张并列时，按现有完整收入语义、语义位置、表族选择。
8. 如果严格标题排除会删除全部基础候选，则恢复全部基础候选，禁止选空。
9. 不再使用页码排序；最终分数完全相同时，保留文档中先出现的物理表。

选表禁止使用当前期 GT、金额、行列结构和页码。
"""
import re
import unicodedata


_TRADITIONAL = "臺裏裡為於與業務產銷售開發網據聯車醫藥護兒電纜風險資產物項類體國華萬億圓號總計額營運綜合損益潤"
_SIMPLIFIED = "台里里为于与业务产销售开发网据联车医药护儿电缆风险资产物项类体国华万亿圆号总计额营运综合损益润"
_TRANSLATION = str.maketrans(_TRADITIONAL, _SIMPLIFIED)


def identity_key(value):
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_TRANSLATION).lower()
    text = re.sub(
        r"\([^)]*(?:附注|note|[ivx\d]+)[^)]*\)|"
        r"（[^）]*(?:附注|note|[ivx\d]+)[^）]*）",
        "",
        text,
        flags=re.I,
    )
    return re.sub(r"[\s:：,，。;；()（）\[\]【】、/\\_\-–—]+", "", text)


def historical_product_name_matches(left, right):
    left_key, right_key = identity_key(left), identity_key(right)
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
REVENUE = re.compile(
    r"收入|收益|營業額|营业额|銷售額|销售额|revenue|turnover|sales",
    re.I,
)
SEGMENT = re.compile(
    r"經營分部|经营分部|業務分部|业务分部|可呈報分部|可报告分部|segment",
    re.I,
)
PRODUCT_SERVICE = re.compile(
    r"產品|产品|商品|貨品|货品|服務|服务|product|goods|service",
    re.I,
)
TYPE_SPLIT = re.compile(
    r"按.{0,12}(?:劃分|划分|分類|分类|類型|类型)|"
    r"(?:產品|产品|商品|貨品|货品|服務|服务).{0,12}(?:類型|类型|分類|分类)|"
    r"type of (?:goods|products?|services?)",
    re.I,
)
BREAKDOWN = re.compile(
    r"構成|构成|分拆|明細|明细|分類|分类|分析|breakdown|disaggregat|detail",
    re.I,
)
EXTERNAL_CUSTOMER_REVENUE = re.compile(
    r"(?:外部|外界).{0,8}(?:客戶|客户).{0,12}(?:收入|收益)|"
    r"(?:收入|收益).{0,12}(?:外部|外界).{0,8}(?:客戶|客户)|"
    r"external.{0,12}customer.{0,12}(?:revenue|turnover|sales)",
    re.I,
)
PROFIT_LOSS = re.compile(
    r"損益表|损益表|虧損表|亏损表|全面虧損表|全面亏损表|"
    r"利潤表|利润表|全面收益表|全面損益表|全面损益表|income statement|"
    r"statement of profit|profit (?:or|and) loss",
    re.I,
)
COST = re.compile(
    r"銷售成本|销售成本|營業成本|营业成本|收入成本|服務成本|服务成本|"
    r"cost of sales|cost of revenue|cost of services",
    re.I,
)
GROSS_PROFIT = re.compile(
    r"毛利|毛利潤|毛利润|gross profit|gross loss|gross margin",
    re.I,
)

# 严格标题排除：所有表达式都匹配清理编号后的完整标题，不扫描表头或正文。
STRICT_TITLE_EXCLUSIONS = (
    (
        "assets_or_liabilities_table",
        re.compile(
            r"^(?:(?:分類|分类|分部|可呈報分部|可报告分部|segment)\s*)?"
            r"(?:資產|资产)(?:\s*(?:及|和|與|与)\s*(?:負債|负债))?"
            r"(?:\s*(?:分析|明細|明细|資料|资料))?$|"
            r"^(?:(?:分類|分类|分部|segment)\s*)?(?:負債|负债)"
            r"(?:\s*(?:分析|明細|明细|資料|资料))?$|"
            r"^(?:segment\s+)?assets?(?:\s+and\s+liabilit(?:y|ies))?$",
            re.I,
        ),
    ),
    (
        "employee_headcount_table",
        re.compile(
            r"^(?:員工|员工|僱員|雇员)(?:人數|人数)(?:分析|明細|明细)?$|"
            r"^(?:employee headcount|number of employees)$",
            re.I,
        ),
    ),
    (
        "sales_or_production_volume_table",
        re.compile(
            r"^(?:銷量|销量|銷售數量|销售数量|產量|产量)(?:分析|明細|明细)?$|"
            r"^(?:sales volume|production volume)$",
            re.I,
        ),
    ),
    (
        "receivable_aging_table",
        re.compile(
            r"^(?:應收|应收)(?:賬款|账款)?(?:賬齡|账龄)(?:分析)?$|"
            r"^(?:賬齡|账龄)(?:分析)?$|"
            r"^(?:receivable )?(?:ageing|aging)(?: analysis)?$",
            re.I,
        ),
    ),
    (
        "cash_flow_table",
        re.compile(
            r"^(?:綜合|综合|合併|合并|簡明|简明|未經審核|未经审核)*"
            r"(?:現金流量表|现金流量表)$|^(?:statement of )?cash flows?$",
            re.I,
        ),
    ),
)

TOTAL_KEYS = {
    identity_key(value)
    for value in ("合計", "合计", "總計", "总计", "總額", "总额", "total")
}


def _rows(table):
    return [list(row) for row in table.get("rows", []) if isinstance(row, (list, tuple))]


def _flattened_cell_keys(rows):
    return {
        key
        for row in rows
        for cell in row
        if (key := identity_key(cell))
    }


def _prior_product_map(prior_names):
    products = {}
    for name in prior_names or ():
        key = identity_key(name)
        if key and key not in TOTAL_KEYS:
            products.setdefault(key, str(name).strip())
    return products


def _title_text(table):
    return str(table.get("section_title") or "").strip()


def _header_text(rows):
    return " ".join(str(cell or "") for row in rows[:4] for cell in row).strip()


def _table_text(rows):
    return " ".join(str(cell or "") for row in rows for cell in row).strip()


def _normalized_title(value):
    text = str(value or "").strip()
    text = re.sub(
        r"^\s*(?:[（(]?[一二三四五六七八九十\dA-Da-divxIVX]+[）)\.、．]?\s*)+",
        "",
        text,
    )
    text = re.sub(r"[（(](?:續|续|continued)[）)]\s*$", "", text, flags=re.I)
    return re.sub(r"[\s:：。]+$", "", text).strip()


def _exclusion_reasons(table, _rows):
    """只按完整 section_title/title 排除，不读取表头和表格正文。"""
    titles = {
        title
        for title in (_normalized_title(table.get("section_title")),)
        if title
    }
    return list(dict.fromkeys(
        name
        for name, pattern in STRICT_TITLE_EXCLUSIONS
        if any(pattern.fullmatch(title) for title in titles)
    ))


def _semantic_family(table, rows):
    """判断完整收入语义；同一语义优先采用离表最近的标题证据。"""
    title = _title_text(table)
    header = _header_text(rows)
    body = _table_text(rows)
    zones = ((title, 3), (" ".join((title, header)), 2), (body, 1))
    candidates = []

    for text, evidence_strength in zones:
        if not text:
            continue
        if EXTERNAL_CUSTOMER_REVENUE.search(text):
            candidates.append((5, evidence_strength, "external_customer_revenue", 4))
        if SEGMENT.search(text) and REVENUE.search(text):
            candidates.append((5, evidence_strength, "segment_revenue", 4))
        if PRODUCT_SERVICE.search(text) and TYPE_SPLIT.search(text) and REVENUE.search(text):
            candidates.append((4, evidence_strength, "product_service_revenue", 3))
        if REVENUE.search(text) and BREAKDOWN.search(text):
            candidates.append((3, evidence_strength, "revenue_breakdown", 2))
        if REVENUE.search(text):
            candidates.append((2, evidence_strength, "revenue", 2))
        if PROFIT_LOSS.search(text):
            candidates.append((1, evidence_strength, "profit_loss", 1))

    if not candidates:
        return 0, 0, "unknown", 0
    return max(candidates)


def _is_complete_profit_loss(table, rows):
    """完整亏损/损益表必须同时出现收入、成本和毛利。"""
    title = _title_text(table)
    body = _table_text(rows)
    return bool(
        PROFIT_LOSS.search(" ".join((title, body)))
        and REVENUE.search(body)
        and COST.search(body)
        and GROSS_PROFIT.search(body)
    )


def _select_tables(tables, prior_names):
    prior_products = _prior_product_map(prior_names)
    prior_keys = set(prior_products)
    scored = []
    for document_order, table in enumerate(tables):
        rows = _rows(table)
        flat_keys = _flattened_cell_keys(rows)
        matched_keys = {
            prior_key for prior_key in prior_keys
            if any(historical_product_name_matches(prior_key, cell_key) for cell_key in flat_keys)
        }
        missing_keys = prior_keys - matched_keys
        has_amount = any(NUMBER.match(str(cell or "")) for row in rows for cell in row)
        rejection_reasons = []
        if not rows:
            rejection_reasons.append("empty_table")
        if not has_amount:
            rejection_reasons.append("no_numeric_amount")
        semantic_level, semantic_strength, family, family_priority = _semantic_family(table, rows)
        scored.append({
            "table": table,
            "document_order": document_order,
            "eligible": not rejection_reasons,
            "rejection_reasons": rejection_reasons,
            "semantic_exclusion_reasons": _exclusion_reasons(table, rows),
            "family": family,
            "family_priority": family_priority,
            "semantic_level": semantic_level,
            "semantic_strength": semantic_strength,
            "complete_profit_loss": _is_complete_profit_loss(table, rows),
            "history_product_count": len(prior_keys),
            "matched_product_count": len(matched_keys),
            "matched_product_names": [prior_products[key] for key in prior_products if key in matched_keys],
            "missing_product_names": [prior_products[key] for key in prior_products if key in missing_keys],
            "full_history_match": bool(prior_keys) and matched_keys == prior_keys,
            "flat_cell_count": len(flat_keys),
            "selection_stage": "basic_candidate",
        })
    basic = [item for item in scored if item["eligible"]]
    if not basic:
        return None, scored
    clean = [item for item in basic if not item["semantic_exclusion_reasons"]]
    candidates = clean or basic
    if clean:
        for item in basic:
            if item not in clean:
                item["eligible"] = False
                item["selection_stage"] = "strict_title_excluded"
    full = [item for item in candidates if item["full_history_match"]]
    if full:
        cohort = full
        stage = "full_history_cohort"
    else:
        max_hits = max(item["matched_product_count"] for item in candidates)
        cohort = [item for item in candidates if item["matched_product_count"] == max_hits]
        stage = "fallback_max_history_cohort"
    for item in cohort:
        item["selection_stage"] = stage
    if len(cohort) == 1:
        cohort[0]["selection_stage"] = (
            "selected_single_full_history_candidate" if full
            else "selected_single_fallback_history_candidate"
        )
        return cohort[0]["table"], scored
    complete_profit_loss = [item for item in cohort if full and item["complete_profit_loss"]]
    if len(complete_profit_loss) == 1:
        complete_profit_loss[0]["selection_stage"] = "selected_complete_profit_loss"
        return complete_profit_loss[0]["table"], scored
    if complete_profit_loss:
        cohort = complete_profit_loss
        for item in cohort:
            item["selection_stage"] = "complete_profit_loss_cohort"
    selected = max(
        cohort,
        key=lambda item: (
            item["semantic_level"], item["semantic_strength"], item["family_priority"]
        ),
    )
    selected["selection_stage"] = "selected_semantic_family"
    return selected["table"], scored


def select_main_table(sections, prior_names=()):
    """按文档顺序遍历各章节中的物理表，只返回唯一主表和完整打分日志。"""
    tables = [table for section in sections or () for table in section.get("tables", [])]
    return _select_tables(tables, prior_names)
