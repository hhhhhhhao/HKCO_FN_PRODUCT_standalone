# -*- coding: utf-8 -*-
"""三类抽取共用的最小工具函数。"""
from __future__ import annotations

import calendar
import datetime
import re
from typing import Any, Dict, List, Optional


NUMBER = re.compile(r"^\s*\(?-?[\d,]+(?:\.\d+)?\)?\s*$")
YEAR = re.compile(r"20\d{2}|二零[〇零一二三四五六七八九]{2}|二〇[〇零一二三四五六七八九]{2}", re.I)
REVENUE = re.compile(r"收入|收益|營業額|营业额|銷售額|销售额|revenue|turnover|sales", re.I)
REVENUE_LABEL = re.compile(r"^(?:收入|收益|營業額|營業收入|銷售收入|銷售額|revenue|turnover|sales)$", re.I)
COST = re.compile(r"成本|cost", re.I)
GROSS_PROFIT = re.compile(r"毛利|毛損|毛损|gross profit|gross loss", re.I)
TOTAL = re.compile(r"^(?:合計|合计|小計|小计|總計|总计|總額|总额|總收入|总收入|subtotal|total)$", re.I)
CURRENCY = re.compile(r"人民幣|人民币|港幣|港币|港元|美元|歐元|欧元|日圓|日元")
UNIT = re.compile(r"百萬|百万|萬|万|千|元|million|thousand")
CN = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
      "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _number(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not NUMBER.fullmatch(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    amount = float(text.strip("() ").replace(",", ""))
    return -amount if negative else amount


def _year(value: Any) -> Optional[int]:
    match = YEAR.search(str(value or ""))
    if not match:
        return None
    token = match.group(0)
    if token.isdigit():
        return int(token)
    return 2000 + int("".join(str(CN.get(ch, 0)) for ch in token[2:]))


def _cn_number(token: str) -> int:
    token = token.strip()
    if token.isdigit():
        return int(token)
    if "十" in token:
        left, right = token.split("十", 1)
        tens = CN.get(left, 1) if left else 1
        units = CN.get(right, 0) if right else 0
        return tens * 10 + units
    return CN.get(token, 0)


def _add_months(value: datetime.date, months: int) -> datetime.date:
    month = value.month + months
    year = value.year
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)


def _period(year: int, context: Dict[str, Any], text: str):
    month, day = tuple(context.get("prior_fiscal_month_day") or (12, 31))
    m = re.search(r"截至.*?(\d{1,2})月(\d{1,2})日", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"截至.*?([〇零一二三四五六七八九十]{1,3})月([〇零一二三四五六七八九十]{1,3})日", text)
        if m:
            month, day = _cn_number(m.group(1)), _cn_number(m.group(2))
    day = min(day, calendar.monthrange(year, month)[1])
    end = datetime.date(year, month, day)
    if re.search(r"六個月|六个月|6\s*個月|6\s*个月|six months", text, re.I):
        months = 6
    elif re.search(r"九個月|九个月|9\s*個月|9\s*个月|nine months", text, re.I):
        months = 9
    else:
        months = 12
    start = _add_months(end, -months) + datetime.timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _rows(table: Dict[str, Any]) -> List[List[Any]]:
    return [list(row) for row in table.get("table", []) if isinstance(row, (list, tuple))]


def _column_header(rows: List[List[Any]], width: int, column: int) -> str:
    values = []
    for row in rows[:5]:
        source = column - max(0, width - len(row))
        if 0 <= source < len(row):
            values.append(str(row[source] or ""))
    return " ".join(values)


def _clean_name(value: Any) -> str:
    return re.sub(r"^(?:其中[:：]?\s*|[-–—·•]{1,3}\s*)", "", str(value or "").strip())


def _clean_header(value: Any) -> str:
    text = str(value or "").replace("\n", " ")
    text = YEAR.sub("", text)
    text = CURRENCY.sub("", text)
    text = UNIT.sub("", text)
    text = re.sub(r"未經審核|未审核|未經審計|未审计|unaudited", "", text, flags=re.I)
    return re.sub(r"[()（）\s]+", "", text)


def _is_total(value: Any) -> bool:
    return bool(TOTAL.fullmatch(str(value or "").strip()))


def _currency_unit(text: str):
    currency = CURRENCY.search(text)
    unit = UNIT.search(text)
    return (currency.group() if currency else ""), (unit.group() if unit else "")


def _fact(table: Dict[str, Any], name: str, amount: float, start: str, end: str,
          currency: str, unit: str) -> Dict[str, Any]:
    return {
        "table_id": table.get("id", ""),
        "metric": "MBREVENUE",
        "product_name": name,
        "amount": amount,
        "start_date": start,
        "end_date": end,
        "currency": currency,
        "unit": unit,
        "row_index": 0,
        "column_index": 0,
        "header": "",
    }
