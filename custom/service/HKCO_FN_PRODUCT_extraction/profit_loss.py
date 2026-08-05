# -*- coding: utf-8 -*-
"""损益表内嵌收入分项：收入行前后紧邻的产品行，收入行金额作为合计。"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from custom.service.HKCO_FN_PRODUCT_extraction.common import (
    REVENUE_LABEL,
    _column_header,
    _clean_name,
    _currency_unit,
    _fact,
    _number,
    _period,
    _rows,
    _year,
)


def _row_label(row) -> str:
    return str(row[0] or "").strip() if row else ""


def _is_revenue_row(row) -> bool:
    cells = [str(cell or "").strip() for cell in row]
    return any(REVENUE_LABEL.fullmatch(cell) for cell in cells[:2])


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

    revenue_index = next(
        (index for index, row in enumerate(rows) if _is_revenue_row(row)),
        None,
    )
    if revenue_index is None:
        return []

    product_indexes = []
    for index in range(revenue_index - 1, max(-1, revenue_index - 9), -1):
        label = _row_label(rows[index])
        if not label or re.search(r"成本|毛利|溢利|虧損|開支|費用|profit|loss", label, re.I):
            break
        if any(_number(cell) is not None for cell in rows[index]):
            product_indexes.append(index)
    product_indexes.reverse()

    total_index = revenue_index
    if not product_indexes:
        for index in range(revenue_index + 1, min(len(rows), revenue_index + 9)):
            label = _row_label(rows[index])
            if not label:
                if any(_number(cell) is not None for cell in rows[index]):
                    total_index = index
                break
            if re.search(r"成本|毛利|溢利|虧損|開支|費用|profit|loss", label, re.I):
                break
            if any(_number(cell) is not None for cell in rows[index]):
                product_indexes.append(index)

    if not product_indexes:
        return []

    facts = []
    for index in product_indexes:
        name = _clean_name(_row_label(rows[index]))
        for column, year in year_columns:
            amount = _number(rows[index][column]) if column < len(rows[index]) else None
            if amount is None:
                continue
            start, end = _period(year, context, text)
            facts.append(_fact(table, name, amount, start, end, currency, unit))
    for column, year in year_columns:
        amount = _number(rows[total_index][column]) if column < len(rows[total_index]) else None
        if amount is None:
            continue
        start, end = _period(year, context, text)
        facts.append(_fact(table, "合计", amount, start, end, currency, unit))
    return facts
