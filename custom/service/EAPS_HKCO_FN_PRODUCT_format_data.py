# -*- coding: utf-8 -*-
"""HKCO_FN_PRODUCT format_data: get_res output →入库字段."""
import re
from datetime import datetime, timezone

from custom.service.EAPS_HKCO_FN_PRODUCT import format_number

_rfn = format_number


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
    name = name.strip(" ,，")
    # 合计 normalization
    if re.fullmatch(r"合計|合计|總計|总计|總額|总额|總收入|总收入|總收益|总收益", name):
        name = "合计"
    return name

# ── unit code ──
def _format_unit_code(unit):
    if not unit: return "002"
    u = str(unit).strip()
    if u in ("001", "元"): return "001"
    if u in ("002", "千元", "千港元"): return "002"
    if u in ("003", "百万元"): return "003"
    if u in ("004", "百万元"): return "004"
    return "002"

# ── period dates ──
def _format_period_dates(year, period_text):
    """year + period_text → STARTDATE/REPORTDATE."""
    y = str(year or "").strip()
    if not y:
        m = re.search(r"20\d{2}", str(period_text or ""))
        if m: y = m.group(0)
    if not y: return "", ""

    try:
        yi = int(y)
    except ValueError:
        return "", ""

    # Try to find month/day from period_text
    md = None
    pt = str(period_text or "")
    m2 = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", pt)
    if m2:
        md = (int(m2.group(1)), int(m2.group(2)))
    else:
        m2 = re.search(r"截至\s*20\d{2}\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", pt)
        if m2:
            md = (int(m2.group(1)), int(m2.group(2)))

    if md and md != (12, 31):
        # Non-calendar year end
        import calendar
        last_day = calendar.monthrange(md[0], 1)[1] + 1 if md[1] < 31 else 0
        if last_day == 0:
            start = datetime(yi - 1, md[0], 1 if md[1] == 31 else md[1], tzinfo=timezone.utc)
        else:
            try:
                start = datetime(yi - 1, md[0], md[1] + 1, tzinfo=timezone.utc)
            except ValueError:
                start = datetime(yi - 1, md[0], 1, tzinfo=timezone.utc)
        end = datetime(yi, md[0], md[1], tzinfo=timezone.utc)
    else:
        # Calendar year
        start = datetime(yi - 1, 12, 31, tzinfo=timezone.utc)
        end = datetime(yi, 12, 31, tzinfo=timezone.utc)

    return start.strftime("%Y-%m-%dT%H:%M:%S.000Z"), end.strftime("%Y-%m-%dT%H:%M:%S.000Z")

# ── main ──
def format_data(res, derived_id, info_code, notice_date, request_id, task_id, reason_arr, last_period_data=None):
    """get_res 输出 → 入库/回测字段."""
    result_data = []
    pipe_meta = _r(res, "pipe_meta", {"selected_count": 0, "source_pages": []})
    target_res = _r(res, "target_res", [])

    for item in target_res:
        product_name = _r(item, "product_name", "")
        year = _r(item, "year", "")
        period_text = _r(item, "period_text", "")
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

        # Filter out noise labels
        if re.match(
            r"^(經營支出|经营支出|營運開支|营运开支|財務成本|财务成本|折舊|折旧|"
            r"信貸減值|信贷减值|除稅前|除税前|所得稅|所得税|未分配|"
            r"其他收入.*淨額|其他收入.*净额|"
            r"分部間收入|分部間收益|分部间收入|分部间收益|"
            r"可呈報分部溢利|可呈報分部利潤|"
            r"可申報分部業績|"
            r"總資產|总资产|總負債|总负债|總權益|总权益|合約資產|合约资产|"
            r"分部資產|分部资产|分部負債|分部负债|分類資產|分类资产|分類負債|分类负债|"
            r"除稅前溢利|除稅前虧損|"
            r"年內溢利|年内溢利|所得稅開支|所得税开支|"
            r"銷售成本|销售成本|毛利|融資成本|融资成本|"
            r"銷售及分銷開支|销售及分销开支|行政開支|行政开支|期內溢利|期内溢利|"
            r"經營利潤|经营利润|"
            r"營運開支|減值虧損|减值亏损|"
            r"利息費用|利息费用|"
            r"所得稅前利潤|所得税前利润|"
            r"經營收入合計|经营收入合计|"
            r"產品銷售成本|产品销售成本|銷售開支|销售开支|"
            r"稅前虧損|税前亏损|期內虧損|期内亏损|"
            r"全面利潤.*|全面利润.*|"
            r"每股.*虧損|每股.*亏损)$",
            product_name,
        ):
            continue

        if re.match(
            r"^(於某一|于某一|在某一|於某個|于某个|隨時間|随时间|在一段|於一段|于一段).{0,16}(確認|确认|轉移|转移|轉讓|转让)",
            product_name,
        ):
            continue

        if mbrevenue and str(mbrevenue).strip() == product_name:
            continue
        if mbrevenue and not re.search(r"\d", str(mbrevenue)) and re.search(
            r"[\u4e00-\u9fffA-Za-z]", str(mbrevenue)
        ):
            continue

        sdate, rdate = _format_period_dates(year, period_text)
        if not sdate and year:
            try:
                yi = int(year)
                sdate = datetime(yi - 1, 12, 31, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                rdate = datetime(yi, 12, 31, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            except ValueError:
                pass

        unit_code = _format_unit_code(unit)

        row = {
            "PRODUCTNAME": product_name,
            "STARTDATE": sdate,
            "REPORTDATE": rdate,
            "CURRENCY": currency or "港元",
            "UNIT": unit_code,
            "MBREVENUE": _rfn(mbrevenue),
            "MBCOST": _rfn(mbcost),
            "GROSS_PROFIT": _rfn(gross_profit),
        }
        result_data.append(row)

    if not result_data:
        reason_arr.append("format_data: 处理后为空")
    return result_data, reason_arr, pipe_meta
