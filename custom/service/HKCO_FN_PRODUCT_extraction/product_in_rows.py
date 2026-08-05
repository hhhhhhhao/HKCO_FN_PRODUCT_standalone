# -*- coding: utf-8 -*-
"""产品名横向在表头行：收入行在产品列下方，逐产品列取收入。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from custom.service.HKCO_FN_PRODUCT_extraction.common import (
    REVENUE,
    _clean_header,
    _column_header,
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
    text = table.get("period_text") or " ".join(str(cell or "") for row in rows for cell in row)
    currency, unit = _currency_unit(text)

    metric_index = None
    for index, row in enumerate(rows):
        row_text = " ".join(str(cell or "") for cell in row)
        if REVENUE.search(row_text) and any(_number(cell) is not None for cell in row):
            if not re.search(r"成本|毛利|溢利|虧損|開支|費用|cost|profit|loss", row_text, re.I):
                metric_index = index
                break
    if metric_index is None:
        return []

    metric_row = rows[metric_index]
    label_index = None
    for index in range(metric_index - 1, -1, -1):
        names = [
            _clean_header(cell)
            for cell in rows[index]
            if _clean_header(cell) and _year(cell) is None
        ]
        if len(names) >= 2:
            label_index = index
            break
    if label_index is None:
        return []

    label_row = rows[label_index]
    offset = max(0, len(metric_row) - len(label_row))
    facts = []
    for column, cell in enumerate(label_row):
        name = _clean_header(cell)
        if not name:
            continue
        if _is_total(name):
            name = "合计"
        data_column = column + offset
        amount = _number(metric_row[data_column]) if data_column < len(metric_row) else None
        header = _column_header(rows, len(metric_row), data_column)
        year = _year(header)
        if amount is None or year is None:
            continue
        start, end = _period(year, context, text)
        facts.append(_fact(table, name, amount, start, end, currency, unit))
    return facts
