# -*- coding: utf-8 -*-
"""HKCO_FN_PRODUCT get_res"""
import re

from custom.service.EAPS_HKCO_FN_PRODUCT import fullwidth_to_halfwidth


# ═══════════════════ Chinese numeral → Arabic ═══════════════════

def replace_chinese_numerals(text):
    """
    Replace all occurrences of Chinese numerals in the text with their corresponding Arabic numerals.
    Handles numbers from 0 to 9999 and beyond, including complex structures with 千, 百, and 十.
    """
    # Replace variant zero characters with standard '0'
    text = re.sub(r'[〇○零]', '0', text)

    single_digits = {
        '0': 0, '一': 1, '二': 2, '三': 3, '四': 4,
        '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
    }

    multipliers = {'千': 1000, '百': 100, '十': 10}

    pattern = re.compile(r'[0-9一二三四五六七八九十百千]+')

    def parse_numeral(s):
        total = 0
        current_value = 0
        last_multiplier = float('inf')

        for c in s:
            if c in single_digits:
                current_value += single_digits[c]
            elif c in multipliers:
                if current_value == 0 and c in ('十', '百', '千'):
                    current_value = 1
                multiplier = multipliers[c]
                if multiplier >= last_multiplier:
                    total += current_value
                    current_value = 0
                else:
                    total += current_value * multiplier
                    current_value = 0
                    last_multiplier = multiplier
            else:
                continue
        total += current_value
        return str(total)

    def replace_match(match):
        numeral = match.group()
        if numeral.isdigit():
            return numeral
        return parse_numeral(numeral)

    return pattern.sub(replace_match, text)


# ── currency detection ──
_CURRENCY_DETECT_RE = re.compile(
    r'(人民幣|人民币)|'
    r'(港[幣币元])|(港元)|'
    r'(美[金元])|'
    r'(新加坡[元幣币])|'
    r'(令吉)|(馬幣|马币|馬來西亞|马来西亚)|'
    r'(加拿大[元幣币])|'
    r'(歐元|欧元|EURO?)|'
    r'(日[圓元])|'
    r'(澳門[元幣币]|澳门[元幣币])|'
    r'(英鎊|英镑)'
)

_CURRENCY_MAP = {
    "人民幣": "人民币", "人民币": "人民币",
    "港元": "港元", "港幣": "港元", "港币": "港元",
    "美元": "美元", "美金": "美元",
    "新加坡元": "新加坡元",
    "令吉": "马来西亚林吉特", "馬幣": "马来西亚林吉特", "马币": "马来西亚林吉特",
    "马来西亚": "马来西亚林吉特", "馬來西亞": "马来西亚林吉特",
    "加拿大元": "加拿大元",
    "歐元": "欧元", "欧元": "欧元", "EUR": "欧元", "EURO": "欧元",
    "日圓": "日元", "日元": "日元",
    "澳門元": "澳门元", "澳门元": "澳门元",
    "英鎊": "英镑", "英镑": "英镑",
}


def _detect_currency(table):
    """从表中检测币种。扫描所有单元格中的币种关键词，返回最常见的币种。"""
    if not isinstance(table, list):
        return ""
    flat = " ".join(
        str(c or "")
        for r in table[:6]
        for c in (r if isinstance(r, list) else [])
    )
    matches = {}
    for m in _CURRENCY_DETECT_RE.finditer(flat):
        matched_text = m.group(0)
        currency = _CURRENCY_MAP.get(matched_text, "")
        if currency:
            matches[currency] = matches.get(currency, 0) + 1
    if matches:
        return max(matches, key=matches.get)
    return ""


def _detect_unit(table, currency=""):
    """从表头检测金额单位。返回 unit code: 001=元, 002=千元, 004=百万元。

    优先从 header 行检测，再扫描正文。
    """
    if not isinstance(table, list) or len(table) < 1:
        return ""
    # 先扫描前6行（header区域）
    header_text = " ".join(
        str(c or "")
        for r in table[:min(6, len(table))]
        for c in (r if isinstance(r, list) else [])
    )
    # 百万元标记（包括"百萬港元"、"百万元"、"以百萬元計"等）
    if re.search(r'百萬', header_text):
        return "004"
    # 千元标记：千港元、千元、人民幣千元、港幣千元（但不含"百萬"）
    if re.search(r'(?<!百)千元|(?<!百萬)千港元|千美元|千令吉|千新加坡元|'
                 r'人民幣千元|人民币千元|港幣千元|港币千元', header_text):
        return "002"
    # 元（无千/百万前缀）—— 只在表头明确出现"单位：元"或"以元計"时
    if re.search(r'(?:單位|单位|以)\s*[：:]\s*元\b|以元[計计]|\(元\)|（元）', header_text):
        return "001"
    # 扫描全表（兜底）
    full_text = " ".join(
        str(c or "")
        for r in table[:20]
        for c in (r if isinstance(r, list) else [])
    )
    if re.search(r'百萬', full_text):
        return "004"
    if re.search(r'(?<!百)千元|(?<!百萬)千港元|人民幣千元|人民币千元|'
                 r'港幣千元|港币千元', full_text):
        return "002"
    # 根据币种推测：人民币通常用千元，港元也可用千元
    if currency and '人民币' in str(currency):
        return "002"  # 人民币财务报表通常以千元为单位
    # 检查数值量级：若最大金额 >10^8 且无千/百万标记，大概率是原始元
    _has_large_raw_numbers = False
    for r in table[:min(20, len(table))]:
        if not isinstance(r, list):
            continue
        for c in r[1:]:
            try:
                v = str(c or "").replace(",", "").replace("(", "").replace(")", "")
                if v and re.search(r'\d', v):
                    fv = float(v)
                    if fv > 1e8:
                        _has_large_raw_numbers = True
                        break
            except (ValueError, TypeError):
                pass
        if _has_large_raw_numbers:
            break
    if _has_large_raw_numbers:
        return "001"  # 数值过大不可能是千元
    return ""


def classify_table(table, title, last_period_data, page_lines=None):
    """返回表类型字符串

    分类层次：
    1. 先排错表（免责声明、公司名、MD&A等）
    2. 分部表（优先于损益，分部标题常同时含P&L关键词）
    3. 百度多期间块表
    4. 损益表（但标题已命中分部的不再归损益）
    5. 收入/收益表，结构兜底
    """
    profile = classify_table_structure(table, last_period_data)
    row = profile["product_axis"] == "row"
    t = str(title or "").strip()

    # ── 排除明显的错表 ──
    # 附注/脚注（不是表格标题）
    if re.match(r'^(附註|附注|Note\s*\d)', t):
        return ""
    # 标题过短且无产品/分部关键词 → 检查表内是否有LP产品匹配
    if len(t) <= 2 and not re.search(r'產品|产品|分部|收入|收益|服務|服务|業務|业务', t):
        # 若表内有LP产品名匹配或有≥2个产品行，不拒绝
        lp_names = [fullwidth_to_halfwidth(str(r.get("PRODUCTNAME", "")).strip())
                    for r in (last_period_data or []) if isinstance(r, dict) and r.get("PRODUCTNAME", "").strip() != "合计"]
        has_lp_in_table = False
        if lp_names:
            for r in table:
                if not isinstance(r, list) or not r:
                    continue
                c0 = fullwidth_to_halfwidth(str(r[0] or "").strip())
                if any(n in c0 or c0 in n for n in lp_names):
                    if any(re.search(r'\d', str(r[c] or "")) for c in range(1, min(3, len(r)))):
                        has_lp_in_table = True
                        break
        if not has_lp_in_table:
            return ""

    # ── 分部表（优先于损益：分部标题常同时含P&L关键词如"綜合收益表"）──
    is_segment = ('分部' in t or '經營分部' in t or '经营分部' in t
                  or '可呈報分部' in t or '可报告分部' in t
                  or '可呈报分部' in t or '可報告分部' in t
                  or re.search(r'分部[资料料信息報告报告业绩業績分析]', t))
    if is_segment:
        if row:
            if profile["row_hierarchy"] == "parent_child":
                return "结构_行产品_父子层级_分部"
            return "分部_行产品"
        else:
            basis = _classify_segment_revenue_basis(table, last_period_data)
            if profile["header_alignment"] == "missing_stub":
                return _column_structure_type(profile, "分部矩阵", basis)
            if (profile["business_role"] == "收入确认分拆"
                    and profile["period_layout"] == "stacked_blocks"):
                return _column_structure_type(profile, "收入确认分拆", basis)
            typ = "分部_列产品"
            basis_suffix = {
                "report": "报告分部收入",
                "group": "集团收入",
                "operating": "营业收入",
                "segment": "分部收入",
                "external": "外部收入",
            }[basis]
            return typ + "_" + basis_suffix

    # ── 百度多期间块表 ──
    if page_lines:
        pl_text = " ".join(str(l.get("content", "")) for l in page_lines if isinstance(l, dict))
        if "百度集團股份有限公司" in pl_text:
            return "百度"

    # ── 损益表 ──
    if ('損益' in t or '损益' in t or '利潤表' in t or '利润表' in t
            or '綜合收益' in t or '综合收益' in t or '綜合虧損' in t or '综合亏损' in t
            or '全面收益' in t or '合併經營' in t or '合并经营' in t
            or '合併利潤' in t or '合并利润' in t or '合併收入表' in t or '合并收入表' in t
            or re.search(r'綜合[損亏]益|综合[损亏]益', t)
            or re.search(r'合[併并]綜合|合[并併]综合', t)
            or re.search(r'STATEMENT\s+OF\s+PROFIT\s+OR\s+LOSS', t, re.I)
            or re.search(r'CONDENSED\s+CONSOLIDATED', t, re.I)
            or re.search(r'合[併并]經營[表报報]', t)):
            # 单产品公司 P&L：LP 只有 1 个非合计产品 → 用 LP 产品名
            lp_product_names = [str(r.get("PRODUCTNAME", "")).strip()
                               for r in (last_period_data or [])
                               if isinstance(r, dict)
                               and str(r.get("PRODUCTNAME", "")).strip() != "合计"]
            if len(lp_product_names) == 1:
                if _has_note_reference_column(table):
                    return "损益_单产品_附注列"
                return "损益_单产品"
            return "损益"

    # ── 收入/收益表 ──
    if '收入' in t or '收益' in t:
        if row:
            if _has_lp_parent_child_structure(table, last_period_data):
                return "收入_行产品_父子明细"
            return "收入_行产品"
        else:
            if (profile["header_alignment"] == "missing_stub"
                    or (profile["business_role"] == "收入确认分拆"
                        and profile["period_layout"] == "stacked_blocks")):
                return _column_structure_type(profile, profile["business_role"], "external")
            return "收入_列产品"

    # ── 结构兜底 ──
    if row:
        typ = (
            "收入_行产品_父子明细"
            if _has_lp_parent_child_structure(table, last_period_data)
            else "收入_行产品"
        )
    else:
        if profile["header_alignment"] == "missing_stub":
            typ = _column_structure_type(profile, profile["business_role"], "external")
            if _has_cost_section(table):
                typ += "_含成本段"
            return typ
        typ = "收入_列产品"

    # 检测成本段：如果前面有产品行、后面有成本标记 → 标记含成本段
    if typ.endswith("行产品") or typ.endswith("列产品"):
        if _has_cost_section(table):
            typ = typ + "_含成本段"

    return typ


def _has_note_reference_column(table):
    """是否为「科目 + 附注编号 + 金额列」的单产品损益表。"""
    if not isinstance(table, list):
        return False
    for row in table[:6]:
        if not isinstance(row, list):
            continue
        if any(re.fullmatch(r"附[註注]|Notes?", str(c or "").strip(), re.I)
               for c in row[:3]):
            return True
    for row in table:
        if not isinstance(row, list) or len(row) < 3:
            continue
        label = fullwidth_to_halfwidth(str(row[0] or "").strip())
        ref = fullwidth_to_halfwidth(str(row[1] or "").strip())
        amount = fullwidth_to_halfwidth(str(row[2] or "").strip())
        if (re.search(r"收入|收益|營業額|营业额|Revenue", label, re.I)
                and re.fullmatch(r"\d{1,2}(?:\([a-z]\))?", ref, re.I)
                and re.search(r"\d", amount)):
            return True
    return False


def _clean_header_product(cell):
    text = fullwidth_to_halfwidth(str(cell or "").strip())
    text = re.sub(
        r"人民幣千元|人民币千元|港幣千元|港币千元|千港元|千元|"
        r"人民幣|人民币|港元|百萬元|百万元|美元|美金",
        "", text,
    )
    return re.sub(r"\s+", "", text).strip()


def _shifted_product_header_row(row, width, last_period_data=None):
    """产品表头少了左侧度量标签格：header[0] 实际对应 data col1。"""
    if not isinstance(row, list) or len(row) != width - 1 or len(row) < 2:
        return False
    cells = [_clean_header_product(c) for c in row]
    if any(re.fullmatch(r"[()（）+\-\d,.\s]+", c) and re.search(r"\d", c)
           for c in cells):
        return False
    lp_names = [
        fullwidth_to_halfwidth(str(x.get("PRODUCTNAME", "") or "").strip())
        for x in (last_period_data or []) if isinstance(x, dict)
        and str(x.get("PRODUCTNAME", "") or "").strip() not in ("", "合计", "合計")
    ]
    lp_hits = sum(
        1 for c in cells if c and any(n in c or c in n for n in lp_names)
    )
    product_like = sum(
        1 for c in cells
        if len(c) >= 2 and re.search(r"[一-鿿A-Za-z]", c)
        and not re.search(r"^(20\d{2}|二零|截至|收入|收益|金額|金额|百分比|佔比|占比)$", c)
    )
    return lp_hits >= 1 or product_like >= 2


def _column_header_starts_with_product(table, last_period_data=None):
    if not isinstance(table, list) or not table:
        return False
    width = max((len(row) for row in table if isinstance(row, list)), default=0)
    return any(
        _shifted_product_header_row(row, width, last_period_data)
        for row in table[:8]
    )


def classify_table_structure(table, last_period_data=None):
    """只描述表格客观结构，不参考标题，也不绑定具体 extractor。"""
    rows = [row for row in (table or []) if isinstance(row, list)]
    text = " ".join(str(cell or "") for row in rows for cell in row)
    axis = "row" if is_row_product(rows, last_period_data) else "column"

    if axis == "column" and _column_header_starts_with_product(rows, last_period_data):
        header_alignment = "missing_stub"
    else:
        header_alignment = "aligned"

    year_block_rows = 0
    for row in rows:
        if not row:
            continue
        first = fullwidth_to_halfwidth(str(row[0] or "").strip())
        rest_nonempty = sum(bool(str(cell or "").strip()) for cell in row[1:])
        if (re.search(r"(?:20\d{2}|二零[一二三四五六七八九零〇○]{2}).{0,12}(?:年|月|日)", first)
                and rest_nonempty <= 1):
            year_block_rows += 1
    period_layout = "stacked_blocks" if year_block_rows >= 2 else "shared_header"

    if re.search(r"某一時點|某一时点|隨時間|随时间|確認時間|确认时间|準則第\s*15|准则第\s*15", text):
        business_role = "收入确认分拆"
    elif re.search(r"分部間|分部间|分類間|分类间|抵銷|抵销|對銷|对销", text):
        business_role = "分部对账"
    elif re.search(r"分部業績|分部业绩|分部資產|分部资产|可呈報分部|可报告分部", text):
        business_role = "分部矩阵"
    elif re.search(r"銷售成本|销售成本|毛利|經營溢利|经营利润", text):
        business_role = "损益明细"
    else:
        business_role = "收入明细"

    row_hierarchy = "parent_child" if _has_lp_parent_child_structure(rows, last_period_data) else "flat"
    modifiers = []
    if _has_note_reference_column(rows):
        modifiers.append("note_column")
    if re.search(r"%|百分比|佔比|占比", text):
        modifiers.append("percentage_column")
    if re.search(r"抵銷|抵销|對銷|对销", text):
        modifiers.append("elimination")
    if re.search(r"合計|合计|總計|总计|總額|总额|綜合|综合", text):
        modifiers.append("explicit_total")

    return {
        "business_role": business_role,
        "product_axis": axis,
        "header_alignment": header_alignment,
        "period_layout": period_layout,
        "row_hierarchy": row_hierarchy,
        "modifiers": tuple(modifiers),
    }


def _column_structure_type(profile, role, revenue_basis):
    basis_name = {
        "report": "报告分部收入",
        "group": "集团收入",
        "operating": "营业收入",
        "segment": "分部收入",
        "external": "外部收入",
    }.get(revenue_basis, "外部收入")
    period_name = "上下期间块" if profile["period_layout"] == "stacked_blocks" else "共享期间头"
    alignment_name = "缺占位" if profile["header_alignment"] == "missing_stub" else "标准占位"
    return f"结构_列产品_{alignment_name}_{period_name}_{role}_{basis_name}"


def _amount_value(value):
    text = fullwidth_to_halfwidth(str(value or "")).replace(",", "").strip()
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()（）")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return -number if negative else number


def _classify_segment_revenue_basis(table, last_period_data=None):
    """用 LP 金额把分部表分类到明确的收入口径。"""
    lp_amounts = [
        _amount_value(x.get("MBREVENUE")) for x in (last_period_data or [])
        if isinstance(x, dict) and str(x.get("PRODUCTNAME", "")).strip() not in ("合计", "合計")
    ]
    lp_amounts = [x for x in lp_amounts if x is not None]
    if not lp_amounts:
        return "external"

    patterns = {
        "external": re.compile(r"外部|外界|對外|对外|來自.*客户|来自.*客户"),
        "report": re.compile(r"報告分部收入|报告分部收入|可報告分部收益|可报告分部收益"),
        "group": re.compile(r"^(集團收入|集团收入|合併收入|合并收入)$"),
        "operating": re.compile(r"^(營業收入|营业收入|經營收入|经营收入)$"),
        "segment": re.compile(r"^分部收入$"),
    }
    rows_by_basis = {key: [] for key in patterns}
    for row in table if isinstance(table, list) else []:
        if not isinstance(row, list) or not row:
            continue
        label = fullwidth_to_halfwidth(str(row[0] or "").strip())
        for basis, pattern in patterns.items():
            if pattern.search(label):
                rows_by_basis[basis].append(row)

    def score(rows):
        values = [_amount_value(c) for row in rows for c in row[1:]]
        values = [x for x in values if x is not None]
        return sum(
            1 for expected in lp_amounts
            if any(abs(actual - expected) <= max(1e-6, abs(expected) * 1e-6)
                   for actual in values)
        )

    scores = {basis: score(rows) for basis, rows in rows_by_basis.items()}
    best_score = max(scores.values(), default=0)
    if best_score <= 0:
        return "external"
    # 同分时优先采用外部客户口径，其次采用抵销后的集团/营业收入。
    priority = ("external", "group", "operating", "report", "segment")
    return next(basis for basis in priority if scores[basis] == best_score)


def _lp_parent_child_map(last_period_data=None):
    """返回 {短子项名: 完整 LP 名}，仅保留唯一后缀。"""
    grouped = {}
    for item in last_period_data or []:
        if not isinstance(item, dict):
            continue
        name = fullwidth_to_halfwidth(str(item.get("PRODUCTNAME", "") or "").strip())
        if ":" not in name and "：" not in name:
            continue
        parts = re.split(r"[:：]", name, maxsplit=1)
        if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
            continue
        child = parts[1].strip()
        grouped.setdefault(child, []).append(name)
    return {child: names[0] for child, names in grouped.items() if len(set(names)) == 1}


def _has_lp_parent_child_structure(table, last_period_data=None):
    child_map = _lp_parent_child_map(last_period_data)
    if len(child_map) < 2 or not isinstance(table, list):
        return False
    labels = [
        fullwidth_to_halfwidth(str(row[0] or "").strip()).lstrip("-–— ")
        for row in table if isinstance(row, list) and row
    ]
    hits = sum(1 for child in child_map if any(label == child for label in labels))
    return hits >= 2


# 成本段标记（绝不可能是产品名的P&L行）
_COST_SECTION_RE = re.compile(
    r'^(銷售成本|销售成本|服務成本|服务成本|營業成本|营业成本)'
    r'([：:總总]|$)|'
    r'^(毛利$|毛利:|營業費用|营业费用|營業費用總額|营业费用总额|'
    r'經營開支|经营开支|經營開支總額|经营开支总额|'
    r'經營虧損|经营亏损|經營利潤|经营利润|'
    r'所得稅開支|所得税开支|所得稅費用|所得税费用|'
    r'行政開支|行政开支|銷售及分銷|销售及分销|'
    r'銷售及營銷|销售及营销|融資成本|融资成本|'
    r'銷售成本總額|销售成本总额|營業費用總額|营业费用总额|'
    r'運營成本及開支|运营成本及开支|'
    r'一般及行政|研發|銷售費用|销售费用|管理費用|管理费用)$')


def _has_cost_section(table):
    """检测表中是否有成本段（收入行后出现成本/费用等标记）。"""
    if not isinstance(table, list) or len(table) < 3:
        return False
    has_product = False
    # 宽松成本关键词（行标签中包含则很可能不是产品名）
    _LOOSE_COST = re.compile(r'成本|費用|费用|開支|开支|虧損|亏损|研發|研发|'
                            r'所得稅|所得税|折舊|折旧|攤銷|摊销|利息|減值|减值|'
                            r'薪金|津貼|酬金|撥備|拨备')
    for row in table:
        if not isinstance(row, list) or len(row) < 1:
            continue
        c0 = str(row[0] or '').strip()
        if _COST_SECTION_RE.search(c0):
            if has_product:
                return True
            return False
        # 宽松检测：产品行后出现成本关键词 → 可能含成本段
        if has_product and _LOOSE_COST.search(c0) and not re.search(
                r'產品|产品|服務|服务|銷售|销售', c0):
            if any(re.search(r'\d', str(c or '')) for c in row[1:]):
                return True
        if re.search(r'[一-鿿]', c0) and any(re.search(r'\d', str(c or '')) for c in row[1:]):
            has_product = True
    return False

_UNIT_RE = re.compile(r"千港元|千元|千美元|千令吉|千新加坡元|"
                      r"人民幣千元|人民币千元|人民幣|人民币|"
                      r"百萬元|百万元|百萬港元|百万港元|"
                      r"港幣千元|港币千元|港元|美元|美金")

_YEAR_RE = re.compile(r"20\d{2}|二零")


# P&L/BS 行标签匹配：仅当整个标签（去掉前后缀后）是纯 P&L 术语才过滤
# 防止误伤 "激光雷達產品收入" 等产品名（含"收入"作为后缀）
_PL_ROW_LABEL_RE = re.compile(
    r"^(來自外部客户|来自外部客户|對外銷售|对外销售|外部銷售|外部销售|"
    r"外部客户|外部客戶|對外部客户|对外部客户|"
    r"分部收益|分部收入|分部業績|分部业绩|可呈報分部|可报告分部|"
    r"分類收益|分类收益|分類收益總額|分类收益总额|"
    r"總收益|总收益|總收入|总收入|合計|合计|總計|总计|"
    r"收益|收入|營業額|营业额|營業收入|营业收入|經營收入|经营收入|"
    r"銷售|销售|銷售收入|销售收入|銷售收益|销售收益|"
    r"分部業績|分部业绩|分部溢利|分部利润|分部虧損|分部亏损|"
    r"業績|业绩|溢利|利潤|利润|虧損|亏损|"
    r"客戶合約收益|客户合约收益|合約收益|合约收益|"
    r"間銷售|间销售|間收益|间收益|內部銷售|内部销售|"
    r"除稅前|除税前|持續經營|持续经营|"
    r"其他收入|其他收益|其他營運|其他营运|"
    r"融資成本|融资成本|財務費用|财务费用|"
    r"未分配|對賬|对账|綜合|综合|總計|总计|合計|合计|總額|总额|"
    r"來自外部客户之收益|来自外部客户之收益|"
    r"來自外部客戶之收益|来自外部客户之收益|"
    r"可呈報分部收益|可报告分部收益|可呈报分部收益|"
    r"對外部客户銷售|對外部客戶銷售|对外部客户销售|"
    r"外部客户收益|外部客戶收益|外部客户收入|外部客戶收入|"
    r"客戶合約收入|客户合约收入|"
    # IFRS15 时间标签
    r"於某一時間點|于某一时间点|於某個時間點|于某个时间点|"
    r"某一時間點|某一时间点|隨時間|随时间|"
    r"於一段時間內|于一段时间内|一段時間內|一段时间内|"
    r"在某時間點|在某时间点|時間點|时间点|"
    r"隨時間確認|随时间确认|隨時間推移|随时间推移|"
    # 地区行
    r"中國內地|中国内地|中國大陸|中国大陆|"
    r"香港|澳門|澳门|台灣|台湾|海外|"
    r"其他地區|其他地区|其他市場|其他市场|"
    r"馬來西亞|马来西亚|新加坡|美國|美国|日本|"
    # 小计/净额/总计
    r"收益總額|收益总额|收益淨額|收益净额|"
    r"收入總額|收入总额|收入淨額|收入净额|"
    r"合約總收入|合约总收入|合約總收益|合约总收益|"
    # 其他非产品
    r"提供服務|提供服务|銷售貨物|销售货物|"
    r"服務收入$|服务收入$|產品收入$|产品收入$|"
    r"服務收益$|服务收益$|產品收益$|产品收益$)$"
)


def _is_noise_label(name):
    """Check if a product name is actually a P&L/BS noise label.

    Only filters SHORT labels (≤6 chars) that are pure P&L terms,
    or specific compound patterns that are NEVER product names.
    Longer names like "銷售原鋁及合金" are preserved as genuine products.
    """
    n = str(name or "").strip()
    if not n or len(n) < 2:
        return True
    # Short exact P&L terms (≤4 chars): 收入, 收益, 成本, 費用, etc.
    if len(n) <= 4:
        _SHORT_PL = re.compile(
            r"^(收入|收益|成本|費用|费用|開支|开支|"
            r"利潤|利润|虧損|亏损|溢利|毛利|"
            r"銷售|销售|營業|营业|"
            r"總計|总计|合計|合计|總額|总额|"
            r"本集團|本集团|按年|"
            r"稅|税|利息)$")
        if _SHORT_PL.search(n):
            return True
    # P&L terms that can be 2-8 chars and never appear as standalone products
    if 2 <= len(n) <= 8:
        _MED_PL = re.compile(
            r"^(分部利潤|分部利润|分部虧損|分部亏损|分部溢利|分部收益|分部收入|分部業績|分部业绩|"
            r"研發費用|研发费用|研發開支|研发开支|"
            r"利息開支|利息开支|利息費用|利息费用|利息收入|"
            r"營業收入|营业收入|營業成本|营业成本|營業額|营业额|"
            r"融資成本|融资成本|財務成本|财务成本|"
            r"銷售成本|销售成本|產品銷售|产品销售|"
            r"行政開支|行政开支|銷售開支|销售开支|"
            r"經營利潤|经营利润|經營虧損|经营亏损|"
            r"減值虧損|减值亏损|減值損失|减值损失|"
            r"長期資產|长期资产|資產減值|资产减值|"
            r"所得稅|所得税|所得稅費用|所得税费用|"
            r"非控股|歸屬|归属|淨利潤|净利润|淨虧損|净亏损|"
            r"經營收入|经营收入|銷售及管理費用|销售及管理费用|"
            r"銷售及分銷|销售及分销|"
            r"成本及費用|成本及费用|成本及費用總額|成本及费用总额|"
            r"可呈報分部|可报告分部|可申報分部|可申报分部|"
            r"對賬|对账|調節|调节|未分配|"
            r"經調整|经调整|調整後|调整后|"
            r"除稅前|除税前|持續經營|持续经营|"
            r"期內溢利|期内溢利|期內虧損|期内亏损|"
            r"年內溢利|年内溢利|每股盈利|每股虧損|每股亏损|"
            r"分部開支|分部开支|分部費用|分部费用|"
            r"分部資產|分部资产|分部負債|分部负债|"
            r"分部間銷售|分部间销售|分部間抵銷|分部间抵销|"
            r"未分配開支|未分配开支|未分配收入|未分配收益|"
            r"中央行政|企業開支|企业开支|"
            r"其他分部|其他分类|其他分類|"
            # IFRS15 收入确认时间标签
            r"於某一時間點|于某一时间点|於某個時間點|于某个时间点|"
            r"某一時間點|某一时间点|隨時間確認|随时间确认|"
            r"一段時間內|一段时间内|於一段時間內|于一段时间内|"
            r"在某時間點|在某时间点|時間點轉移|"
            # 小计/净额
            r".*收益總額$|.*收益总额$|.*收入總額$|.*收入总额$|"
            r".*收益淨額$|.*收益净额$|.*收入淨額$|.*收入净额$)$")
        if _MED_PL.search(n):
            return True
    return False


def _cell_looks_like_product_name(cell):
    """判断单元格是否像产品名（先清理单位/日期/百分比后缀再判断）"""
    s = str(cell or "").strip()
    if not s:
        return False
    # 纯数字/符号不是
    if re.fullmatch(r'[\d,.\-()（）\s%％]+', s):
        return False
    # 排除 合计/总计/抵销 等
    if re.match(r'^(合[计計]|總[计計]|小[计計]|總收入|总收入|總收益|总收益|'
                r'總計|总计|合計|合计|總額|总额|抵銷|抵销|對銷|对销)$', s):
        return False
    # 排除日期横幅（截至...止年度/期間）
    if re.match(r'^(截至|止).{0,30}(止|年度|期間|期间|个月|個月|months)', s):
        return False
    # 排除日期类：纯年份、月日
    if re.match(r'^(20\d{2}|二零[一二三四五六七八九零]{2,3})年?$', s):
        return False
    if re.search(r'\d{1,2}\s*月\s*\d{1,2}\s*日', s):
        return False
    # 有中文且长度≥2
    if re.search(r'[一-鿿]', s) and len(s) >= 2:
        return True
    # 纯英文标签长度≥3
    if re.match(r'^[A-Za-z][A-Za-z\s&/\-]{2,}$', s):
        return True
    return False


def _is_metric_subheader_cell(cell):
    """判断表头单元格是否是度量指标标签（收入/成本/金額/百分比等）而非产品名."""
    s = str(cell or "").strip()
    if not s:
        return True
    # 清理：去掉整个括号内容（包括 (人民幣千元, 百分比除外) 等注释）
    s = re.sub(r'[（(][^)）]*[)）]', '', s).strip()
    # 清理单位/日期/百分比符号
    s = _UNIT_RE.sub("", s).strip()
    s = re.sub(r'(20\d{2}|二零[一二三四五六七八九零]{2,3})年?', '', s).strip()
    s = re.sub(r'[%％]', '', s).strip()
    # 清理残留的纯标点/空白
    s = re.sub(r'^[\s,，、。.]+$', '', s).strip()
    if not s:
        return True
    # _is_noise_label 过滤已知的 P&L/BS 噪声标签
    if _is_noise_label(s):
        return True
    # 常见的列头度量标签
    _EXTRA_METRIC = re.compile(
        r'^(金額|金额|百分比|佔比|占比|比重|同比|按年|按季|變動|变动|'
        r'變動率|变动率|增長率|增长率|降幅|增幅|'
        r'數目|数目|項目|项目|類別|类别|'
        r'附註|附注|Note|'
        r'人民幣|人民币|港元|美元|千港元|千元|百萬元|百万元)$')
    if _EXTRA_METRIC.search(s):
        return True
    # 广义度量特征：包含 額/率/比/增/減 → 大概率是度量标签非产品名
    # 产品名通常描述业务（業務/产品/服務/销售），不会包含這些统计术语
    if re.search(r'[額额率][\s$]|[率比][\s$]|同比|按年|按季|增[減减]|變[動动]', s):
        return True
    # 纯年份/期间标签（本年/上年/本期/上期/同期）
    if re.match(r'^(本|上|同)[年期季度]', s):
        return True
    return False


# 表身行中如果包含这些 P&L 专用术语 → 大概率是 P&L 行项目而非产品名
_PL_BODY_INDICATOR = re.compile(
    r'折舊|折旧|攤銷|摊销|減值|减值|撥回|拨回|'
    r'所得稅|所得税|每股|加權|加权|基本及|攤薄|稀释|'
    r'公平值|利息開支|利息支出|融資成本|融资成本|'
    r'廢料|废料|薪金|津貼|酬金|'
    r'貿易應收款|贸易应收款|合約資產|合约资产|'
    r'物業、廠房及設備|物业、厂房及设备|使用權資產|使用权资产|'
    r'無形資產|无形资产|商譽|商誉')


def _table_structure_signal(table):
    """纯结构信号：(body_product_score, hdr_product_score)

    body_product_score: body 行中 col 0 有产品名 + 其他列有数字的行数
    hdr_product_score: 表头 columns 1+ 中产品名数量（清理单位/日期后判断）

    改进：表头列中的度量标签（收入、成本、金額、百分比等）不计入 hdr_score，
    重复出现的标签（同一行出现多次）也不计入。
    """
    hdr, body = _split_header_body(table)

    # body_product_score: exclude P&L line items (收益/成本/溢利/etc.)
    body_product_score = 0
    hdr_set = set(id(r) for r in hdr)
    for row in table:
        if not isinstance(row, list) or len(row) < 2:
            continue
        if id(row) in hdr_set:
            continue
        c0 = str(row[0] or "").strip()
        # Skip P&L sub-item rows (these are metric labels, not products)
        if _PL_ROW_LABEL_RE.search(c0):
            continue
        # Also skip if c0 looks like a P&L noise label (catches non-exact matches)
        if _is_noise_label(c0):
            continue
        # Skip rows whose col 0 contains definite P&L indicators
        if _PL_BODY_INDICATOR.search(c0):
            continue
        has_numbers = any(re.search(r'\d', str(c or "")) for c in row[1:])
        if _cell_looks_like_product_name(c0) and has_numbers:
            body_product_score += 1

    # hdr_product_score: 表头列中产品名数量
    hdr_product_score = 0
    for row in hdr:
        # 只处理有至少2个非空列的表头行（1个非空列=节标题，非列头）
        non_empty_cols = sum(1 for c in range(1, len(row))
                           if str(row[c] or '').strip())
        if non_empty_cols < 2:
            continue
        # 收集该行所有非空清理后的 cell 文本
        row_cleaned = []
        for c in range(1, len(row)):
            cell = str(row[c] or "").strip()
            if not cell:
                continue
            clean = _UNIT_RE.sub("", cell).strip()
            clean = re.sub(r'(20\d{2}|二零[一二三四五六七八九零]{2,3})年?', '', clean).strip()
            clean = re.sub(r'[%％]', '', clean).strip()
            if not clean:
                continue
            row_cleaned.append(clean)

        if not row_cleaned:
            continue

        from collections import Counter
        txt_counts = Counter(row_cleaned)

        # 重复文本检测：只有 SHORT metric-like 文本重复才说明是度量子表头
        # 长产品名（如 餐飲業務）跨多年重复是正常的，应计数
        all_repeating_are_metric = True
        for txt, cnt in txt_counts.items():
            if cnt > 1 and not _is_metric_subheader_cell(txt):
                all_repeating_are_metric = False
                break

        if all_repeating_are_metric and len(txt_counts) >= 1 and any(cnt > 1 for cnt in txt_counts.values()):
            # 所有重复的文本都是度量标签 → 度量子表头行，整行跳过
            continue

        # 每个 cell 判断：不是度量标签 + 像产品名 → 计数
        for clean in row_cleaned:
            if not _is_metric_subheader_cell(clean) and _cell_looks_like_product_name(clean):
                hdr_product_score += 1

    return body_product_score, hdr_product_score


def _lp_name_in_cell(lp_name, cell_text):
    """LP 产品名是否匹配单元格文本（非子串匹配，防「其他」误中「其他收入」）。"""
    n = fullwidth_to_halfwidth(str(lp_name or "")).strip()
    c = fullwidth_to_halfwidth(str(cell_text or "")).strip()
    if not n or not c:
        return False
    if n == c:
        return True
    # 短 LP 名（≤3 CJK chars）极容易误中子串 → 必须精确匹配
    cjk_count = len([ch for ch in n if '一' <= ch <= '鿿'])
    if cjk_count <= 3 and len(n) <= 4:
        return n == c
    # 长 LP 名：允许子串匹配，但必须在词边界（前后是标点/空白/行首尾）
    idx = c.find(n)
    if idx < 0:
        return False
    before_ok = idx == 0 or not re.match(r'[一-鿿A-Za-z0-9]', c[idx - 1])
    after_ok = (idx + len(n) >= len(c)
                or not re.match(r'[一-鿿A-Za-z0-9]', c[idx + len(n)]))
    return before_ok and after_ok


def is_row_product(table, last_period_data=None):
    """结构分析 + 上期产品名匹配：判断产品在行头还是列头"""
    # 提取上期产品名（排除"合计"）
    names = [str(r.get("PRODUCTNAME", "")).strip()
             for r in (last_period_data or [])
             if isinstance(r, dict) and str(r.get("PRODUCTNAME", "")).strip() != "合计"]
    rows = [r for r in table if isinstance(r, list) and len(r) > 0]

    # 1. 上期产品名匹配 → 最可靠的信号
    if names:
        # 在 col 0 中匹配（行产品）— 只计有金额数据的行
        # 双语表：col0 英文时也查 col1（中文产品名常在第二列）
        _PL_COMPOUND = re.compile(r'客户合約|分部|業績|损益|開支|资产|负债|資產|負債|費用|成本|折舊|攤銷|利息')
        def _row_match(n, r):
            c0 = str(r[0] or "")
            if _lp_name_in_cell(n, c0):
                # LP名命中了col0，但要排除P&L复合标签（"客户合約之收益--直播帶貨"）
                # 特征：col0比LP名长很多 + 含P&L关键词
                if len(c0) > len(n) + 6 and _PL_COMPOUND.search(c0):
                    return False
                return True
            # col0 无中文 → 可能是双语表的英文名列，查 col1
            if len(r) >= 2 and not re.search(r'[一-鿿]', c0):
                c1 = str(r[1] or "")
                if _lp_name_in_cell(n, c1):
                    if len(c1) > len(n) + 6 and _PL_COMPOUND.search(c1):
                        return False
                    return True
            return False

        # 行是否真正有金额数据（排除只有脚注编号 (1)(2) 的伪数据行）
        def _row_has_real_amount(r):
            for c in r[1:]:
                v = str(c or "").strip()
                # 去掉脚注标记 (1)（2）再判断
                v_clean = re.sub(r'[（(]\s*\d+\s*[）)]', '', v).strip()
                if re.search(r'\d', v_clean):
                    return True
            return False

        row_hit = sum(1 for n in names for r in rows
                      if _row_match(n, r)
                      and not _UNIT_RE.search(str(r[0] or ""))
                      and _row_has_real_amount(r))
        # 在表头列的 cols 0+ 中匹配 → 排除有真实金额的数据行
        # 洗掉单位后缀（\n千港元）再匹配，否则短 LP 名会被拒绝（"批發"≠"批發\n千港元"）
        def _clean_cell(v):
            return _UNIT_RE.sub("", str(v or "")).replace("\n", "").strip()
        col_hit = 0
        for n in names:
            for r in rows[:6]:
                if _row_has_real_amount(r):
                    continue  # 排除数据行
                for c in range(0, len(r)):  # 含 col0：列头行首列也是产品名
                    if _lp_name_in_cell(n, _clean_cell(r[c])):
                        col_hit += 1
        if row_hit > 0 or col_hit > 0:
            return row_hit >= col_hit

    # 2. 结构分析兜底
    body_score, hdr_score = _table_structure_signal(table)
    # Tiny table with product in col 0 → row-product
    valid_rows = [r for r in table if isinstance(r, list) and len(r) >= 2]
    if len(valid_rows) <= 3 and body_score >= 1 and hdr_score == 0:
        return True
    # Strong col-product: header has multiple product columns
    if hdr_score >= 3:
        return False
    # Strong row-product: body has product rows, header has no product columns
    if body_score >= 2 and hdr_score == 0:
        return True
    # Weighted: header columns signal is 2x weight vs body rows
    if hdr_score >= 1 and hdr_score * 2 >= body_score:
        return False
    if body_score >= 2 and body_score > hdr_score:
        return True

    # 3. col 0 有 合计/总计/小计 → 行产品表
    for r in table:
        if not isinstance(r, list) or len(r) < 2:
            continue
        lab = str(r[0] or "").strip()
        if re.match(r"^(合[计計]|總[计計]|小[计計])", lab):
            return True

    return False

# P&L 成本/费用行标记（用于判断是否错表）
_PL_COST_MARKS = re.compile(
    r"成本|費用|费用|開支|开支|虧損|亏损|利潤|利润|溢利|"
    r"稅|税|折舊|折旧|攤銷|摊销|利息|減值|减值|"
    r"行政|研發|研发|薪金|津貼|酬金|公平值|"
    r"融資|融资|每股|毛利(?!率)|經營利|经营利|所得稅|所得税|"
    r"銷售及|销售及|管理費|管理费|財務費|财务费")


def _is_pl_or_wrong_table(table, title, last_period_data=None):
    """检测是否是 P&L 表或错表（不应提取的表格）。

    信号：
    1. 标题是明显的非产品表（公司名、披露文字、纯损益表）
    2. 表身行以成本/费用/亏损为主（P&L ratio > 0.5 且无产品匹配）
    """
    if not isinstance(table, list) or len(table) < 3:
        return False

    # ── 信号1：标题明显是错表 ──
    t = str(title or "").strip()
    # 标题太短且没有产品/分部关键词
    if len(t) <= 4 and not re.search(r'產品|产品|分部|收入|收益|服務|服务|業務|业务', t):
        if re.search(r'業績|业绩|摘要|概覽|概览|承諾|承诺|報告|报告', t):
            return True
    # 纯公司名标题（没有表格相关词）
    if re.match(r'^[A-Z][A-Za-z\s.]+$', t) and len(t) > 5 and not re.search(r'Revenue|Income|Segment|Product', t, re.I):
        return True
    # 含免责声明关键词
    if re.search(r'不負責|不负责|聲明|声明|概不', t):
        return True

    # ── 信号2：表身 P&L 成本项占比过高 ──
    _, body = _split_header_body(table)
    pl_cost_rows = 0
    total_rows = 0
    lp_match_rows = 0
    lp_names = [fullwidth_to_halfwidth(str(r.get("PRODUCTNAME", "")).strip())
                for r in (last_period_data or []) if isinstance(r, dict)]

    for row in body:
        if not isinstance(row, list) or len(row) < 2:
            continue
        c0 = str(row[0] or "").strip()
        if not c0:
            continue
        # 只看有数据的行
        if not any(re.search(r'\d', str(c or "")) for c in row[1:]):
            continue
        total_rows += 1
        if _PL_COST_MARKS.search(c0):
            pl_cost_rows += 1
        if lp_names and any(x in c0 or c0 in x for x in lp_names):
            lp_match_rows += 1

    if total_rows >= 5:
        pl_ratio = pl_cost_rows / total_rows
        # P&L 行占比 > 50% 且上期产品命中 < 2 → 很可能是 P&L 表
        if pl_ratio > 0.5 and lp_match_rows < 2:
            return True

    return False


def _trim_cost_section(table):
    """截掉成本段：只保留第一个成本标记之前的行。"""
    if not isinstance(table, list) or len(table) < 3:
        return table
    has_product = False
    for i, row in enumerate(table):
        if not isinstance(row, list) or len(row) < 1:
            continue
        c0 = str(row[0] or '').strip()
        # 精确成本标记
        if _COST_SECTION_RE.search(c0):
            if has_product:
                return table[:i]
            return table
        # 宽松成本检测：产品行后出现成本关键词 + 有金额 + 非产品名 → 截断
        if has_product and re.search(r'成本|費用|费用|開支|开支|研發|研发|'
                                      r'所得稅|所得税|撥備|拨备|減值|减值|'
                                      r'行政開支|行政开支|銷售及|销售及|一般及', c0):
            if any(re.search(r'\d', str(c or '')) for c in row[1:]):
                # 不截产品名：包含产品/服务/业务/销售关键词
                if not re.search(r'產品|产品|服務|服务|業務|业务|銷售|销售|'
                                r'收益|收入|合約|合约', c0):
                    return table[:i]
        if re.search(r'[一-鿿]', c0) and any(re.search(r'\d', str(c or '')) for c in row[1:]):
            has_product = True
    return table


def _inject_cost_and_profit_from_full_table(full_table, rows, trimmed_table, typ):
    """从原始全表中提取成本行和分部业绩行的值，注入到 rows 的 MBCOST/GROSS_PROFIT。

    对列产品表：在 body 中找成本行和分部业绩行，按列位置匹配。
    对分部表额外提取 GROSS_PROFIT（分部业绩）。
    """
    if not full_table or not rows:
        return
    header, body = _split_header_body(full_table)
    nc = max((len(r) for r in full_table if isinstance(r, list)), default=0)

    # 找成本行（适用于所有含成本段的表）
    _COST_ROW_RE = re.compile(r'成本|銷售成本|销售成本|營業成本|营业成本')
    cost_rows = []
    for r in body:
        lab = fullwidth_to_halfwidth(str(r[0] or "").strip())
        if _COST_ROW_RE.search(lab):
            if any(re.search(r"\d", str(r[c] or "")) for c in range(1, len(r))):
                cost_rows.append(r)

    # 找分部业绩行（仅分部表）
    seg_row = None
    if typ.startswith("分部"):
        _SEG_PROFIT_RE = re.compile(
            r'分部業績|分部业绩|分部利潤|分部利润|分部溢利|分部亏损|'
            r'分部業績/\(虧損\)|分部.*業績|分部.*业绩|分部.*溢利|'
            r'分部.*利潤|分部.*利润')
        for r in body:
            lab = fullwidth_to_halfwidth(str(r[0] or "").strip())
            if _SEG_PROFIT_RE.search(lab):
                if any(re.search(r"\d", str(r[c] or "")) for c in range(1, len(r))):
                    seg_row = r
                    break

    if not cost_rows and not seg_row:
        return

    # 从 trim 表 header 重建列→产品名映射
    trim_header, _ = _split_header_body(trimmed_table)
    col_to_product = {}
    _UNIT_NAME_RE = re.compile(
        r"(千港元|千元|千美元|千令吉|千新加坡元|"
        r"人民幣千元|人民币千元|人民幣|人民币|"
        r"百萬元|百万元|百萬港元|百万港元|"
        r"港幣千元|港币千元|港元|美元|美金|"
        r"未經審核|未经审核|經審核|经审核|"
        r"二零[一二三四五六七八九零]{2,3}年|20\d{2}年|"
        r"\([^)]*\)|（[^）]*）)"
    )
    for r in reversed(trim_header):
        for c in range(1, min(nc, len(r))):
            if c in col_to_product:
                continue
            name = fullwidth_to_halfwidth(str(r[c] or "").strip())
            name = re.sub(r"\s+", "", name).strip()
            if not name or not re.search(r"[一-鿿A-Za-z]{2,}", name):
                continue
            name = _UNIT_NAME_RE.sub("", name).strip()
            if not name or not re.search(r"[一-鿿A-Za-z]{2,}", name):
                continue
            if re.match(r"^(合[计計]|總[计計]|小[计計]|總收入|总收入|"
                        r"總收益|总收益|總計|总计|合計|合计|總額|总额)", name) or \
               re.search(r"(總計|总计|合計|合计|總額|总额|總收益|总收益|總收入|总收入)$", name):
                name = "合计"
            col_to_product[c] = name

    if not col_to_product:
        return

    periods = _find_periods(trimmed_table)

    def _match_inject(target_rows, field, label_year_re=None):
        """将 target_rows 中各列的值注入 rows 中匹配的条目。"""
        for tgt_row in target_rows:
            # 检测该行的年份标签（如"2025年成本"）
            row_year = None
            if label_year_re:
                tgt_lab = fullwidth_to_halfwidth(str(tgt_row[0] or "").strip())
                ym = label_year_re.search(tgt_lab)
                if ym:
                    row_year = ym.group(1)

            for col, prod_name in col_to_product.items():
                if col >= len(tgt_row):
                    continue
                val = str(tgt_row[col] or "").strip()
                if not val or val == "-":
                    continue
                # 匹配 rows
                for e in rows:
                    if e.get("product_name") != prod_name:
                        continue
                    if e.get(field):
                        continue  # 已有值，不覆盖
                    if periods and row_year:
                        for pc, y, sd, ed in periods:
                            if pc == col and e.get("start_date") == sd and e.get("end_date") == ed:
                                # 行年份匹配
                                if row_year and (row_year in e.get("year", "") or
                                                row_year in e.get("start_date", "")):
                                    e[field] = val
                    elif periods:
                        for pc, y, sd, ed in periods:
                            if pc == col and e.get("start_date") == sd and e.get("end_date") == ed:
                                e[field] = val
                    elif not e.get(field):
                        e[field] = val

    yr_re = re.compile(r'(20\d{2})')
    # 注入成本
    if cost_rows:
        _match_inject(cost_rows, "mbcost", yr_re)
    # 注入分部业绩
    if seg_row:
        _match_inject([seg_row], "gross_profit", yr_re)


def get_res(selected, info_code, reason_arr, notice_date="", last_period_data=None):
    res = {"target_res": [], "pipe_meta": {"selected_count": 0, "source_pages": []}}
    if not selected: reason_arr.append("未选到目标表"); return res

    target_table = selected.get("target_table") if isinstance(selected, dict) else selected
    page_number = selected.get("page_number") if isinstance(selected, dict) else None
    title = selected.get("title") if isinstance(selected, dict) else ""
    if page_number is not None: res["pipe_meta"]["source_pages"] = [page_number]
    res["pipe_meta"]["selected_count"] = 1
    if not target_table: reason_arr.append("目标表为空"); return res

    rows = []
    page_lines = selected.get("page_lines") if isinstance(selected, dict) else None
    typ = classify_table(target_table, title, last_period_data, page_lines)
    if not typ:
        reason_arr.append("未识别表类型")
        return res

    # 含成本段 → 先截掉成本只留收入段
    # 记录原始表用于后续 GROSS_PROFIT 提取
    full_table = target_table if "_含成本段" in typ else None
    if "_含成本段" in typ:
        target_table = _trim_cost_section(target_table)
        typ = typ.replace("_含成本段", "")

    if typ in ("收入_行产品", "分部_行产品"):
        rows = extract_type1(target_table, last_period_data)
    elif typ in ("收入_行产品_父子明细", "结构_行产品_父子层级_分部"):
        rows = extract_type1_parent_child(target_table, last_period_data)
    elif typ.startswith("结构_列产品_"):
        basis = {
            "报告分部收入": "report",
            "集团收入": "group",
            "营业收入": "operating",
            "分部收入": "segment",
            "外部收入": "external",
        }.get(typ.rsplit("_", 1)[-1], "external")
        if "_标准占位_" in typ and "_收入确认分拆_" in typ:
            rows = extract_type2_total_row_matrix(target_table, last_period_data)
        else:
            rows = extract_type2_first_cell_product(
                target_table,
                last_period_data,
                revenue_basis=basis,
            )
    elif typ in ("收入_列产品", "分部_列产品"):
        rows = extract_type2(target_table, last_period_data)
    elif typ.startswith("分部_列产品_") and "首格即产品" not in typ:
        basis = {
            "报告分部收入": "report",
            "集团收入": "group",
            "营业收入": "operating",
            "分部收入": "segment",
            "外部收入": "external",
        }.get(typ.rsplit("_", 1)[-1], "external")
        rows = extract_type2(
            target_table,
            last_period_data,
            revenue_basis=basis,
        )
    elif typ.startswith("分部_列产品_首格即产品_"):
        basis = {
            "报告分部收入": "report",
            "集团收入": "group",
            "营业收入": "operating",
            "分部收入": "segment",
            "外部收入": "external",
        }.get(typ.rsplit("_", 1)[-1], "external")
        rows = extract_type2_first_cell_product(
            target_table,
            last_period_data,
            revenue_basis=basis,
        )
    elif typ == "损益":
        rows = extract_type3(target_table, last_period_data)
    elif typ == "损益_单产品":
        rows = extract_type4(target_table, last_period_data)
    elif typ == "损益_单产品_附注列":
        rows = extract_type4_note_column(target_table, last_period_data)
    elif typ == "百度":
        rows = extract_type5(target_table, last_period_data)

    if not rows:
        reason_arr.append("提取为空")
        return res

    # 检测币种并注入每条记录
    currency = _detect_currency(target_table)
    if currency:
        for r in rows:
            if not r.get("currency"):
                r["currency"] = currency

    # 检测单位（百万元/千元/元）并注入每条记录
    unit_code = _detect_unit(target_table, currency)
    if unit_code:
        for r in rows:
            if not r.get("unit"):
                r["unit"] = unit_code

    # 分部表：从原始（未截断）全表中提取 GROSS_PROFIT 和 MBCOST
    if full_table:
        _inject_cost_and_profit_from_full_table(full_table, rows, target_table, typ)

    # 后处理：过滤明显的P&L噪声行（短标签+精确匹配PL模式+不在LP中）
    if len(rows) > 1:
        lp_names_post = [fullwidth_to_halfwidth(str(r.get("PRODUCTNAME", "")).strip())
                        for r in (last_period_data or []) if isinstance(r, dict)]
        filtered = []
        for r in rows:
            pn = str(r.get('product_name', ''))
            cjk_count = len([c for c in pn if '一' <= c <= '鿿'])
            # 仅过滤：短CJK名(≤4字) + 精确PL标签匹配 + 不在LP中
            if (cjk_count <= 4 and _PL_ROW_LABEL_RE.search(pn)
                    and pn not in lp_names_post and pn != '合计'):
                continue
            filtered.append(r)
        rows = filtered

    res["target_res"] = rows
    return res



# 收入_行产品 — 表头关键词 + 提取
_TITLE_KW_IN = ["千港元", "千元", "千美元", "千令吉", "千新加坡元",
                "人民幣千元", "人民币千元", "人民幣", "人民币",
                "百萬元", "百万元", "百萬港元", "百万港元",
                "港幣千元", "港币千元", "港元", "美元", "美金",
                "未經審核", "未经审核", "經審核", "经审核",
                "同比", "變動", "变动", "百分比",
                "截至", "年度", "期間", "期间", "止",
                "總收入", "总收入", "總收益", "总收益"]


def _split_header_body(table):
    """按关键词拆表头和数据行

    表头行特征：无数字、含日期/单位关键词、空col0
    数据行特征：col0 有标签 + 其他列有数字
    cols 1+ 有逗号分隔的财务数字 → 必定是数据行（非表头）
    """
    title_rows, data_rows = [], []
    for row in table:
        if not isinstance(row, list) or len(row) < 2:
            continue
        row_str = " ".join(str(c or "") for c in row)
        col0 = str(row[0] or "").strip()

        # 空 col 0 + 其他列全是纯数字 → 合计行，归入数据
        is_sum_row = (not col0 and len(row) > 1
                      and all(re.fullmatch(r'[\d,.\-()（）\s]+', str(c or ""))
                              for c in row[1:] if str(c or "").strip()))

        # cols 1+ 有逗号分隔的财务数字 → 数据行（表头只会有年份/单位，不会有千分位数字）
        has_financial_number = any(
            re.search(r'\d{1,3}(,\d{3})+', str(c or "")) for c in row[1:])
        if has_financial_number:
            data_rows.append(row)
            continue

        # 整行（cols 1+）无数字 → 表头（描述性文字/节标题等）
        has_any_digit = any(re.search(r'\d', str(c or "")) for c in row[1:])
        if not has_any_digit:
            title_rows.append(row)
            continue

        is_title = (not col0 and not is_sum_row
                    or re.match(r"^(20\d{2}|二零)", col0)
                    or any(kw in row_str for kw in _TITLE_KW_IN))
        if is_title:
            title_rows.append(row)
        else:
            data_rows.append(row)
    return title_rows, data_rows


def _detect_subcolumn_metrics(table):
    """检测表格的子列度量类型，返回 {col_index: metric_type}。

    子列度量行是最靠近数据行的表头行，其 cols 1+ 的文本为短度量标签
    （如 收入/成本, 金額/百分比, 同比變動）。返回每个列的度量类型标签，
    用于后续过滤（只提取收入/收益/金額类列）。
    如果检测不确定（列数不对齐），返回空字典，走后续的值级别去重。
    """
    hdr, _body = _split_header_body(table)
    if not hdr:
        return {}

    max_cols = max(len(r) for r in table if isinstance(r, list))

    _METRIC_LABEL = re.compile(
        r'^(金額|金额|百分比|佔比|占比|比重|同比|按年|按季|變動|变动|'
        r'收入|收益|成本|費用|费用|毛利|開支|开支|溢利|亏损|虧損|'
        r'數目|数目|數量|数量|'
        r'變動率|变动率|增長率|增长率)$')

    for row in reversed(hdr):
        # 左补齐到 max_cols，避免列错位
        if len(row) < max_cols:
            row = [''] * (max_cols - len(row)) + list(row)
        labels = {}
        all_metric = True
        for c in range(1, len(row)):
            cell = str(row[c] or '').strip()
            if not cell:
                all_metric = False
                break
            clean = _UNIT_RE.sub('', cell).strip()
            clean = re.sub(r'[%％]', '', clean).strip()
            if not clean:
                all_metric = False
                break
            if _METRIC_LABEL.search(clean):
                labels[c] = clean
            else:
                all_metric = False
                break
        if all_metric and len(labels) >= 2:
            # 列数对齐检查：如果子表头行比表头其他行少列，且 col 0 非空
            # 可能是 PDF 提取对齐问题，保守地不采用子列过滤
            if len(row) < max_cols - 1 and str(row[0] or '').strip():
                return {}  # 对齐不确定，回退到值级别去重
            return labels
    return {}


def _should_skip_subcolumn(metric_label):
    """判断子列度量类型是否应该跳过（非收入/金额类的列）。"""
    if not metric_label:
        return False
    _SKIP_METRICS = re.compile(
        r'^(百分比|佔比|占比|比重|同比|按年|按季|變動|变动|變動率|变动率|'
        r'增長率|增长率|降幅|增幅|'
        r'成本|費用|费用|開支|开支|毛利)$')
    return bool(_SKIP_METRICS.search(metric_label))


def _clean_product_name(name):
    """清理产品名：去掉脚注标记 (1)（2）、去掉前导破折号（保留层级）。"""
    n = str(name or '').strip()
    # 去掉脚注标记 (1), (2), （1）, （2）— 但保留末位如 其他(2) → 其他
    n = re.sub(r'[（(]\s*\d+\s*[）)]\s*$', '', n).strip()
    # 去掉前导破折号但保留层级前缀
    n = re.sub(r'^[-\–—]+', '', n).strip()
    return n


def extract_type1(table, last_period_data=None):
    """从行产品表中提取，用上期产品名过滤"""
    _, data_rows = _split_header_body(table)
    periods = _find_periods(table)
    sub_metrics = _detect_subcolumn_metrics(table)

    # 子列度量检测：跳过非收入列（成本/百分比等）
    skip_cols = set()
    if sub_metrics:
        for col_idx, label in sub_metrics.items():
            if _should_skip_subcolumn(label):
                skip_cols.add(col_idx)
    # 补充检测：表头中带有"成本/費用/開支"的列也应跳过
    if not skip_cols:
        hdr, _ = _split_header_body(table)
        for row in hdr[-3:]:
            for c in range(1, len(row)):
                cell = str(row[c] or '').strip()
                if re.search(r'成本|費用|费用|開支|开支', cell) and not re.search(r'收入|收益|Revenue', cell):
                    skip_cols.add(c)

    # 1. 全量提取原始行
    raw = []  # [(name, [(col, sd, ed, value), ...]), ...]
    last_parent = ""  # 层级父产品名（用于 -- 前缀的子项）
    for row in data_rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        name = fullwidth_to_halfwidth(str(row[0] or "").strip())

        # 双语表：col0 无中文 + col1 有中文 → 用 col1 作为产品名
        if not re.search(r'[一-鿿]', name) and len(row) >= 2:
            c1 = fullwidth_to_halfwidth(str(row[1] or "").strip())
            if c1 and re.search(r'[一-鿿]', c1) and len(c1) >= 2:
                name = c1

        # 跳过"其中:"子项（"of which" breakdown，不是独立产品）
        if re.match(r'其中[：:\s]', name):
            continue

        # 检测层级关系："--" 或 "——" 开头 → 子项，继承父产品名
        is_child = bool(re.match(r'^[-\–—]{1,3}', name))
        name = re.sub(r'^[-\–—]+', '', name).strip()

        # 清理脚注标记
        name = _clean_product_name(name)

        has_cjk = bool(re.search(r"[一-鿿A-Za-z]", name))
        if not name and any(re.search(r'\d', str(c or "")) for c in row[1:]):
            name = "合计"
        if re.match(r"^(合[计計]|總[计計]|总[计計]|小[计計]|總額|总额|"
                     r"收入總額|收入总额|收益總額|收益总额|"
                     r"淨收入總額|净收入总额|淨收益總額|净收益总额|"
                     r"總收入|总收入|總收益|总收益)", name):
            name = "合计"
        if not name:
            continue
        if not has_cjk and name != "合计":
            continue

        # 层级处理：如果是子项且有父产品，组合名称
        if is_child and last_parent and last_parent != "合计":
            name = last_parent + "--" + name
        elif not is_child and name != "合计" and not re.match(r'^[-\–—]', str(row[0] or '').strip()):
            # 非子项 → 更新父产品名（供后续子项使用）
            last_parent = name

        vals = []
        if periods:
            for col, _y, sd, ed in periods:
                if col >= len(row):
                    continue
                if col in skip_cols:
                    continue
                v = str(row[col] or "").strip()
                if v:
                    vals.append((sd, ed, v))
        else:
            for c in range(1, len(row)):
                if c in skip_cols:
                    continue
                v = str(row[c] or "").strip()
                if v:
                    vals.append(("", "", v))
        if vals:
            raw.append((name, vals))

    # 2. 上期产品名包含匹配过滤
    lp_raw = [fullwidth_to_halfwidth(str(r.get("PRODUCTNAME", "")).strip())
              for r in (last_period_data or [])
              if isinstance(r, dict)]
    lp_names = [n for n in lp_raw if n != "合计"]

    def _to_num(v):
        s = str(v).replace(",", "").replace("(", "-").replace(")", "")
        try: return float(s)
        except: return None

    if lp_names:
        kept = []
        removed = []
        for name, vals in raw:
            # 保留：匹配上期产品名，或是 合计，或有足够长度看起来像产品名 (≥4 CJK chars)
            cjk_count = len([c for c in name if '一' <= c <= '鿿'])
            is_lp_match = any(x in name or name in x for x in lp_names)
            looks_like_product = cjk_count >= 4 or bool(re.search(r'[A-Za-z]{4,}', name))
            if is_lp_match or name == "合计" or looks_like_product:
                kept.append((name, vals))
            else:
                removed.append((name, vals))

        # 2.5 从 kept 中排除非核心收入项目（股息收入/利息收入等），仅当已有LP匹配的产品时
        if kept and lp_names:
            _NON_CORE_REVENUE_KEPT = re.compile(
                r'(股息收入|利息收入|其他收入|租金收入|投資收入|財務收入|'
                r'汇兑收益|匯兌收益|政府補助|政府补助|出售.*收益|'
                r'其他收益及虧損|其他收益及亏损|其他經營收入|其他经营收入|'
                r'投資物業.*租金|投资物业.*租金|'
                r'銀行利息|银行利息|存款利息)')
            lp_set = set(lp_names)
            has_lp_match = any(
                any(x in name or name in x for x in lp_set)
                for name, _ in kept if name != "合计"
            )
            if has_lp_match:
                kept = [(name, vals) for name, vals in kept
                        if name == "合计"
                        or any(x in name or name in x for x in lp_set)
                        or not _NON_CORE_REVENUE_KEPT.search(name)]

        # 3. removed 行值累加 = 某个保留行 → 确认子项，删除
        if removed:
            kept_sums = {}
            for name, vals in kept:
                for sd, ed, v in vals:
                    kept_sums.setdefault((sd, ed), []).append((name, v))

            confirmed_children = set()
            for (sd, ed), klist in kept_sums.items():
                for kname, kval in klist:
                    fk = _to_num(kval)
                    if fk is None: continue
                    candidates = []
                    for rname, rvals in removed:
                        for rsd, red, rv in rvals:
                            if (rsd, red) == (sd, ed):
                                fv = _to_num(rv)
                                if fv is not None:
                                    candidates.append((fv, rname))
                    if len(candidates) >= 2:
                        total = sum(c[0] for c in candidates)
                        if abs(total - fk) < 1:
                            for _, rname in candidates:
                                confirmed_children.add(rname)

            removed = [(n, v) for n, v in removed if n not in confirmed_children]

        # 新产品也保留但过滤明显非产品的行
        filtered_removed = []
        for rname, rvals in removed:
            cjk_count = len([c for c in rname if '一' <= c <= '鿿'])
            # 至少3CJK字符或英文长名或LP匹配+非噪声
            looks_valid = (cjk_count >= 3
                          or bool(re.search(r'[A-Za-z]{4,}', rname))
                          or any(x in rname or rname in x for x in lp_names))
            # 排除明显P&L噪声
            is_noise = (_is_noise_label(rname)
                       or _PL_ROW_LABEL_RE.search(rname)
                       or _PL_BODY_INDICATOR.search(rname))
            if looks_valid and not is_noise:
                # 额外检查：非产品段标题特征（地区、客户类型、时间等）
                _SECTION_TITLE_RE = re.compile(
                    r'^(按地区|按地區|按地域|按客户|按客戶|按.*時間|按.*时间|'
                    r'地區市場|地区市场|地域市場|地域市场|區域市場|区域市场|'
                    r'第三方|關聯方|关联方|'
                    r'確認收入時間|确认收入时间|'
                    # IFRS15 收入确认时间标签（絕不是产品名）
                    r'於某一時間點|于某一时间点|於某個時間點|于某个时间点|'
                    r'某一時間點|某一时间点|於.*時間點|于.*时间点|'
                    r'隨時間|随时间|隨時間推移|随时间推移|'
                    r'於一段時間內|于一段时间内|一段時間內|一段时间内|'
                    r'在某時間點|在某时间点|時間點轉移|时间点转移|'
                    # 小计/净额/总额标签
                    r'.*總額$|.*总额$|.*總計$|.*总计$|.*小計$|.*小计$|'
                    r'.*淨額$|.*净额$|.*凈額$|.*收益總額$|.*收益总额$|'
                    # 地区/地理标签
                    r'中國內地$|中国内地$|中國大陸$|中国大陆$|'
                    r'香港$|澳門$|澳门$|台灣$|台湾$|海外$|'
                    r'其他地區$|其他地区$|其他地域$|'
                    r'馬來西亞$|马来西亚$|新加坡$|美國$|美国$|日本$|'
                    # 其他非产品行
                    r'收益分析$|收入分析$|經營業績$|经营业绩$|'
                    r'客戶合約收益$|客户合约收益$|客戶合約收入$|客户合约收入$|'
                    r'合約收益$|合约收益$|合約收入$|合约收入$)$')
                # 非核心收入项目：当已有匹配LP的产品行时，排除不相关的收入项
                _NON_CORE_REVENUE = re.compile(
                    r'(股息收入|利息收入|其他收入|租金收入|投資收入|財務收入|'
                    r'汇兑收益|匯兌收益|政府補助|政府补助|出售.*收益|'
                    r'其他收益及虧損|其他收益及亏损|其他經營收入|其他经营收入)')
                if not _SECTION_TITLE_RE.match(rname):
                    # 非核心收入项：仅当它不在LP名称中且有其他更匹配的产品时才排除
                    is_non_core = bool(_NON_CORE_REVENUE.search(rname))
                    is_lp_item = any(x in rname or rname in x for x in lp_names)
                    if is_non_core and not is_lp_item and kept:
                        # 有LP匹配的核心产品存在，排除此项
                        pass
                    else:
                        filtered_removed.append((rname, rvals))
        raw = kept + filtered_removed

        # 3.5 去重：同名集合中，优先保留 lp 精确匹配，删纯包含匹配的
        if lp_names and len(raw) > 1:
            raw_names = set(n for n, _ in raw)
            lp_set = set(lp_names)
            for n in sorted(raw_names, key=len):
                for m in raw_names:
                    if n != m and n in m and n not in lp_set and m in lp_set:
                        raw = [(name, vals) for name, vals in raw if name != n]
                        break

    # 4. 格式化输出 + 值级别去重 + 自动补合计
    out = []
    for name, vals in raw:
        for sd, ed, v in vals:
            out.append({"product_name": name, "mbrevenue": v,
                        "start_date": sd, "end_date": ed,
                        "year": sd[:4] if sd else ed[:4] if ed else ""})

    # 4.5 值级别去重：同一 (product, period) 有多个值时保留数值最大的
    # （解决百分比/金额列对齐不确定导致的多提取问题）
    if out:
        groups = {}
        for e in out:
            key = (e["product_name"], e["start_date"], e["end_date"])
            groups.setdefault(key, []).append(e)
        deduped = []
        for key, entries in groups.items():
            if len(entries) == 1:
                deduped.append(entries[0])
            else:
                # 保留数值最大的（金额总是远大于百分比）
                best = max(entries, key=lambda x: abs(_to_num(x["mbrevenue"]) or 0))
                deduped.append(best)
        out = deduped

    # GT 有合计但提取没有 → 按期间累加生成合计
    # 单产品或有产品时都尝试生成合计
    should_gen_sum = ("合计" in lp_raw if lp_raw else len(out) > 0)
    if should_gen_sum and not any(e["product_name"] == "合计" for e in out):
        period_sums = {}
        other_names = set()
        for e in out:
            if e["product_name"] != "合计":
                key = (e["start_date"], e["end_date"])
                fv = _to_num(e["mbrevenue"])
                if fv is not None:
                    period_sums[key] = period_sums.get(key, 0) + fv
                    other_names.add(e["product_name"])
        if len(other_names) >= 2:
            for (sd, ed), total in period_sums.items():
                out.append({"product_name": "合计", "mbrevenue": str(int(total)),
                            "start_date": sd, "end_date": ed,
                            "year": sd[:4] if sd else ed[:4] if ed else ""})
    return out


_CN_DIGITS = {"零": "0", "〇": "0", "○": "0", "一": "1", "二": "2",
              "三": "3", "四": "4", "五": "5", "六": "6", "七": "7",
              "八": "8", "九": "9"}


def _extract_years_from_text(text):
    """从文本中提取所有年份（Arabic + Chinese numeral），返回去重的 Arabic 年份列表"""
    years = []
    # Arabic years: 2024, 2025, etc.
    for y in re.findall(r"(20\d{2})", text):
        if y not in years:
            years.append(y)
    # Chinese numeral years: 二零二四, 二〇二三, etc.
    for m in re.finditer(r"二[零○〇]([一二三四五六七八九零○〇]{2})", text):
        try:
            y = "20" + "".join(_CN_DIGITS[c] for c in m.group(1))
            if y not in years:
                years.append(y)
        except Exception:
            pass
    # 日期中的年份: 2024年12月31日
    for m in re.finditer(r"(20\d{2})\s*年\s*\d{1,2}\s*月", text):
        if m.group(1) not in years:
            years.append(m.group(1))
    return years


def extract_type1_parent_child(table, last_period_data=None):
    """行产品专类：LP 使用「父项:子项」，表内父子行没有破折号。"""
    rows = extract_type1(table, last_period_data)
    child_map = _lp_parent_child_map(last_period_data)
    lp_names = {
        fullwidth_to_halfwidth(str(x.get("PRODUCTNAME", "") or "").strip())
        for x in (last_period_data or []) if isinstance(x, dict)
    }
    parent_names = {
        re.split(r"[:：]", name, maxsplit=1)[0].strip()
        for name in lp_names if ":" in name or "：" in name
    }
    mapped = []
    matched_children = set()
    for item in rows:
        name = fullwidth_to_halfwidth(str(item.get("product_name", "") or "").strip())
        clean = name.lstrip("-–— ")
        if clean in child_map:
            item = dict(item)
            item["product_name"] = child_map[clean]
            matched_children.add(clean)
            mapped.append(item)
        elif name in lp_names or name == "合计":
            mapped.append(item)
        elif name in parent_names:
            # LP 要的是父项下的叶子，父项金额只作层级/合计证据。
            continue
        else:
            mapped.append(item)

    # 至少两个叶子命中后，说明该专类成立；过滤同一区块里未标注的中间层。
    if len(matched_children) >= 2:
        allowed = lp_names | {"合计"}
        mapped = [x for x in mapped if str(x.get("product_name", "")) in allowed]

    deduped = {}
    for item in mapped:
        key = (item.get("product_name"), item.get("start_date"), item.get("end_date"))
        deduped[key] = item
    return list(deduped.values())


def _find_periods(table):
    rows = [r for r in table if isinstance(r, list) and len(r) > 0]
    if not rows:
        return []
    nc = max(len(r) for r in rows)

    text = " ".join(str(c or "") for r in rows for c in r)
    mm, dd = 12, 31
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        mm, dd = int(m.group(2)), int(m.group(3))
    half = bool(re.search(r"(6|六).{0,3}(個|个)月|中期|半年|H1|six\s*months", text, re.I))

    padded = []
    for r in rows[:5]:
        if len(r) < nc:
            padded.append([""] * (nc - len(r)) + list(r))
        else:
            padded.append(list(r))

    # 从表头全文中提取所有年份
    header_text = " ".join(str(r[c] or "") for r in padded for c in range(nc) if c < len(r))
    all_years = _extract_years_from_text(header_text)
    if not all_years:
        all_years = _extract_years_from_text(text)

    # 每列找年份，提取该列的月/日用于精确期间计算
    out, seen = [], set()
    for c in range(1, nc):
        hdr = " ".join(str(r[c] or "") for r in padded if c < len(r))
        col_years = _extract_years_from_text(hdr)
        if not col_years:
            # 只兜底有显式年份标签的列；无年份列不映射（避免USD列等重复）
            if all_years:
                col_years = [all_years[0]]  # 只取第一个年份，不重复全表年份
            else:
                col_years = []
        # 从该列 header 提取月日
        col_mm, col_dd = mm, dd
        col_m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", hdr)
        if col_m:
            col_mm, col_dd = int(col_m.group(1)), int(col_m.group(2))
        for y in col_years:
            if (c, y) in seen:
                continue
            seen.add((c, y))
            import datetime, calendar
            try:
                yi = int(y)
                ed_day = col_dd if col_mm != 2 else min(col_dd, 28)
                try:
                    ed = datetime.date(yi, col_mm, ed_day)
                except ValueError:
                    ed = datetime.date(yi, col_mm, 28)
                if half:
                    start_month = col_mm - 5
                    start_year = yi
                    if start_month <= 0:
                        start_month += 12
                        start_year -= 1
                    sd = datetime.date(start_year, start_month, 1)
                else:
                    start_month = col_mm + 1
                    start_year = yi - 1
                    if start_month > 12:
                        start_month = 1
                        start_year = yi
                    sd = datetime.date(start_year, start_month, 1)
                out.append((c, y, sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d")))
            except Exception:
                out.append((c, y, f"{int(y)-1}-12-31", f"{y}-12-31"))

    # Period-per-column dedup: each col gets its most recent year
    col_best = {}
    for entry in out:
        c, y, sd, ed = entry
        if c not in col_best or y > col_best[c][1]:
            col_best[c] = entry
    out = list(col_best.values())
    return out


# 收入_列产品 — 产品在列头，从表身找收入行提取
def extract_type2(table, last_period_data=None, revenue_basis=""):
    header, body = _split_header_body(table)
    nc = max((len(r) for r in table if isinstance(r, list)), default=0)

    # 1. 从表头找产品名（底部优先：最后一行表头离数据最近，产品名最干净）
    _UNIT_NAME_RE = re.compile(
        r"(千港元|千元|千美元|千令吉|千新加坡元|"
        r"人民幣千元|人民币千元|人民幣|人民币|"
        r"百萬元|百万元|百萬港元|百万港元|"
        r"港幣千元|港币千元|港元|美元|美金|"
        r"未經審核|未经审核|經審核|经审核|"
        r"二零[一二三四五六七八九零]{2,3}年|20\d{2}年|"
        r"\([^)]*\)|（[^）]*）)"
    )
    products = {}
    for r in reversed(header):
        # 检测 span 行：>=60% 的非空 cell 文本相同 → 跨列标签行，跳过
        # 但不跳过含合计/年份的行（它们是真正的表头行）
        non_empty_cells = [str(r[c] or "").strip() for c in range(1, min(nc, len(r)))
                          if str(r[c] or "").strip()]
        if len(non_empty_cells) >= 3:
            from collections import Counter
            cell_counts = Counter(non_empty_cells)
            most_common_text, most_common_count = cell_counts.most_common(1)[0]
            # 常见重复文本是年份/日期 → 不是 span 标签，不要跳过
            is_year_like = bool(re.match(r'^(20\d{2}|二零|截至)', most_common_text))
            # 行中包含合计/总计 → 是真正的表头行
            has_total = any(re.match(r'^(合[计計]|總[计計]|小[计計]|總收入|總收益|總計|总计|合計|合计|總額|总额)', c)
                          for c in non_empty_cells)
            if not is_year_like and not has_total and most_common_count >= max(2, int(len(non_empty_cells) * 0.6)):
                # 该行大部分列文本相同，是 span 标签行（如"於六月三十日"、"未經審核"）
                continue

        for c in range(1, min(nc, len(r))):
            name = fullwidth_to_halfwidth(str(r[c] or "").strip())
            name = re.sub(r"\s+", "", name).strip()
            if not name or not re.search(r"[一-鿿A-Za-z]{2,}", name):
                continue
            # 去掉单位/日期/括号后缀
            name = _UNIT_NAME_RE.sub("", name).strip()
            if not name or not re.search(r"[一-鿿A-Za-z]{2,}", name):
                continue
            if re.match(r"^(合[计計]|總[计計]|小[计計]|總收入|总收入|總收益|总收益|"
                        r"總計|总计|合計|合计|總額|总额)", name) or \
               re.search(r"(總計|总计|合計|合计|總額|总额|總收益|总收益|總收入|总收入)$", name):
                name = "合计"
            # 抵銷等保留原样（GT可能有对应条目）
            # 排除纯年份+单位
            if re.match(r"^(20\d{2}|二零)", name):
                continue
            # 排除日期横幅
            if re.match(r'^(截至|止).{0,30}(止|年度|期間|期间|个月|個月|months)', name):
                continue
            if re.search(r'\d{1,2}\s*月\s*\d{1,2}\s*日', name):
                continue
            # 排除 IFRS15 时间标签（絕不是产品名列头）
            if re.search(r'(時間點|时间点|時點|时点|時間段|时间段|時段|时段|'
                        r'隨時間|随时间|一段時間|一段时间|'
                        r'於某|于某|在某|某一時|某個時)', name) and \
               len(name) <= 15 and not re.search(r'(服務|服务|產品|产品|銷售|销售|收入|收益)$', name):
                continue
            # 水平续名拼接：当前cell以"及/與/和/、"开头且左列已有产品名 → 拼到左列
            if re.match(r'^[及與和、/]', name) and c > 1 and (c - 1) in products:
                left_name = products[c - 1]
                combined = left_name + name
                # 检查左列是否在同一header行也被更新过（避免覆盖刚拼好的名）
                # 将拼接结果放到左列，当前列也记录以便后续引用
                products[c - 1] = combined
                products[c] = name  # 暂时记录续名部分，后续可能被上层行覆盖
                continue

            # 若已有值且是 span 文本（全列同文），允许覆盖
            # span 文本特征：长 >15 字，或含特定关键词
            if c in products:
                old = products[c]
                # 续名拼接：下层是"及休閒"开头的续名 → 上层"健康醫療"拼接为"健康醫療及休閒"
                if re.match(r'^[及與和、/]', old):
                    products[c] = name + old
                    continue
                if len(old) > 15 or re.search(r'分部|業績|资产|负债|資產|負債|計量|计量', old):
                    pass  # 覆盖 span 文本
                else:
                    continue
            products[c] = name

    # 1b. 兜底：header 没产品或全是度量标签 → 从 body 前2行 columns 1+ 补搜列产品名
    if not products or all(_is_metric_subheader_cell(v) for v in products.values()):
        for r in body[:2]:
            for c in range(1, min(nc, len(r))):
                if c in products:
                    continue
                name = fullwidth_to_halfwidth(str(r[c] or "").strip())
                name = re.sub(r"\s+", "", name).strip()
                if not name or not re.search(r"[一-鿿A-Za-z]{2,}", name):
                    continue
                name = _UNIT_NAME_RE.sub("", name).strip()
                if not name or not re.search(r"[一-鿿A-Za-z]{2,}", name):
                    continue
                if _is_metric_subheader_cell(name):
                    continue
                if _PL_ROW_LABEL_RE.search(name):
                    continue
                if re.match(r"^(合[计計]|總[计計]|小[计計])", name):
                    name = "合计"
                products[c] = name

    # 2. 从表身找收入行：优先外部/對外收入行，再兜底通用收入行
    revenue_row = None
    if revenue_basis:
        basis_re = {
            "report": re.compile(r"報告分部收入|报告分部收入|可報告分部收益|可报告分部收益"),
            "group": re.compile(r"^(集團收入|集团收入|合併收入|合并收入)$"),
            "operating": re.compile(r"^(營業收入|营业收入|經營收入|经营收入)$"),
            "segment": re.compile(r"^分部收入$"),
            "external": re.compile(r"外部|外界|對外|对外|來自.*客户|来自.*客户"),
        }.get(revenue_basis, re.compile(r"外部|外界|對外|对外"))
        basis_candidates = []
        for r in body:
            label = fullwidth_to_halfwidth(str(r[0] or "").strip())
            if not basis_re.search(label):
                continue
            valid_cols = sum(
                1 for c in r[1:]
                if str(c or "").strip() not in ("", "-")
            )
            if valid_cols:
                basis_candidates.append((valid_cols, r))
        if basis_candidates:
            revenue_row = max(basis_candidates, key=lambda x: x[0])[1]
    # Pass 1: 外部/對外客户收入行（最精确）
    _REV_EXT = re.compile(r'對外交易|对外交易|外部客户|外部客戶|來自外部|来自外部|外界客户|外界客戶')
    for r in body:
        lab = fullwidth_to_halfwidth(str(r[0] or "").strip())
        if _REV_EXT.search(lab) and not re.search(r"成本|费用|費用|開支|开支|税|稅|利息", lab):
            if any(re.search(r"\d", str(r[c] or "")) for c in range(1, len(r))):
                revenue_row = r
                break
    # Pass 2: 通用收入/收益行（优先简单标签，避免"其他營業收入"类子项）
    if not revenue_row:
        _REV_GEN = re.compile(r"收入|收益|銷售|销售|營業|营业|Revenue")
        _REV_SUBITEM = re.compile(r'其他|其它|其中')
        candidates = []
        for r in body:
            lab = fullwidth_to_halfwidth(str(r[0] or "").strip())
            if _REV_GEN.search(lab) and not re.search(r"成本|费用|費用|開支|开支|税|稅|利息", lab):
                valid_cols = sum(1 for c in range(1, len(r))
                               if str(r[c] or "").strip() and str(r[c] or "").strip() != "-")
                if valid_cols > 0:
                    # 评分：非子项标签优先、更短标签优先、更多有效列优先
                    is_sub = bool(_REV_SUBITEM.search(lab))
                    score = (0 if not is_sub else 1, len(lab), -valid_cols)
                    candidates.append((score, r))
        if candidates:
            candidates.sort(key=lambda x: x[0])
            revenue_row = candidates[0][1]
    # Pass 3: 从所有候选收入行中选最优
    # 优先：對外/外部行（IFRS 8要求披露外部客户收入）
    if not revenue_row:
        _REV_ANY = re.compile(r"收入|收益|銷售|销售|營業|营业|Revenue|對外|对外", re.I)
        _REV_EXT = re.compile(r'對外交易|对外交易|外部客户|外部客戶|來自外部|来自外部|外界客户', re.I)
        ext_candidates = []
        gen_candidates = []
        for r in body:
            lab = fullwidth_to_halfwidth(str(r[0] or "").strip())
            if _REV_ANY.search(lab) and not re.search(r"成本|费用|費用|開支|开支|税|稅|利息", lab):
                valid_cols = sum(1 for c in range(1, len(r))
                               if str(r[c] or "").strip() and str(r[c] or "").strip() != "-")
                if valid_cols > 0:
                    if _REV_EXT.search(lab):
                        ext_candidates.append((valid_cols, r))
                    else:
                        gen_candidates.append((valid_cols, r))
        # 优先选外部行（IFRS8要求），再选通用收入行
        # 但若通用收入行包含「總收益/總收入/收益總額」且有效列数与外部行接近，优先选总额行
        # （GT 通常用经分部间抵销后的總收益，而非外部客户收益）
        _REV_TOTAL = re.compile(r'總收益|总收益|總收入|总收入|收益總額|收益总额|收入總額|收入总额')
        if ext_candidates:
            best_ext_cols, best_ext_row = max(ext_candidates, key=lambda x: x[0])
            total_candidates = [(cols, r) for cols, r in gen_candidates
                              if _REV_TOTAL.search(fullwidth_to_halfwidth(str(r[0] or "").strip()))]
            if total_candidates:
                best_total_cols, best_total_row = max(total_candidates, key=lambda x: x[0])
                # 总额行的有效列数 >= 外部行有效列数的 60%，说明是完整的总收益行
                if best_total_cols >= max(1, int(best_ext_cols * 0.6)):
                    revenue_row = best_total_row
                else:
                    revenue_row = best_ext_row
            else:
                revenue_row = best_ext_row
        elif gen_candidates:
            revenue_row = max(gen_candidates, key=lambda x: x[0])[1]

    # Pass 3.5: 验证所选收入行的有效列数是否合理（>= 产品列数的 50%）
    if revenue_row and products:
        n_products = len(products)
        n_valid = sum(1 for c in range(1, min(len(revenue_row), nc))
                     if str(revenue_row[c] or "").strip() and str(revenue_row[c] or "").strip() != "-")
        if n_valid < max(1, int(n_products * 0.5)):
            # 所选行有效值太少，可能是子项行，尝试找更好的候选
            _REV_ANY = re.compile(r"收入|收益|銷售|销售|營業|营业|Revenue|對外|对外", re.I)
            _REV_EXT = re.compile(r'對外交易|对外交易|外部客户|外部客戶|來自外部|来自外部|外界客户', re.I)
            better_candidates = []
            for r in body:
                if r is revenue_row:
                    continue
                lab = fullwidth_to_halfwidth(str(r[0] or "").strip())
                if _REV_ANY.search(lab) and not re.search(r"成本|费用|費用|開支|开支|税|稅|利息", lab):
                    valid_cols = sum(1 for c in range(1, len(r))
                                   if str(r[c] or "").strip() and str(r[c] or "").strip() != "-")
                    if valid_cols > n_valid:
                        better_candidates.append((valid_cols, r))
            if better_candidates:
                better_candidates.sort(key=lambda x: -x[0])
                revenue_row = better_candidates[0][1]
    # Pass 4: 兜底 — 优先空col0合计行，再选最多有效列的行
    if not revenue_row:
        best = None
        best_score = (-1, -1)
        for r in body:
            lab = fullwidth_to_halfwidth(str(r[0] or "").strip())
            is_total_row = (not lab)  # 空col0 = 合计行，最可靠
            valid_cols = sum(1 for c in range(1, len(r))
                           if str(r[c] or "").strip() and str(r[c] or "").strip() != "-")
            if valid_cols == 0:
                continue
            # 合计行优先，其次有效列多的
            score = (1 if is_total_row else 0, valid_cols)
            if score > best_score:
                best_score = score
                best = r
        if best:
            revenue_row = best

    if not revenue_row or not products:
        return []

    # 检测多年度块表：body中有多个带年份+收入关键词的行（如"2025年收入"+"2024年收入"）
    revenue_rows = [revenue_row]
    year_label_re = re.compile(r'(20\d{2}|二零[一二三四五六七八九零]{2})年?')
    _YEAR_REV_RE = re.compile(r'(20\d{2}|二零).{0,6}(收入|收益|Revenue)', re.I)
    primary_year = year_label_re.search(fullwidth_to_halfwidth(str(revenue_row[0] or '')))
    if primary_year and _YEAR_REV_RE.search(fullwidth_to_halfwidth(str(revenue_row[0] or ''))):
        seen_years = {primary_year.group(0)}
        for r in body:
            if r is revenue_row:
                continue
            lab = fullwidth_to_halfwidth(str(r[0] or "").strip())
            yr_match = year_label_re.search(lab)
            if yr_match and yr_match.group(0) not in seen_years:
                # 必须也像收入行（含收入关键词或金额充足）
                if _YEAR_REV_RE.search(lab) or sum(1 for c in r[1:] if re.search(r'\d', str(c or ''))) >= 2:
                    revenue_rows.append(r)
                    seen_years.add(yr_match.group(0))

    # 3. 上期产品名只用于 合计 补全判断，不过滤产品（允许新产品出现）
    lp_names = [fullwidth_to_halfwidth(str(r.get("PRODUCTNAME", "")).strip())
                for r in (last_period_data or []) if isinstance(r, dict)]

    # 4. 提取（支持多收入行）
    out = []
    periods = _find_periods(table)
    for row_idx, rev_row in enumerate(revenue_rows):
        for col, prod_name in products.items():
            if col >= len(rev_row):
                continue
            v = str(rev_row[col] or "").strip()
            if not v or v == "-":
                continue

            if periods:
                # 如果多收入行，期间检测与行关联（同一列，每行匹配其对应年份）
                if len(revenue_rows) > 1:
                    row_year = year_label_re.search(fullwidth_to_halfwidth(str(rev_row[0] or '')))
                    for pc, y, sd, ed in periods:
                        if pc == col:
                            # 仅匹配该行对应年份的期间
                            if row_year and y in row_year.group(0):
                                out.append({"product_name": prod_name, "mbrevenue": v,
                                            "start_date": sd, "end_date": ed, "year": y})
                            elif not row_year:
                                out.append({"product_name": prod_name, "mbrevenue": v,
                                            "start_date": sd, "end_date": ed, "year": y})
                else:
                    for pc, y, sd, ed in periods:
                        if pc == col:
                            out.append({"product_name": prod_name, "mbrevenue": v,
                                        "start_date": sd, "end_date": ed, "year": y})
            else:
                out.append({"product_name": prod_name, "mbrevenue": v,
                            "start_date": "", "end_date": "", "year": ""})

    # 自动补合计
    lp_has_total = any(str(r.get("PRODUCTNAME", "")).strip() == "合计"
                       for r in (last_period_data or []) if isinstance(r, dict))
    if not any(e["product_name"] == "合计" for e in out):
        if lp_has_total or len(products) >= 2:
            period_sums = {}
            product_names = set()
            for e in out:
                if e["product_name"] != "合计" and e["mbrevenue"]:
                    key = (e["start_date"], e["end_date"])
                    try:
                        fv = float(str(e["mbrevenue"]).replace(",", "").replace("(", "-").replace(")", ""))
                    except (ValueError, TypeError):
                        continue
                    period_sums[key] = period_sums.get(key, 0) + fv
                    product_names.add(e["product_name"])
            if len(product_names) >= 2:
                for (sd, ed), total in period_sums.items():
                    out.append({"product_name": "合计", "mbrevenue": str(int(total)),
                                "start_date": sd, "end_date": ed,
                                "year": sd[:4] if sd else ed[:4] if ed else ""})
            # 单产品 → 复制产品值作为合计（GT 几乎总会有合计）
            elif len(product_names) == 1:
                for e in out:
                    if e["product_name"] != "合计":
                        out.append({"product_name": "合计", "mbrevenue": e["mbrevenue"],
                                    "start_date": e["start_date"], "end_date": e["end_date"],
                                    "year": e["year"]})

    # 值级别去重：同一 (product, period) 保留数值最大的
    if len(out) > 1:
        groups = {}
        for e in out:
            key = (e['product_name'], e['start_date'], e['end_date'])
            groups.setdefault(key, []).append(e)
        deduped = []
        for key, entries in groups.items():
            if len(entries) == 1:
                deduped.append(entries[0])
            else:
                best = max(entries, key=lambda x: abs(_to_num(x['mbrevenue']) or 0))
                deduped.append(best)
        out = deduped

    return out


def _map_product_name_to_lp(name, last_period_data=None):
    """短表头名唯一命中 LP 后缀时，恢复完整产品名。"""
    raw = fullwidth_to_halfwidth(str(name or "").strip())
    if not raw or raw == "合计":
        return raw
    names = [
        fullwidth_to_halfwidth(str(x.get("PRODUCTNAME", "") or "").strip())
        for x in (last_period_data or []) if isinstance(x, dict)
        and str(x.get("PRODUCTNAME", "") or "").strip() not in ("", "合计", "合計")
    ]
    exact = [n for n in names if n == raw]
    if exact:
        return exact[0]
    suffix = [n for n in names if len(raw) >= 2 and n.endswith(raw)]
    return suffix[0] if len(suffix) == 1 else raw


def extract_type2_first_cell_product(table, last_period_data=None, revenue_basis="external"):
    """列产品专类：产品表头首格就是产品，整体对应金额列 +1。"""
    width = max((len(row) for row in table if isinstance(row, list)), default=0)
    normalized = []
    for row in table:
        copied = list(row) if isinstance(row, list) else row
        if (isinstance(copied, list)
                and _shifted_product_header_row(copied, width, last_period_data)):
            copied = [""] + copied
            for index in range(1, len(copied)):
                header_name = _clean_header_product(copied[index])
                if re.search(r"分部小[計计]|小[計计]$|總[計计]$|总[計计]$|^(集團|集团|綜合|综合)$", header_name):
                    copied[index] = "合计"
        normalized.append(copied)
    rows = extract_type2(normalized, last_period_data, revenue_basis=revenue_basis)
    for item in rows:
        name = str(item.get("product_name", "") or "").strip()
        if re.search(r"分部小[計计]|小[計计]$|總[計计]$|总[計计]$|^(集團|集团|綜合|综合)$", name):
            item["product_name"] = "合计"
        else:
            item["product_name"] = _map_product_name_to_lp(name, last_period_data)
    # 表内「分部小计」和「总计」可能是同值同期间，只保留一条合计。
    deduped = {}
    for item in rows:
        key = (item.get("product_name"), item.get("start_date"), item.get("end_date"))
        old = deduped.get(key)
        # 首格产品专类中，显式「综合/集团」列会先被通用逻辑当产品参与自动求和，
        # 随后又映射成合计，形成“显式总额”和“包含总额再求和”的两条记录。
        # 同期多个合计时，较小者是表内显式总额；普通产品仍保留绝对值较大者。
        prefer = (
            old is None
            or (key[0] == "合计" and 0 < abs(_to_num(item.get("mbrevenue")) or 0)
                < abs(_to_num(old.get("mbrevenue")) or 0))
            or (key[0] != "合计" and abs(_to_num(item.get("mbrevenue")) or 0)
                > abs(_to_num(old.get("mbrevenue")) or 0))
        )
        if prefer:
            deduped[key] = item
    return list(deduped.values())


def extract_type2_total_row_matrix(table, last_period_data=None):
    """列产品收入确认矩阵：每个期间块以显式总计行作为产品收入。"""
    rows = [list(row) for row in (table or []) if isinstance(row, list)]
    if not rows:
        return []

    # 当前期间是首个完整块；块内可能先列商品/服务类别，再列确认时点，
    # 两个区段均以总计收束，取第一个显式总计即可避免重复累计。
    total_index = None
    for index, row in enumerate(rows):
        label = fullwidth_to_halfwidth(str(row[0] or "").strip()) if row else ""
        if (re.match(r"^(總計|总计|合計|合计|收入總額|收入总额|收益總額|收益总额)$", label)
                and any(_amount_value(cell) is not None for cell in row[1:])):
            total_index = index
            break
    if total_index is None:
        return extract_type2(rows, last_period_data, revenue_basis="external")

    selected = rows[:total_index + 1]
    selected[-1][0] = "外部客户收入"
    out = extract_type2(selected, last_period_data, revenue_basis="external")

    # 表头可能用简称（CDMO），本期明细行给出完整产品名（CDMO服務）。
    # 仅在“简称开头 + 对应产品列有金额”唯一成立时扩展名称。
    header = next((row for row in selected[:6]
                   if sum(bool(_clean_header_product(cell)) for cell in row[1:]) >= 2), None)
    if header:
        expansions = {}
        for col in range(1, len(header)):
            short = _clean_header_product(header[col])
            if not short:
                continue
            candidates = []
            for row in selected[1:-1]:
                label = fullwidth_to_halfwidth(str(row[0] or "").strip()) if row else ""
                if (len(label) > len(short) and label.startswith(short) and col < len(row)
                        and _amount_value(row[col]) not in (None, 0)):
                    candidates.append(label)
            if len(set(candidates)) == 1:
                expansions[short] = candidates[0]
        for item in out:
            name = str(item.get("product_name", "") or "").strip()
            if name in expansions:
                item["product_name"] = expansions[name]
    return out


# 损益表 — 检测嵌入式产品并提取
# 损益_单产品 — LP 只有一个产品，取第一个收益行，切在銷售成本
# 百度 — 多期间块列产品表
def extract_type5(table, last_period_data=None):
    """百度多期间块表：body[0]=产品列头, body[1]=收入行, 产品名重复4次(4个期间块)"""
    hdr, body = _split_header_body(table)
    periods = _find_periods(table)
    if len(body) < 2:
        return []

    # 产品名从 body[0] 取，body[1] 是收入行
    header_row = body[0]
    revenue_row = body[1]
    nc = max(len(header_row), len(revenue_row))
    lp_names = [fullwidth_to_halfwidth(str(r.get("PRODUCTNAME", "").strip()))
                for r in (last_period_data or [])
                if isinstance(r, dict) and str(r.get("PRODUCTNAME", "").strip()) != "合计"]

    # 识别哪些列是产品列
    col_product = {}   # col_idx → product_name
    for c in range(1, min(nc, len(header_row))):
        name = fullwidth_to_halfwidth(str(header_row[c] or "").strip())
        name = re.sub(r"\s+", "", name)
        name = re.sub(r'[（(]\s*\d+\s*[）)]', '', name).strip()
        if not name or not re.search(r"[一-鿿A-Za-z]", name):
            continue
        if re.match(r'^(百度集團|百度集团)', name):
            continue  # 合计列，跳过
        if any(_lp_name_in_cell(n, name) for n in lp_names):
            col_product[c] = name

    if not col_product:
        return []

    out = []
    # 当 header_row[0] 是产品名时，收入行有 +1 偏移（收入行 col0="收入"占位）
    val_offset = 1 if (header_row[0] and re.search(r'[一-鿿A-Za-z]', str(header_row[0] or ''))
                       and not _is_metric_subheader_cell(str(header_row[0] or ''))) else 0
    for col, prod_name in col_product.items():
        val_col = col + val_offset
        if val_col >= len(revenue_row):
            continue
        v = str(revenue_row[val_col] or "").strip()
        if not v or v == "-":
            continue
        for pc, y, sd, ed in periods:
            if pc == val_col:
                out.append({"product_name": prod_name, "mbrevenue": v,
                            "start_date": sd, "end_date": ed, "year": y})

    # 值级别去重：每 (product, period) 保留绝对值最大的（防 USD 块混入）
    if out:
        groups = {}
        for e in out:
            key = (e["product_name"], e["start_date"], e["end_date"])
            groups.setdefault(key, []).append(e)
        out = [max(entries, key=lambda x: abs(_to_num(x["mbrevenue"]) or 0))
               for entries in groups.values()]

    # 自动补合计
    if len(out) >= 2 and not any(e["product_name"] == "合计" for e in out):
        period_sums = {}
        for e in out:
            if e["product_name"] != "合计":
                key = (e["start_date"], e["end_date"])
                fv = _to_num(e["mbrevenue"])
                if fv is not None:
                    period_sums[key] = period_sums.get(key, 0) + fv
        for (sd, ed), total in period_sums.items():
            out.append({"product_name": "合计", "mbrevenue": str(int(total)),
                        "start_date": sd, "end_date": ed,
                        "year": sd[:4] if sd else ed[:4] if ed else ""})

    return out


def _to_num(v):
    s = str(v).replace(",", "").replace("(", "-").replace(")", "")
    try: return float(s)
    except: return None


def extract_type4(table, last_period_data=None):
    """单产品 P&L：LP 给产品名，表里取收益行、切成本段。"""
    hdr, body = _split_header_body(table)
    periods = _find_periods(table)
    if not body:
        return []

    lp_names = [fullwidth_to_halfwidth(str(r.get("PRODUCTNAME", "").strip()))
                for r in (last_period_data or [])
                if isinstance(r, dict) and str(r.get("PRODUCTNAME", "")).strip() != "合计"]
    product_name = lp_names[0] if lp_names else "本集團"

    # 找收益行：第一个匹配收入/收益/營業額且有金额的行
    _REV = re.compile(r'^(收益|收入|營業額|营业额|營業收入|营业收入|Revenue)', re.I)
    _COST = re.compile(r'^(銷售成本|销售成本|服務成本|服务成本|營業成本|营业成本)')
    revenue_row = None
    for row in body:
        if not isinstance(row, list) or len(row) < 2:
            continue
        lab = fullwidth_to_halfwidth(str(row[0] or '').strip())
        if _REV.search(lab):
            if any(re.search(r'\d', str(c or '')) for c in row[1:]):
                revenue_row = row
                break

    if revenue_row is None:
        # 回退：第一个有金额、不像成本的行
        for row in body:
            lab = fullwidth_to_halfwidth(str(row[0] or '').strip())
            if _COST.search(lab):
                continue
            if any(re.search(r'\d', str(c or '')) for c in row[1:]):
                revenue_row = row
                break

    if revenue_row is None:
        return []

    rows = [item for item in _format_type3_output(revenue_row, periods, product_name)
            if _to_num(item.get("mbrevenue")) is not None]
    if rows and any(str(x.get("PRODUCTNAME", "")).strip() in ("合计", "合計")
                    for x in (last_period_data or []) if isinstance(x, dict)):
        rows.extend({**item, "product_name": "合计"} for item in list(rows))
    return rows


def extract_type4_note_column(table, last_period_data=None):
    """单产品损益专类：科目后第一列为附注编号。"""
    _hdr, body = _split_header_body(table)
    periods = [p for p in _find_periods(table) if p[0] >= 2]
    lp_names = [fullwidth_to_halfwidth(str(r.get("PRODUCTNAME", "").strip()))
                for r in (last_period_data or [])
                if isinstance(r, dict) and str(r.get("PRODUCTNAME", "")).strip() != "合计"]
    product_name = lp_names[0] if lp_names else "本集團"
    rev_re = re.compile(r"^(收益|收入|營業額|营业额|營業收入|营业收入|Revenue)", re.I)
    revenue_row = next(
        (row for row in body if isinstance(row, list) and len(row) >= 3
         and rev_re.search(fullwidth_to_halfwidth(str(row[0] or "").strip()))
         and any(re.search(r"\d", str(c or "")) for c in row[2:])),
        None,
    )
    if revenue_row is None:
        return []
    rows = _format_type3_output(revenue_row, periods, product_name)
    if rows and any(str(x.get("PRODUCTNAME", "")).strip() in ("合计", "合計")
                    for x in (last_period_data or []) if isinstance(x, dict)):
        rows.extend({**item, "product_name": "合计"} for item in list(rows))
    return rows


def extract_type3(table, last_period_data=None):
    """损益表：先尝试提取嵌入的产品行（如携程的住宿预订/交通票务等），
    如果没有产品明细则回退到 LP 产品名（或本集團）。"""
    hdr, body = _split_header_body(table)
    periods = _find_periods(table)

    # LP 产品名（用于单产品 P&L 回退）
    lp_names = [fullwidth_to_halfwidth(str(r.get("PRODUCTNAME", "")).strip())
                for r in (last_period_data or [])
                if isinstance(r, dict) and str(r.get("PRODUCTNAME", "")).strip() != "合计"]
    fallback_name = lp_names[0] if lp_names else "本集團"

    if not body:
        return []

    # 1. 找到收入段
    # 注意：收入 section header（"收入:"）可能被 _split_header_body 分到表头
    # 所以需要在表头尾部中一起搜索

    _REVENUE_START = re.compile(
        r'^(收入|收益|營業額|营业额|營業收入|营业收入|'
        r'銷售收入|销售收入|經營收入|经营收入|Revenue)[：:]*$')
    _REVENUE_END = re.compile(
        r'^(收入合計|收入合计|收益合計|收益合计|'
        r'收入總額|收入总额|收益總額|收益总额|'
        r'淨收入|净收入|淨收入總額|净收入总额|'
        r'總收入|总收入|總收益|总收益|'
        r'合[计計]|總[计計]|總額|总额|'
        r'銷售成本|销售成本|服務成本|服务成本|營業成本|营业成本|'
        r'產品收入的成本|合作收入的成本|'
        r'毛利$|毛利:|營業費用|营业费用|研發|研发|'
        r'銷售及|销售及|行政開支|行政开支|一般及|'
        r'撮合.*發起|撮合.*发起|融資成本|融资成本|'
        r'銷售及營銷|销售及营销|應收.*撥備|应收.*拨备|'
        r'運營成本|运营成本|經營收益|经营收益|'
        r'利息收入|利息支出|利息開支|利息开支|'
        r'銷售成本總額|销售成本总额|營業費用總額|营业费用总额)$')
    _COST_EXPENSE = re.compile(
        r'成本|費用|费用|開支|开支|毛利|研發|研发|'
        r'融資|融资|利息|所得稅|所得税|稅前|税前')

    # 合并最后几个表头行和所有表身行来搜索
    search_rows = list(hdr[-3:]) + list(body) if hdr else list(body)

    # 在表头找 section header（"收入:"）
    rev_start_in_hdr = False
    for row in hdr:
        if not isinstance(row, list) or len(row) < 2:
            continue
        lab = fullwidth_to_halfwidth(str(row[0] or '').strip())
        lab_flat = lab.replace('\n', ' ').replace('\r', ' ').strip()
        if _REVENUE_START.search(lab_flat):
            rev_start_in_hdr = True
            break

    rev_start_idx = None
    rev_end_idx = None
    for i, row in enumerate(body):
        if not isinstance(row, list) or len(row) < 2:
            continue
        lab = fullwidth_to_halfwidth(str(row[0] or '').strip())
        lab_flat = lab.replace('\n', ' ').replace('\r', ' ')

        if rev_start_idx is None:
            # 如果 section header 在表头，从第一个有数据的 body 行开始
            if rev_start_in_hdr:
                if any(re.search(r'\d', str(c or '')) for c in row[1:]):
                    rev_start_idx = i
            elif _REVENUE_START.search(lab_flat):
                has_data = any(re.search(r'\d', str(c or '')) for c in row[1:])
                if has_data:
                    rev_start_idx = i
                elif i + 1 < len(body):
                    next_row = body[i + 1]
                    if isinstance(next_row, list) and len(next_row) > 1:
                        if any(re.search(r'\d', str(c or '')) for c in next_row[1:]):
                            rev_start_idx = i
        elif _REVENUE_END.search(lab):
            rev_end_idx = i
            break

    if rev_start_idx is None:
        # 兜底：用 LP 产品名在 body 前几行找产品段起点
        if lp_names:
            for i, row in enumerate(body):
                if not isinstance(row, list) or len(row) < 2:
                    continue
                lab = fullwidth_to_halfwidth(str(row[0] or '').strip())
                lab_flat = lab.replace('\n', ' ').replace('\r', ' ')
                if any(_lp_name_in_cell(n, lab_flat) for n in lp_names):
                    rev_start_idx = i
                    break
        if rev_start_idx is None:
            return []

    if rev_end_idx is None:
        rev_end_idx = len(body)

    revenue_section = body[rev_start_idx:rev_end_idx]

    # 2. 过滤：只保留产品行（排除 section header、合计、P&L noise）
    _SECTION_HEADER = re.compile(
        r'^(收入|收益|營業額|营业额|營業收入|营业收入|'
        r'銷售收入|销售收入|經營收入|经营收入|Revenue|淨收入|净收入)[：:]*$')
    _SUB_HEADER = re.compile(
        r'^(營業費用|营业费用|其他收入|其他收益|其他營運|其他营运|'
        r'銷售成本|销售成本|服務成本|服务成本|營業成本|营业成本|'
        r'成本及費用|成本及费用)[：:]*$')

    product_rows = []
    for row in revenue_section:
        if not isinstance(row, list) or len(row) < 2:
            continue
        lab = fullwidth_to_halfwidth(str(row[0] or '').strip())
        lab_flat = lab.replace('\n', ' ').replace('\r', ' ').strip()
        if not lab_flat:
            continue

        # 跳过 section header（如果它有值则保留，因为是单产品公司）
        if _SECTION_HEADER.search(lab_flat):
            has_vals = any(re.search(r'\d', str(c or '')) for c in row[1:])
            if has_vals:
                product_rows.append(row)
            continue

        # 跳过子表头
        if _SUB_HEADER.search(lab_flat):
            continue

        # 跳过合计/净收入行
        if re.match(r'^(收入合計|收入合计|收益合計|收益合计|'
                     r'收入總額|收入总额|收益總額|收益总额|'
                     r'淨收入|净收入|合[计計]|總[计計])', lab_flat):
            continue

        # 跳过 P&L 噪声行
        if _is_noise_label(lab_flat):
            continue
        if _PL_ROW_LABEL_RE.search(lab_flat):
            continue
        if _COST_EXPENSE.search(lab_flat):
            continue

        # 有数据 → 产品行
        if any(re.search(r'\d', str(c or '')) for c in row[1:]):
            product_rows.append(row)

    # 3. 安全检查：如果 revenue section 太长（>10行）或没找到明确的结束标记
    # → P&L 表没有独立的产品段，回退到本集團
    if len(revenue_section) > 10 or rev_end_idx is None:
        product_rows = []

    # 4. 如果只有1个或没有产品行 → 回退到本集團
    if len(product_rows) <= 1:
        for row in revenue_section:
            if not isinstance(row, list) or len(row) < 2:
                continue
            lab = fullwidth_to_halfwidth(str(row[0] or '').strip())
            lab_flat = lab.replace('\n', ' ').replace('\r', ' ')
            if not lab_flat:
                continue
            # 跳过子表头和无数据行
            if _SUB_HEADER.search(lab_flat):
                continue
            if _SECTION_HEADER.search(lab_flat) or _REVENUE_END.search(lab_flat):
                has_vals = any(re.search(r'\d', str(c or '')) for c in row[1:])
                if has_vals:
                    return _format_type3_output(row, periods, fallback_name)
            elif _is_noise_label(lab_flat) or _COST_EXPENSE.search(lab_flat):
                continue
            has_vals = any(re.search(r'\d', str(c or '')) for c in row[1:])
            if has_vals:
                return _format_type3_output(row, periods, fallback_name)
        # 没找到合适的行 → 回退
        return _format_type3_output(revenue_section[0], periods, fallback_name)

    # 4. 多产品行 → 逐个提取
    out = []
    for row in product_rows:
        name = fullwidth_to_halfwidth(str(row[0] or '').strip())
        name = _clean_product_name(name)
        out.extend(_format_type3_output(row, periods, name))

    # 自动补合计
    if len(product_rows) >= 2 and not any(e['product_name'] == '合计' for e in out):
        period_sums = {}
        prod_names = set()
        for e in out:
            if e['product_name'] != '合计':
                key = (e['start_date'], e['end_date'])
                try:
                    fv = float(str(e['mbrevenue']).replace(',', '').replace('(', '-').replace(')', ''))
                except (ValueError, TypeError):
                    continue
                period_sums[key] = period_sums.get(key, 0) + fv
                prod_names.add(e['product_name'])
        if len(prod_names) >= 2:
            for (sd, ed), total in period_sums.items():
                out.append({'product_name': '合计', 'mbrevenue': str(int(total)),
                            'start_date': sd, 'end_date': ed,
                            'year': sd[:4] if sd else ed[:4] if ed else ''})

    # 值级别去重：同一 (product, period) 保留数值最大的（解决CNY/USD列重复）
    if len(out) > 1:
        groups = {}
        for e in out:
            key = (e['product_name'], e['start_date'], e['end_date'])
            groups.setdefault(key, []).append(e)
        deduped = []
        for key, entries in groups.items():
            if len(entries) == 1:
                deduped.append(entries[0])
            else:
                best = max(entries, key=lambda x: abs(_to_num(x['mbrevenue']) or 0))
                deduped.append(best)
        out = deduped

    return out


def _format_type3_output(row, periods, product_name):
    """从单行提取格式化的输出列表."""
    out = []
    # 检测附注/脚注编号列：col2有金额且col1是1位纯数字+col0无数字→跳过col1(footnote ref)
    first_val_col = 1
    if len(row) > 2:
        c0 = str(row[0] or '').strip()
        c1 = str(row[1] or '').strip()
        c2 = str(row[2] or '').strip()
        if (c1 and re.fullmatch(r'\d', c1) and c2 and re.search(r'\d', c2)
                and not re.search(r'\d', c0)):
            first_val_col = 2
    if periods:
        for col, _y, sd, ed in periods:
            if col >= len(row):
                continue
            v = str(row[col] or '').strip()
            if v and re.search(r'\d', v):
                out.append({'product_name': product_name, 'mbrevenue': v,
                            'start_date': sd, 'end_date': ed,
                            'year': _y})
    else:
        for c in range(first_val_col, len(row)):
            v = str(row[c] or '').strip()
            if v and re.search(r'\d', v):
                out.append({'product_name': product_name, 'mbrevenue': v,
                            'start_date': '', 'end_date': '', 'year': ''})
                break
    return out
