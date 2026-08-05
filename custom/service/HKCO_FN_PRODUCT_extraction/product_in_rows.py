# -*- coding: utf-8 -*-
"""产品名横向在表头行：收入行在产品列下方，逐产品列取收入。"""
from __future__ import annotations

from typing import Any, Dict, List

from custom.service.HKCO_FN_PRODUCT_extraction.common import (
    _clean_header,
    _column_header,
    _fact,
    _label_kind,
    _matches,
    _number,
    _name_overlap,
    _rows,
    _year,
    get_currency,
    get_end_date,
    get_start_date,
    get_unit,
)


def extract(table: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = _rows(table)
    text = table.get("period_text") or " ".join(str(cell or "") for row in rows for cell in row)
    currency, unit = get_currency([text]), get_unit([text])

    metric_index = None
    metric_priority = None
    for index, row in enumerate(rows):
        row_text = " ".join(str(cell or "") for cell in row)
        if _matches("revenue", row_text) and any(_number(cell) is not None for cell in row):
            if not _matches("pl_line", row_text):
                priority = 0 if _matches("external", row_text) else 1
                if metric_priority is None or priority < metric_priority:
                    metric_index = index
                    metric_priority = priority
    if metric_index is None:
        return []

    metric_row = rows[metric_index]
    label_index = None
    best_score = (-1, -1)
    for index in range(metric_index - 1, -1, -1):
        names = [
            _clean_header(cell)
            for cell in rows[index]
            if _clean_header(cell)
            and _year(cell) is None
            and _number(cell) is None
            and str(cell or "").strip() not in {"-", "–", "—"}
        ]
        if len(names) >= 2:
            prior_hits = sum(1 for name in names if _name_overlap(name, context.get("prior_product_names") or ()))
            score = (prior_hits, -index)
            if score > best_score:
                best_score = score
                label_index = index
    if label_index is None:
        return []

    label_row = rows[label_index]
    offset = max(0, len(metric_row) - len(label_row))
    facts = []
    final_columns = []
    subtotal_columns = []
    for column, cell in enumerate(label_row):
        name = _clean_header(cell)
        if not name:
            continue
        data_column = column + offset
        amount = _number(metric_row[data_column]) if data_column < len(metric_row) else None
        header = _column_header(rows, len(metric_row), data_column)
        year = _year(header)
        if year is None:
            year = _year(text)
        kind = _label_kind(name)
        if kind == "subtotal":
            if amount is not None and year is not None:
                subtotal_columns.append((name, amount, year))
            continue
        if kind == "final":
            if amount is not None and year is not None:
                final_columns.append((name, amount, year))
            continue
        if amount is None or year is None:
            continue
        end = get_end_date(text) or f"{year}-12-31"
        if int(end[:4]) != year:
            end = f"{year}{end[4:]}"
        start = get_start_date(None, end, f"{year}年", text)
        facts.append(_fact(table, name, amount, start, end, currency, unit))

    total_columns = final_columns or subtotal_columns
    if total_columns:
        for _name, amount, year in total_columns:
            end = get_end_date(text) or f"{year}-12-31"
            if int(end[:4]) != year:
                end = f"{year}{end[4:]}"
            start = get_start_date(None, end, f"{year}年", text)
            facts.append(_fact(table, "合计", amount, start, end, currency, unit))
    else:
        sums = {}
        for fact in facts:
            key = (fact["start_date"], fact["end_date"])
            sums[key] = sums.get(key, 0) + fact["amount"]
        for (start, end), amount in sums.items():
            facts.append(_fact(table, "合计", amount, start, end, currency, unit))
    return facts
