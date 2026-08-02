# -*- coding: utf-8 -*-
"""HKCO_FN_PRODUCT get_res — table classification + extraction."""
import re

from custom.service.EAPS_HKCO_FN_PRODUCT import (
    _dbg,
    _last_period_match_score,
    _last_period_product_entries,
    _last_period_product_names,
    _last_period_same_grain,
    format_number,
    fullwidth_to_halfwidth,
)


# ═══════════════════ helpers ═══════════════════
def _fw(s):
    return fullwidth_to_halfwidth(str(s or "")).replace("\n", " ").strip()


def _has_num(cell):
    s = str(cell or "").replace("\n", " ").strip()
    s = s.replace(",", "").replace("(", "").replace(")", "").replace("-", "").replace(" ", "")
    return bool(re.search(r"\d", s)) and not re.fullmatch(r"\d{4}", s)


def _extract_year(s):
    m = re.search(r"20\d{2}", str(s or ""))
    if m: return m.group(0)
    m = re.search(r"二[零〇○]([一二三四五六七八九零〇○]{2})", str(s or ""))
    if m:
        cn = {"零":"0","〇":"0","○":"0","一":"1","二":"2","三":"3","四":"4","五":"5","六":"6","七":"7","八":"8","九":"9"}
        try: return "20" + "".join(cn[x] for x in m.group(1))
        except: pass
    return ""


def _cn_year_to_arabic(s):
    m = re.search(r"二[零○〇]([一二三四五六七八九零○〇]{2})", s)
    if not m: return ""
    cn = {"零":"0","〇":"0","○":"0","一":"1","二":"2","三":"3","四":"4","五":"5","六":"6","七":"7","八":"8","九":"9"}
    try: return "20" + "".join(cn[x] for x in m.group(1))
    except: return ""


# ═══════════════════ CLASSIFICATION ═══════════════════

def classify_table(table, title=""):
    """分类表格布局。
    {orientation: row|col, row_structure: flat|nested|sectioned,
     fields: [revenue,...], period_layout: columns|sections|single}
    """
    nr, nc = len(table), max((len(r) for r in table if isinstance(r, list)), default=0)
    if nc < 2 or nr < 2:
        return {"orientation": "row", "row_structure": "flat", "fields": [], "period_layout": "single"}

    labels = [_fw(r[0]) if isinstance(r, list) and r else "" for r in table]
    _UNIT_RE = re.compile(r"(千港元|千元|人民幣千元|人民币千元|人民幣元|人民币元|百萬元|百万元|"
                          r"百萬港元|百万港元|千美元|千令吉|千新加坡元|港幣千元|港币千元|港元|人民幣|人民币|"
                          r"未經審核|未经审核|經審核|经审核|\(.*?\))+$")
    _NOT_PRODUCT = re.compile(
        r"^(同比|變動|变动|百分比|佔比|占比|佔總|占总|發生額|发生额|本期|上期|附註|附注|變幅|减幅|增幅|"
        r"收入|收益|成本|毛利|費用|费用|開支|开支|人民幣千元|人民币千元|千港元|千元|港幣千元|港币千元|"
        r"百萬元|百万元|未經審核|未经审核|經審核|经审核|20\d{2}|二零|"
        r"截至|於|于|for|總計|总计|合計|合计|總額|总额|小計|小计|合併|合并|集團|集团)")

    # orientation
    col_products = 0
    for row in table[:4]:
        if not isinstance(row, list): continue
        for c in row[1:]:
            clean = _UNIT_RE.sub("", _fw(c)).strip()
            if not clean or len(clean) < 3: continue
            if not re.search(r"[一-鿿]", clean): continue
            if _NOT_PRODUCT.match(clean): continue
            col_products += 1

    row_products = 0
    for row in table:
        if not isinstance(row, list) or len(row) < 2: continue
        lab = _fw(row[0])
        if not lab or not _has_num(row[1]): continue
        if re.match(r"^(合計|合计|總計|总计|總額|总额|小計|小计|合併|合并)\s*$", lab): continue
        if re.match(r"^(截至|於|于|for|20\d{2}|二零|收益確認|收入確認|分部|業績|业绩|毛利|"
                    r"銷售成本|销售成本|融資|融资|財務|财务|所得稅|所得税|利息|"
                    r"總資產|总资产|總負債|总负债|資產|负债|現金|现金|應收|应收|應付|应付|存貨|存货)", lab): continue
        row_products += 1

    if row_products >= 2 and row_products >= col_products:
        orientation = "row"
    elif col_products >= 2:
        orientation = "col"
    else:
        orientation = "row"

    # row_structure
    nested = sum(1 for lab in labels if lab.startswith("-") or lab.startswith("–"))
    sections = sum(1 for lab in labels if re.match(
        r"^(收益|收入|營業額|营业额|分部|業績|业绩|毛利|銷售成本|销售成本|服務成本|服务成本|"
        r"於某一時|于某一时|在某個時|按.*劃分|按.*划分|主要地區|主要地区|收入確認|收入确认|香港財務|香港财务)", lab))
    row_structure = "nested" if nested >= 2 else ("sectioned" if sections >= 2 else "flat")

    # fields
    fields = []
    for lab in labels:
        lab_clean = re.sub(r"^-+\s*", "", lab)
        if re.search(r"(收益|收入|營業額|营业额|銷售|销售|Revenue|Turnover|對外客户|对外客户|"
                     r"外部客户|向外|分部收益|分部收入|總收入|总收入)", lab_clean):
            fields.append("revenue"); break
    if "revenue" not in fields and row_products >= 2:
        fields.append("revenue")
    for lab in labels:
        if re.search(r"銷售成本|销售成本|服務成本|服务成本|營業成本|营业成本|Cost.of.sales", re.sub(r"^-+\s*", "", lab)):
            fields.append("cost"); break
    for lab in labels:
        if re.search(r"毛利|毛損|毛损|Gross.profit", re.sub(r"^-+\s*", "", lab)):
            fields.append("gross_profit"); break

    # period_layout
    row1_text = " ".join(_fw(c) for c in (table[0] if isinstance(table[0], list) else []))
    year_cols = len(set(re.findall(r"(20\d{2}|二[零○〇][一二三四五六七八九零○〇]{2})", row1_text)))
    section_breaks = 0
    prev = ""
    for lab in labels:
        if re.match(r"^(截至|於|于|for|20\d{2}|二零)", lab.strip(), re.I):
            if lab.strip()[:20] != prev[:20]: section_breaks += 1
            prev = lab.strip()
    if section_breaks >= 2 and year_cols >= 2: period_layout = "both"
    elif section_breaks >= 2: period_layout = "sections"
    elif year_cols >= 2: period_layout = "columns"
    else: period_layout = "single"

    return {"orientation": orientation, "row_structure": row_structure,
            "fields": fields, "period_layout": period_layout}


# ═══════════════════ EXTRACTION ═══════════════════

def _clean_name(s):
    s = str(s or "").replace("\n", " ").strip()
    s = re.sub(r"(千港元|千元|港幣千元|港币千元|人民幣千元|人民币千元|人民幣元|人民币元|"
               r"百萬元|百万元|百萬港元|百万港元|千美元|千令吉|千新加坡元|"
               r"未經審核|未经审核|經審核|经审核)\s*$", "", s).strip()
    s = re.sub(r"\(.*?\)", "", s).strip()
    return re.sub(r"\s+", " ", s)


def _is_product(s):
    s = s.strip()
    if not s or len(s) < 2: return False
    if re.match(r"^(合計|合计|總計|总计|總額|总额|小計|小计|合併|合并|集團|集团)\s*$", s): return False
    if re.match(r"^(20\d{2}|二零).{0,10}$", s): return False
    return bool(re.search(r"[一-鿿]", s))


def _is_financial_label(s):
    return bool(re.match(
        r"^(收益|收入|營業額|营业额|銷售成本|销售成本|服務成本|服务成本|毛利|毛損|毛损|"
        r"分部業績|分部业绩|分部收益|分部收入|分部虧損|分部亏损|"
        r"融資成本|融资成本|財務成本|财务成本|財務費用|财务费用|"
        r"所得稅開支|所得税开支|利息收入|利息開支|利息支出|"
        r"除稅前(溢利|虧損|亏损)|除税前(溢利|亏损)|年內(溢利|虧損|亏损)|年内(溢利|亏损)|"
        r"其他(收入|收益|開支|开支|虧損|亏损|收益|收入)|"
        r"每股(盈利|虧損|亏损|股息)|淨利潤|净利润|淨虧損|净亏损|"
        r"總資產|总资产|總負債|总负债|資產總值|资产总值|負債總額|负债总额|"
        r"現金及|现金及|應收賬|应收账|應付賬|应付账|存貨|存货|"
        r"權益總額|权益总额|股東權益|股东权益|"
        r"經營(溢利|利潤|利润|虧損|亏损)|经营(溢利|利润|亏损)|"
        r"未分配(收益|開支|开支|項目|项目))\s*$", s.strip()))


def _parse_period_cols(table):
    """检测每列的报告期，从表内容提取 REPORTDATE。
    返回 [(col, year, start_date, end_date)]。
    """
    nc = max((len(r) for r in table if isinstance(r, list)), default=0)

    # 从整表文本提取截止日期
    all_text = " ".join(str(c or "") for r in table if isinstance(r, list) for c in r)
    end_mm, end_dd = 12, 31
    is_half = False
    m = re.search(r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', all_text)
    if m:
        end_mm, end_dd = int(m.group(2)), int(m.group(3))
    if re.search(r'(6|六).{0,3}(個|个)月|中期|半年|止六個月|止六个月', all_text, re.I):
        is_half = True

    def _calc(yi):
        import datetime
        try:
            end_dt = datetime.date(yi, end_mm, min(end_dd, 28))
            delta = datetime.timedelta(days=182 if is_half else 365)
            start_dt = end_dt - delta
            return start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")
        except:
            return f"{yi-1}-12-31", f"{yi}-12-31"

    pairs = []
    seen = set()
    for col in range(1, nc):
        texts = []
        for row in table[:5]:
            if isinstance(row, list) and col < len(row):
                texts.append(str(row[col] or ""))
        hdr = " ".join(texts)
        years = re.findall(r"(20\d{2})", hdr)
        if not years:
            y_cn = _cn_year_to_arabic(hdr)
            years = [y_cn] if y_cn else []
        for y in years:
            if y in seen: continue
            seen.add(y)
            sd, ed = _calc(int(y))
            pairs.append((col, y, sd, ed))
            break
    return pairs


def _is_data_row(row):
    if not isinstance(row, list) or len(row) < 2: return False
    lab = _clean_name(row[0])
    if not lab or not _is_product(lab): return False
    if _is_financial_label(lab): return False
    if re.match(r"^(截至|於|于|for|收益確認|收入確認|按.*劃分|按.*划分|於某一|于某一|在某個|在某个|隨時間|随时间)", lab, re.I): return False
    return any(format_number(str(c or "")) for c in row[1:])


def _extract_row(table):
    periods = _parse_period_cols(table)
    rows = []
    for row in table:
        if not _is_data_row(row): continue
        pn = _clean_name(row[0])
        if periods:
            for col, yr, sd, ed in periods:
                if col >= len(row): continue
                rev = format_number(str(row[col] or ""))
                if not rev: continue
                rows.append({"product_name": pn, "mbrevenue": rev, "start_date": sd, "end_date": ed})
        else:
            for c in range(1, len(row)):
                rev = format_number(str(row[c] or ""))
                if rev:
                    rows.append({"product_name": pn, "mbrevenue": rev,
                                 "start_date": "", "end_date": ""}); break
    return rows


def _find_product_cols(table):
    nc = max((len(r) for r in table if isinstance(r, list)), default=0)
    products = {}
    for row in table[:5]:
        if not isinstance(row, list): continue
        for c in range(1, min(nc, len(row))):
            if c in products: continue
            name = _clean_name(row[c])
            if not name or not _is_product(name): continue
            if re.match(r"^(20\d{2}|二零|截至|於|于|for|變動|变动|百分比|%).*", name): continue
            if re.match(r"^(總計|总计|合計|合计|總額|总额|小計|小计|合併|合并)$", name): continue
            products[c] = name
    to_del = [c for c, n in products.items() if re.match(r"^(總計|总计|合計|合计|總額|总额|小計|小计)$", n)]
    if to_del: del products[max(to_del)]
    return products


def _find_rev_rows(table):
    candidates = []
    for i, row in enumerate(table):
        if not isinstance(row, list) or not row: continue
        lab = _fw(row[0])
        if re.search(r"(對外客户|对外客户|向外|外部客户|外部收入|外部收益|分部收益|分部收入|"
                     r"可呈報.*收益|可报告.*收益|收益|收入|營業額|营业额|銷售|销售|Revenue|Turnover)", lab):
            count = sum(1 for c in row[1:] if format_number(str(c or "")))
            candidates.append((i, count))
        elif re.match(r"^-\s*(對外|对外|向外|外部|於|于|在|按).{0,20}(銷售|销售|收益|收入|時點|时点|時段|时段|時間|时间)", lab):
            count = sum(1 for c in row[1:] if format_number(str(c or "")))
            candidates.append((i, count))
    return candidates


def _extract_col(table):
    products = _find_product_cols(table)
    if not products: return []
    candidates = _find_rev_rows(table)
    if not candidates: return []

    best = max(candidates, key=lambda x: x[1])
    rev_data = table[best[0]]
    periods = _parse_period_cols(table)
    rows = []
    for col, pn in products.items():
        if col >= len(rev_data): continue
        rev = format_number(str(rev_data[col] or ""))
        if not rev: continue
        row = {"product_name": _clean_name(pn), "mbrevenue": rev}
        col_ps = [(c, y, sd, ed) for c, y, sd, ed in periods if c == col]
        if col_ps:
            _, _, sd, ed = col_ps[0]
            row["start_date"] = sd; row["end_date"] = ed
            for _, _, sd2, ed2 in col_ps[1:2]:
                r2 = dict(row); r2["start_date"] = sd2; r2["end_date"] = ed2; rows.append(r2)
        rows.append(row)
    return rows


def _extract(table, layout):
    if layout.get("orientation") == "row":
        return _extract_row(table)
    else:
        return _extract_col(table)


# ═══════════════════ MAIN ENTRY ═══════════════════

def get_res(selected, info_code, reason_arr, notice_date="", last_period_data=None):
    res = {"target_res": [], "pipe_meta": {"selected_count": 0, "source_pages": []}}
    if not selected: reason_arr.append("未选到目标表"); return res

    target_table = selected.get("target_table") if isinstance(selected, dict) else selected
    page_number = selected.get("page_number") if isinstance(selected, dict) else None
    if page_number is not None: res["pipe_meta"]["source_pages"] = [page_number]
    res["pipe_meta"]["selected_count"] = 1
    if not target_table: reason_arr.append("目标表为空"); return res

    # currency / unit
    flat = " ".join(_fw(c) for r in target_table[:8] for c in (r or []))
    cur, unit = _detect_currency_unit(flat)
    if not cur: cur = "港元"
    if not unit: unit = "千元"

    # classify + extract
    layout = classify_table(target_table, selected.get("title", "") if isinstance(selected, dict) else "")
    _dbg(f"[get_res] layout={layout} cur={cur} unit={unit}")
    rows = _extract(target_table, layout)

    # post-process
    seen = set()
    result = []
    for r in rows:
        r["currency"] = cur
        r["unit"] = unit
        r.setdefault("start_date", "")
        r.setdefault("end_date", "")
        key = (r["product_name"], r["mbrevenue"], r["start_date"])
        if key not in seen:
            seen.add(key)
            result.append(r)

    if not result:
        reason_arr.append("提取为空"); return res

    # LP alignment
    lp_names = _last_period_product_names(last_period_data)
    if lp_names:
        lp_display = {n: d for n, d in _last_period_product_entries(last_period_data)}
        for r in result:
            pn = r.get("product_name", "")
            if pn == "合计": continue
            best_s, best_n = 0, None
            for lp in lp_names:
                if _last_period_same_grain(lp, pn) or pn == lp:
                    s = _last_period_match_score(pn, lp)
                    if s > best_s: best_s, best_n = s, lp
            if best_n and best_s >= 60:
                r["product_name"] = lp_display.get(best_n) or best_n

    _dbg(f"[get_res] final: {len(result)} rows")
    res["target_res"] = result
    return res


# ═══════════════════ PUBLIC API ═══════════════════

def _detect_currency_unit(header_text):
    currency, unit = "", ""
    if re.search(r"令吉|林吉特|MYR", header_text, re.I): currency = "马来西亚林吉特"
    elif re.search(r"新加坡|SGD|坡元|S\$", header_text, re.I): currency = "新加坡元"
    elif re.search(r"澳門元|澳门元|MOP", header_text, re.I): currency = "澳门元"
    elif re.search(r"人民幣|人民币|RMB|CNY", header_text, re.I): currency = "人民币"
    elif re.search(r"美元|美金|USD|US\$", header_text, re.I): currency = "美元"
    elif re.search(r"港元|港幣|港币|HKD|HK\$", header_text, re.I): currency = "港元"
    if re.search(r"百萬|百万|million|Million", header_text): unit = "003"
    elif re.search(r"千元|千港元|thousand|Thousand|千美元|千令吉|千新加坡元", header_text): unit = "002"
    elif re.search(r"元\b|dollars", header_text, re.I): unit = "001"
    return currency, unit


def get_period_hint(selected, notice_date=""):
    title = selected.get("title") if isinstance(selected, dict) else ""
    text = (title or "") + " " + str(notice_date or "")
    m = re.search(r"截至\s*(20\d{2}|二零[零○〇][一二三四五六七八九零○〇]{2})", text)
    if m:
        y = m.group(1)
        if y.startswith("20"): return y + "年"
        cn_year = _cn_year_to_arabic(y)
        if cn_year: return cn_year + "年"
    return ""


_extract_year_from_text = _extract_year


def _is_period_or_unit_name(name):
    n = str(name or "").strip()
    return bool(re.match(r"^(20\d{2}|二零|截至|於|于|for|"
                         r"(截至|止)\s*(二零|20\d{2}).{0,12}|"
                         r"千港元|千元|人民幣|人民币|百萬|百万|千美元)$", n))


def merge_raw_by_product_year(rows):
    merged = {}
    for r in rows or []:
        key = (r.get("product_name", ""), r.get("end_date", ""))
        if key not in merged: merged[key] = dict(r)
        else:
            for field in ("mbrevenue", "mbcost", "gross_profit"):
                if not merged[key].get(field) and r.get(field): merged[key][field] = r.get(field)
    return list(merged.values())
