# -*- coding: utf-8 -*-
"""HKCO_FN_PRODUCT format_data: get_res output →入库字段."""
import re


def _format_number(value):
    """Format an already extracted scalar without rediscovering facts."""
    text = str(value) if value is not None else ""
    if not text or text in {"-", "--", "—", "–", "n/a", "N/A", "nil", "Nil", "null", "None"}:
        return ""
    text = text.replace("\r", "\n").split("\n")[0].strip()
    negative = text.startswith(("(", "（")) and text.endswith((")", "）"))
    if negative:
        text = text.strip("()（）")
    match = re.search(r"-?\d+(?:,\d{3})*(?:\.\d+)?", text)
    if not match:
        return ""
    number = match.group(0).replace(",", "")
    return "-" + number if negative and not number.startswith("-") else number


_rfn = _format_number


def _is_period_or_unit_name(name):
    """Check if the string is a period label or unit name, not a product."""
    n = str(name or "").strip()
    return bool(re.match(
        r"^(20\d{2}|二零|截至|於|于|for|"
        r"(截至|止)\s*(二零|20\d{2}).{0,12}|"
        r"千港元|千元|人民幣|人民币|百萬|百万|千美元)$", n
    ))

def _r(obj, key, default=None):
    """Safe dict access."""
    try:
        return obj[key] if obj and key in obj else default
    except (TypeError, KeyError):
        return default

# ── product name cleanup ──
def _format_product_name(name):
    """Clean up product name."""
    if not name:
        return ""
    name = str(name).strip()
    # Remove period/time suffixes
    name = re.sub(r"[,，]\s*(於某個時間點|于某个时间点|於某一時間點|于某一时间点|"
                  r"隨時間|随时间).{0,24}(\s|$)", "", name)
    # IFRS15 表常把确认方式用破折号拼在产品后面；这是产品属性，不是名称。
    name = re.sub(
        r"\s*[-–—]\s*(?:於|于|在)?(?:某一|某個|某个|特定)?"
        r"(?:時間點|时间点|時點|时点)(?:確認|确认)?\s*$",
        "",
        name,
    )
    name = re.sub(
        r"\s*[-–—]\s*(?:隨|随)(?:著|着)?(?:時間|时间)(?:推移)?(?:確認|确认)?\s*$",
        "",
        name,
    )
    name = name.strip(" ,，")
    # 合计 normalization
    if re.fullmatch(r"合計|合计|總計|总计|總額|总额|總收入|总收入|總收益|总收益", name):
        name = "合计"
    return name

# ── unit code ──
def _format_unit_code(unit):
    if not unit: return ""
    u = str(unit).strip()
    if u in ("001", "元"): return "001"
    if u in ("002", "千元", "千港元"): return "002"
    if u in ("004", "百万元", "百萬元"): return "004"
    return ""

# ── period dates ──
# ── main ──
def format_data(res, derived_id, info_code, notice_date, request_id, task_id, reason_arr, last_period_data=None):
    """get_res 输出 → 入库/回测字段."""
    result_data = []
    pipe_meta = _r(res, "pipe_meta", {"selected_count": 0, "source_pages": []})
    target_res = _r(res, "target_res", [])

    for item in target_res:
        product_name = _r(item, "product_name", "")
        currency = _r(item, "currency", "")
        unit = _r(item, "unit", "")
        mbrevenue = _r(item, "mbrevenue", "")
        mbcost = _r(item, "mbcost", "")
        gross_profit = _r(item, "gross_profit", "")

        if _is_period_or_unit_name(product_name):
            continue
        product_name = _format_product_name(product_name)
        if not product_name or _is_period_or_unit_name(product_name):
            continue

        # 日期是事实物化的一部分；格式层不得再次推断。
        start_date_val = _r(item, "start_date", "")
        end_date_val = _r(item, "end_date", "")
        if not start_date_val or not end_date_val:
            continue
        sdate = start_date_val + "T00:00:00.000Z"
        rdate = end_date_val + "T00:00:00.000Z"

        unit_code = _format_unit_code(unit)

        row = {
            "PRODUCTNAME": product_name,
            "STARTDATE": sdate,
            "REPORTDATE": rdate,
            "CURRENCY": currency,
            "UNIT": unit_code,
            "MBREVENUE": _rfn(mbrevenue),
            "MBCOST": _rfn(mbcost),
            "GROSS_PROFIT": _rfn(gross_profit),
        }
        result_data.append(row)

    if not result_data:
        reason_arr.append("format_data: 处理后为空")
    return result_data, reason_arr, pipe_meta
