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


def classify_table(table, title, last_period_data, page_lines=None):
    """返回表类型字符串

    分类层次：
    1. 先排错表（免责声明、公司名、MD&A等）
    2. 损益表（防止"综合收益表"被收入/收益误截）
    3. 分部表，收入/收益表，结构兜底
    """
    row = is_row_product(table, last_period_data)
    t = str(title or "").strip()

    # ── 排除明显的错表 ──
    # 附注/脚注（不是表格标题）
    if re.match(r'^(附註|附注|Note\s*\d)', t):
        return ""
    # 标题过短且无产品/分部关键词（不是有效表格标题）
    if len(t) <= 2 and not re.search(r'產品|产品|分部|收入|收益|服務|服务|業務|业务', t):
        return ""

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
                return "损益_单产品"
            return "损益"

    # ── 百度多期间块表 ──
    if page_lines:
        pl_text = " ".join(str(l.get("content", "")) for l in page_lines if isinstance(l, dict))
        if "百度集團股份有限公司" in pl_text:
            return "百度"

    # ── 分部表 ──
    if ('分部' in t or '經營分部' in t or '经营分部' in t
            or '可呈報分部' in t or '可报告分部' in t
            or '可呈报分部' in t or '可報告分部' in t
            or re.search(r'分部[资料料信息報告报告业绩業績分析]', t)):
        if row:
            return "分部_行产品"
        else:
            return "分部_列产品"

    # ── 收入/收益表 ──
    if '收入' in t or '收益' in t:
        if row:
            return "收入_行产品"
        else:
            return "收入_列产品"

    # ── 结构兜底 ──
    if row:
        typ = "收入_行产品"
    else:
        typ = "收入_列产品"

    # 检测成本段：如果前面有产品行、后面有成本标记 → 标记含成本段
    if typ.endswith("行产品") or typ.endswith("列产品"):
        if _has_cost_section(table):
            typ = typ + "_含成本段"

    return typ


# 成本段标记（绝不可能是产品名的P&L行）
_COST_SECTION_RE = re.compile(
    r'^(銷售成本|销售成本|服務成本|服务成本|營業成本|营业成本)'
    r'[：:總总]|'
    r'^(毛利$|營業費用|营业费用|營業費用總額|营业费用总额|'
    r'經營開支|经营开支|經營開支總額|经营开支总额|'
    r'經營虧損|经营亏损|經營利潤|经营利润|'
    r'所得稅開支|所得税开支|所得稅費用|所得税费用|'
    r'行政開支|行政开支|銷售及分銷|销售及分销)$')


def _has_cost_section(table):
    """检测表中是否有成本段（收入行后出现成本/费用等标记）。"""
    if not isinstance(table, list) or len(table) < 3:
        return False
    has_product = False
    for row in table:
        if not isinstance(row, list) or len(row) < 1:
            continue
        c0 = str(row[0] or '').strip()
        if _COST_SECTION_RE.search(c0):
            if has_product:
                return True
            return False
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
    r"客戶合約收入|客户合约收入)$"
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
            r"年內溢利|年内溢利|每股盈利|每股虧損|每股亏损)$")
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
        def _row_match(n, r):
            c0 = str(r[0] or "")
            if _lp_name_in_cell(n, c0):
                return True
            # col0 无中文 → 可能是双语表的英文名列，查 col1
            if len(r) >= 2 and not re.search(r'[一-鿿]', c0):
                c1 = str(r[1] or "")
                if _lp_name_in_cell(n, c1):
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
        # 在表头列的 cols 1+ 中匹配 → 排除有真实金额的数据行（防 body 行 col1 被当列产品）
        col_hit = sum(1 for n in names for r in rows[:6] for c in r[1:]
                      if _lp_name_in_cell(n, str(c or ""))
                      and not _row_has_real_amount(r))
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
        if _COST_SECTION_RE.search(c0):
            if has_product:
                return table[:i]
            return table  # 成本行前没产品，不截
        if re.search(r'[一-鿿]', c0) and any(re.search(r'\d', str(c or '')) for c in row[1:]):
            has_product = True
    return table


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
    if "_含成本段" in typ:
        target_table = _trim_cost_section(target_table)
        typ = typ.replace("_含成本段", "")

    if typ in ("收入_行产品", "分部_行产品"):
        rows = extract_type1(target_table, last_period_data)
    elif typ in ("收入_列产品", "分部_列产品"):
        rows = extract_type2(target_table, last_period_data)
    elif typ == "损益":
        rows = extract_type3(target_table, last_period_data)
    elif typ == "损益_单产品":
        rows = extract_type4(target_table, last_period_data)
    elif typ == "百度":
        rows = extract_type5(target_table, last_period_data)

    if not rows:
        reason_arr.append("提取为空")
        return res

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

    # 子列度量检测仅用于信息披露，不实际过滤列
    # PDF 提取中表头列对齐经常偏移，列级过滤不可靠
    # 依赖后续的值级别去重（保留最大数值 = 收入/金额）来处理多列重复
    skip_cols = set()

    # 1. 全量提取原始行
    raw = []  # [(name, [(col, sd, ed, value), ...]), ...]
    last_parent = ""  # 层级父产品名（用于 -- 前缀的子项）
    for row in data_rows:
        if not isinstance(row, list) or len(row) < 2:
            continue
        name = fullwidth_to_halfwidth(str(row[0] or "").strip())

        # 检测层级关系："--" 或 "——" 开头 → 子项，继承父产品名
        is_child = bool(re.match(r'^[-\–—]{1,3}', name))
        name = re.sub(r'^[-\–—]+', '', name).strip()

        # 清理脚注标记
        name = _clean_product_name(name)

        has_cjk = bool(re.search(r"[一-鿿A-Za-z]", name))
        if not name and any(re.search(r'\d', str(c or "")) for c in row[1:]):
            name = "合计"
        if re.match(r"^(合[计計]|總[计計]|总[计計]|小[计計]|總額|总额|"
                     r"收入總額|收入总额|收益總額|收益总额|總收入|总收入|總收益|总收益)", name):
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

        # 新产品也保留（上期数据是白名单，不是过滤器）
        raw = kept + removed

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
    if lp_raw and "合计" in lp_raw and not any(e["product_name"] == "合计" for e in out):
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

    # 每列找年份，找不到就用全表年份兜底
    out, seen = [], set()
    for c in range(1, nc):
        hdr = " ".join(str(r[c] or "") for r in padded if c < len(r))
        col_years = _extract_years_from_text(hdr)
        if not col_years:
            col_years = all_years
        for y in col_years:
            if (c, y) in seen:
                continue
            seen.add((c, y))
            import datetime, calendar
            try:
                yi = int(y)
                # Use actual month/day from the table, defaulting to 12/31
                ed_day = dd if mm != 2 else min(dd, 28)
                try:
                    ed = datetime.date(yi, mm, ed_day)
                except ValueError:
                    ed = datetime.date(yi, mm, 28)
                if half:
                    # Half-year: period starts first day of (report_month - 5)th month
                    # HK convention: Jul 1 - Dec 31, Jan 1 - Jun 30, etc.
                    start_month = mm - 5
                    start_year = yi
                    if start_month <= 0:
                        start_month += 12
                        start_year -= 1
                    sd = datetime.date(start_year, start_month, 1)
                else:
                    # Full year: start = day after previous year's report date
                    # Dec 31 → Jan 1; Jun 30 → Jul 1; Mar 31 → Apr 1
                    start_month = mm + 1
                    start_year = yi - 1
                    if start_month > 12:
                        start_month = 1
                        start_year = yi
                    sd = datetime.date(start_year, start_month, 1)
                out.append((c, y, sd.strftime("%Y-%m-%d"), ed.strftime("%Y-%m-%d")))
            except Exception:
                out.append((c, y, f"{int(y)-1}-12-31", f"{y}-12-31"))
    return out


# 收入_列产品 — 产品在列头，从表身找收入行提取
def extract_type2(table, last_period_data=None):
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
        for c in range(1, min(nc, len(r))):
            if c in products:
                continue
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

    # 2. 从表身找收入行：优先收入/收益/銷售/外部/Revenue，跳过值全空的行
    revenue_row = None
    for r in body:
        lab = fullwidth_to_halfwidth(str(r[0] or "").strip())
        if re.search(r"收入|收益|銷售|销售|營業|营业|外部|對外|对外|Revenue", lab) \
                and not re.search(r"成本|费用|費用|開支|开支|税|稅|利息|确认|時間|間", lab):
            # 验证该行确实有数字
            if any(re.search(r"\d", str(r[c] or "")) for c in range(1, len(r))):
                revenue_row = r
                break
    if not revenue_row:
        for r in body:
            if any(re.search(r"\d", str(c or "")) for c in r[1:]):
                revenue_row = r
                break

    if not revenue_row or not products:
        return []

    # 3. 上期产品名只用于 合计 补全判断，不过滤产品（允许新产品出现）
    lp_names = [fullwidth_to_halfwidth(str(r.get("PRODUCTNAME", "")).strip())
                for r in (last_period_data or []) if isinstance(r, dict)]

    # 4. 提取
    out = []
    for col, prod_name in products.items():
        if col >= len(revenue_row):
            continue
        v = str(revenue_row[col] or "").strip()
        if not v or v == "-":
            continue

        periods = _find_periods(table)
        if periods:
            for pc, y, sd, ed in periods:
                if pc == col:
                    out.append({"product_name": prod_name, "mbrevenue": v,
                                "start_date": sd, "end_date": ed,
                                "year": y})
        else:
            out.append({"product_name": prod_name, "mbrevenue": v,
                        "start_date": "", "end_date": "",
                        "year": ""})

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
            # 单产品 + lp有合计 → 复制产品值作为合计
            elif len(product_names) == 1 and lp_has_total:
                for e in out:
                    if e["product_name"] != "合计":
                        out.append({"product_name": "合计", "mbrevenue": e["mbrevenue"],
                                    "start_date": e["start_date"], "end_date": e["end_date"],
                                    "year": e["year"]})

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

    return _format_type3_output(revenue_row, periods, product_name)


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
        r'淨收入|净收入|合[计計]|總[计計]|'
        r'銷售成本|销售成本|服務成本|服务成本|營業成本|营业成本|'
        r'毛利|營業費用|营业费用|研發|研发|'
        r'銷售及|销售及|行政開支|行政开支|一般及)')
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
    return out


def _format_type3_output(row, periods, product_name):
    """从单行提取格式化的输出列表."""
    out = []
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
        for c in range(1, len(row)):
            v = str(row[c] or '').strip()
            if v and re.search(r'\d', v):
                out.append({'product_name': product_name, 'mbrevenue': v,
                            'start_date': '', 'end_date': '', 'year': ''})
                break
    return out
