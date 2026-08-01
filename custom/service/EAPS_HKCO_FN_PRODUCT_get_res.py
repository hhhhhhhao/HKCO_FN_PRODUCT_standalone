# -*- coding: utf-8 -*-
"""HKCO_FN_PRODUCT get_res — named classification + extraction stubs."""
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


# ═══════════════════ CLASSIFICATION ═══════════════════
# ── title keyword ──
_TITLE_KEYWORDS = [
    # (pattern, keyword)
    (r"損益|损益|利潤|利润|合併.*虧損|合并.*亏损", "利润"),
    (r"分部", "分部"),
    (r"利息收入|股息收入|利息收益", "利息"),
    (r"負債|负债|應付|应付|應收|应收|資產負債|资产负债", "负债"),
]

def _title_keyword(title):
    t = (title or "")[:200]
    if not t: return "?"
    for pat, kw in _TITLE_KEYWORDS:
        if re.search(pat, t):
            return kw
    # Default: most HKCO tables are about revenue
    return "收入"


def _table_structure(table):
    """纯结构性判断，不打分不计数。"""
    nr = len(table)
    nc = max((len(r) for r in table if isinstance(r, list)), default=0)
    if nc < 2 or nr < 2: return "未找到表"

    labels = [_fw(r[0]) for r in table if isinstance(r, list) and r]

    # P&L：收益→成本→毛利 序列
    for i, lab in enumerate(labels):
        if re.search(r"^(收益|收入|營業額|营业额)$", lab):
            if i + 1 < len(labels) and re.search(r"^(銷售成本|销售成本|服務成本|服务成本|直接成本|成本|费用)", labels[i + 1]):
                return "P&L"
            if i + 2 < len(labels) and re.search(r"^(毛利|毛損|毛损)", labels[i + 2]):
                return "P&L"

    # 嵌套：有 "-" 子产品
    if any(lab.startswith("-") or lab.startswith("–") for lab in labels):
        return "嵌套"

    return "行产品"


# ── combine ──
def classify_table(table, title=""):
    """→ "收入__行产品" / "分部__P&L" / "利润__P&L" / ..."""
    kw = _title_keyword(title)
    st = _table_structure(table)
    return f"{kw}__{st}"


# ═══════════════════ EXTRACTORS (stubs) ═══════════════════
def _extract_income_row(table, cur, unit, ph, hy):
    """收入__行产品 / 收入__嵌套 / 收入__少产品"""
    return []

def _extract_income_pl(table, cur, unit, ph, hy):
    """收入__P&L"""
    return []

def _extract_seg_pl(table, cur, unit, ph, hy):
    """分部__P&L"""
    return []

def _extract_seg_row(table, cur, unit, ph, hy):
    """分部__行产品"""
    return []

def _extract_pl_pl(table, cur, unit, ph, hy):
    """利润__P&L"""
    return []

def _extract_pl_row(table, cur, unit, ph, hy):
    """利润__行产品 / 利润__嵌套"""
    return []

def _extract_interest(table, cur, unit, ph, hy):
    """利息__*"""
    return []

# map: type string → extractor function
_EXTRACTORS = {
    "收入__行产品": _extract_income_row,
    "收入__嵌套":   _extract_income_row,
    "收入__P&L":    _extract_income_pl,
    "分部__行产品": _extract_seg_row,
    "分部__嵌套":   _extract_seg_row,
    "分部__P&L":    _extract_seg_pl,
    "利润__P&L":    _extract_pl_pl,
    "利润__行产品": _extract_pl_row,
    "利润__嵌套":   _extract_pl_row,
    "利息__行产品": _extract_interest,
    "利息__嵌套":   _extract_interest,
    "利息__P&L":    _extract_interest,
    "?__未找到表":  lambda *a: [],
    "负债__行产品":  lambda *a: [],
    "负债__嵌套":    lambda *a: [],
    "负债__少产品":  lambda *a: [],
    "负债__P&L":     lambda *a: [],
    "负债__多维":    lambda *a: [],
    "负债__分部":    lambda *a: [],
}


# ═══════════════════ MAIN ENTRY ═══════════════════
def get_res(selected, info_code, reason_arr, notice_date="", last_period_data=None):
    res = {"target_res": [], "pipe_meta": {"selected_count": 0, "source_pages": []}}
    if not selected: reason_arr.append("未选到目标表"); return res

    target_table = selected.get("target_table") if isinstance(selected, dict) else selected
    page_number = selected.get("page_number") if isinstance(selected, dict) else None
    title = selected.get("title") if isinstance(selected, dict) else ""
    if page_number is not None: res["pipe_meta"]["source_pages"] = [page_number]
    res["pipe_meta"]["selected_count"] = 1
    if not target_table: reason_arr.append("目标表为空"); return res

    # ─── period / year ───
    period_hint = get_period_hint(selected, notice_date=notice_date)
    hy = _extract_year(period_hint) or _extract_year(str(notice_date or "")) or ""
    if not hy:
        for row in target_table[:8]:
            if not isinstance(row, list): continue
            for c in row:
                y = _extract_year(c)
                if y: hy = y; break
            if hy: break
    if not period_hint and hy: period_hint = hy + "年"

    # ─── currency / unit ───
    flat = " ".join(_fw(c) for r in target_table[:8] for c in (r or []))
    cur, unit = detect_currency_unit(flat + " " + (period_hint or ""))
    lp_cur, lp_unit = "", ""
    for lr in last_period_data or []:
        if not isinstance(lr, dict): continue
        if not lp_cur and lr.get("CURRENCY"): lp_cur = str(lr.get("CURRENCY") or "")
        if lp_unit == "" and str(lr.get("UNIT") or "") != "": lp_unit = str(lr.get("UNIT"))
    if not cur: cur = lp_cur or "港元"
    if not unit: unit = lp_unit or "千元"

    # ─── classify ───
    typ = classify_table(target_table, title)
    _dbg(f"[get_res] type={typ} hy={hy} cur={cur} unit={unit}")

    # ─── extract ───
    extractor = _EXTRACTORS.get(typ)
    rows = []
    if extractor:
        try: rows = extractor(target_table, cur, unit, period_hint, hy)
        except Exception: rows = []

    if not rows:
        reason_arr.append("提取为空")
        return res

    # ─── LP alignment ───
    lp_names = _last_period_product_names(last_period_data)
    if lp_names:
        lp_display = {n: d for n, d in _last_period_product_entries(last_period_data)}
        for r in rows:
            pn = r.get("product_name", "")
            if pn == "合计": continue
            best_s, best_n = 0, None
            for lp in lp_names:
                if _last_period_same_grain(pn, lp) or pn == lp:
                    s = _last_period_match_score(pn, lp)
                    if s > best_s: best_s, best_n = s, lp
            if best_n and best_s >= 60:
                r["product_name"] = lp_display.get(best_n) or best_n
            if lp_cur and not r.get("currency"): r["currency"] = lp_cur
            if lp_unit: r["unit"] = lp_unit

    _dbg(f"[get_res] final: {len(rows)} rows")
    res["target_res"] = rows
    return res


# ═══════════════════ PUBLIC API ═══════════════════

def detect_currency_unit(header_text):
    currency, unit = "", ""
    if re.search(r"令吉|林吉特|MYR", header_text, re.I): currency = "马来西亚林吉特"
    elif re.search(r"新加坡|SGD|坡元|S\$", header_text, re.I): currency = "新加坡元"
    elif re.search(r"澳門元|澳门元|MOP", header_text, re.I): currency = "澳门元"
    elif re.search(r"人民幣|人民币|RMB|CNY", header_text, re.I): currency = "人民币"
    elif re.search(r"日元|日圓|JPY", header_text, re.I): currency = "日元"
    elif re.search(r"美元|USD|US\$", header_text, re.I): currency = "美元"
    elif re.search(r"港元|港幣|港币|HK\$|HKD", header_text, re.I): currency = "港元"
    elif re.search(r"欧元|EUR", header_text, re.I): currency = "欧元"
    if re.search(r"千港元|千元|仟元|千美元|千新加坡|千坡元|人民幣千元|人民币千元|HK\$['′']\s*0{3}|S\$['′']\s*0{3}|\$['′']\s*0{3}|['′']\s*000", header_text, re.I):
        unit = "千港元" if currency == "港元" else "千元"
    elif re.search(r"百[萬万]|million", header_text, re.I): unit = "百万元"
    elif re.search(r"(金額單位|金额单位|單位|单位|以)\s*[：:]*\s*(人民幣|人民币|港元|美元)?\s*元\b|[（(]\s*(人民幣|人民币|港元|美元)?\s*元\s*[)）]", header_text) and not re.search(r"千|百[萬万]|['′']\s*000", header_text, re.I):
        unit = "元"
    elif currency == "新加坡元" and not re.search(r"千|百[萬万]|['′']\s*000", header_text, re.I): unit = "元"
    else: unit = "千元"
    if not currency and re.search(r"百[萬万]元", header_text) and not re.search(r"港元|港幣|港币|HK\$|HKD|美元|USD|日[元圓]|JPY", header_text, re.I): currency = "人民币"
    return currency or "港元", unit

def get_period_hint(selected, notice_date=""):
    texts = []
    if isinstance(selected, dict):
        texts.append(selected.get("title") or "")
        for line in selected.get("page_lines") or []:
            if isinstance(line, dict): texts.append(line.get("text") or "")
            else: texts.append(str(line or ""))
        for row in selected.get("target_table") or []:
            for cell in (row or [])[:8]: texts.append(str(cell or ""))
    if notice_date: texts.append(str(notice_date))
    joined = "\n".join(texts)
    cands = []
    for m in re.finditer(r"截至[^。；;\n]{0,40}止(?:六[個个]月|三[個个]月|九[個个]月|年度|年)?", joined): cands.append(m.group(0))
    for m in re.finditer(r"For the (?:six|three|nine)?\s*months?\s*ended[^.\n]{0,60}", joined, re.I): cands.append(m.group(0))
    if cands:
        def _y(s): ys = re.findall(r"20\d{2}", fullwidth_to_halfwidth(s)); return max(ys) if ys else "0000"
        cands.sort(key=_y, reverse=True); return cands[0]
    m = re.search(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", joined)
    if m: return m.group(0)
    m = re.search(r"(20\d{2})\s*年", joined)
    if m: return m.group(0)
    m = re.search(r"(20\d{2})", str(notice_date or ""))
    if m: return m.group(1) + "年"
    return ""

def _cn_year_to_arabic(text):
    blob = str(text or "")
    m = re.search(r"二[零〇○]([一二三四五六七八九零〇○]{2})", blob)
    if not m: return ""
    cn = {"零":"0","〇":"0","○":"0","一":"1","二":"2","三":"3","四":"4","五":"5","六":"6","七":"7","八":"8","九":"9"}
    try: return "20" + "".join(cn[x] for x in m.group(1))
    except: return ""

_extract_year_from_text = _extract_year

def _is_period_or_unit_name(name):
    t = re.sub(r"\s+", "", fullwidth_to_halfwidth(str(name or "")))
    if not t or t in {".","-","—","–"}: return True
    if re.search(r"截至|止年度|止期間|months?\s*ended|止六[個个]月|止三[個个]月", t, re.I): return True
    if re.match(r"^(20\d{2}|二[零〇○][一二三四五六七八九零〇○]{2})年?", t): return True
    if re.match(r"^(人民幣|人民币|港元|港幣|港币|美元|日元|日圓|千元|千港元|百萬元|百万元|百萬港元|百万港元|金額|金额|佔總|占总|百分比|附註|附注|項目|项目|佔比|占比)$", t): return True
    if re.search(r"佔總|占总|百分比", t): return True
    return False

def merge_raw_by_product_year(rows):
    merged = {}; order = []
    for item in rows or []:
        name = str(item.get("product_name") or "")
        key = (name, item.get("year"))
        if key not in merged: merged[key] = dict(item); order.append(key); continue
        cur = merged[key]
        for fld in ("mbrevenue", "mbcost", "gross_profit"):
            a = format_number(cur.get(fld, "")); b = format_number(item.get(fld, ""))
            if a == "" and b == "": cur[fld] = ""
            elif a == "": cur[fld] = b
            elif b == "": cur[fld] = a
            elif a == b: cur[fld] = a
            else:
                try:
                    fa, fb = float(a), float(b)
                    cur[fld] = str(fa + fb) if abs(fa - fb) > 1e-6 else a
                except: cur[fld] = a
    return [merged[k] for k in order]
