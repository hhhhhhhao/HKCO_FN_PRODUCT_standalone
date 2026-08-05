# -*- coding: utf-8 -*-
"""产品名纵向在第一列：按年份列取每行收入。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from custom.service.HKCO_FN_PRODUCT_extraction.common import (
    _column_header,
    _clean_name,
    _currency_unit,
    _fact,
    _is_total,
    _number,
    _period,
    _rows,
    _year,
)


def extract(table: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = _rows(table)
    width = max((len(row) for row in rows), default=0)
    text = table.get("period_text") or " ".join(str(cell or "") for row in rows for cell in row)
    currency, unit = _currency_unit(text)

    year_columns = []
    for column in range(width):
        header = _column_header(rows, width, column)
        year = _year(header)
        if year and any(
            column < len(row) and _number(row[column]) is not None
            for row in rows[3:]
        ):
            year_columns.append((column, year))
    if not year_columns:
        return []

    facts = []
    for row in rows[2:]:
        if not row:
            continue
        name = _clean_name(row[0])
        if len(row) > 1 and re.search(r"[\u4e00-\u9fff]", str(row[1] or "")) and not re.search(r"[\u4e00-\u9fff]", name):
            name = _clean_name(row[1])
        if not name:
            if any(_number(cell) is not None for cell in row[1:3]):
                name = "合计"
            else:
                continue
        if _is_total(name):
            name = "合计"
        if re.search(
            r"分部業績|分部收益|毛利|成本|溢利|虧損|開支|費用|折舊|利息收入|"
            r"profit|loss|expense|subtotal|total",
            name,
            re.I,
        ):
            continue
        for column, year in year_columns:
            amount = _number(row[column]) if column < len(row) else None
            if amount is None:
                continue
            start, end = _period(year, context, text)
            facts.append(_fact(table, name, amount, start, end, currency, unit))
    return facts
