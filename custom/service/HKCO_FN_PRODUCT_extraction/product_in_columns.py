# -*- coding: utf-8 -*-
"""产品名纵向在第一列：按年份列取每行收入。"""
from __future__ import annotations

from typing import Any, Dict, List

from custom.service.HKCO_FN_PRODUCT_extraction.common import (
    _column_header,
    _clean_name,
    _fact,
    _label_kind,
    _is_header_row,
    _matches,
    _name_overlap,
    _number,
    _rows,
    _year,
    get_currency,
    get_end_date,
    get_start_date,
    get_unit,
)


def extract(table: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = _rows(table)
    width = max((len(row) for row in rows), default=0)
    text = table.get("period_text") or " ".join(str(cell or "") for row in rows for cell in row)
    currency, unit = get_currency([text]), get_unit([text])
    prior_names = context.get("prior_product_names") or ()

    year_columns = []
    for column in range(width):
        header = _column_header(rows, width, column)
        if _matches("note", header):
            continue
        year = _year(header)
        if year is None:
            year = _year(text)
        if year and any(
            column < len(row) and _number(row[column]) is not None
            for row in rows
        ):
            year_columns.append((column, year))
    if not year_columns:
        return []
    revenue_year_columns = [
        (column, year)
        for column, year in year_columns
        if _matches("revenue", _column_header(rows, width, column))
    ]
    if revenue_year_columns:
        year_columns = revenue_year_columns

    identity_column = 0
    best_score = (-1, -1, -1)
    for column in range(width):
        labels = [
            _clean_name(row[column])
            for row in rows
            if column < len(row) and _clean_name(row[column])
        ]
        prior_hits = sum(1 for label in labels if _name_overlap(label, prior_names))
        cjk_count = sum(1 for label in labels if _matches("cjk", label))
        score = (prior_hits, len(labels), cjk_count)
        if score > best_score:
            best_score = score
            identity_column = column

    facts = []
    sums = {}
    final_rows = []
    subtotal_rows = []
    last_data_index = max(
        (
            index
            for index, row in enumerate(rows)
            if any(
                column < len(row) and _number(row[column]) is not None
                for column, _ in year_columns
            )
        ),
        default=-1,
    )
    for row_index, row in enumerate(rows):
        if not row:
            continue
        if _is_header_row(row):
            continue
        name = _clean_name(row[identity_column]) if identity_column < len(row) else ""
        if not name:
            if row_index == last_data_index and any(_number(cell) is not None for cell in row[1:3]):
                final_rows.append(("", row))
            continue
        kind = _label_kind(name)
        if kind == "subtotal":
            subtotal_rows.append((name, row))
            continue
        if kind == "final":
            final_rows.append((name, row))
            continue
        if _matches("metric", name):
            continue
        if not _name_overlap(name, prior_names) and _matches("metric_contains", name) and not _matches("revenue", name):
            continue
        if _name_overlap(name, prior_names):
            pass
        elif _matches("recognition", name):
            continue
        for column, year in year_columns:
            amount = _number(row[column]) if column < len(row) else None
            if amount is None:
                continue
            end = get_end_date(text) or f"{year}-12-31"
            if int(end[:4]) != year:
                end = f"{year}{end[4:]}"
            start = get_start_date(None, end, f"{year}年", text)
            facts.append(_fact(table, name, amount, start, end, currency, unit))
            key = (start, end)
            sums[key] = sums.get(key, 0) + amount

    total_rows = final_rows or subtotal_rows
    if total_rows:
        for _name, row in total_rows:
            for column, year in year_columns:
                amount = _number(row[column]) if column < len(row) else None
                if amount is None:
                    continue
                end = get_end_date(text) or f"{year}-12-31"
                if int(end[:4]) != year:
                    end = f"{year}{end[4:]}"
                start = get_start_date(None, end, f"{year}年", text)
                facts.append(_fact(table, "合计", amount, start, end, currency, unit))
    else:
        for (start, end), amount in sums.items():
            facts.append(_fact(table, "合计", amount, start, end, currency, unit))
    return facts
