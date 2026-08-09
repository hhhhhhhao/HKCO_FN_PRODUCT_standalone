# -*- coding: utf-8 -*-
"""损益表内嵌收入分项：收入行前后紧邻的产品行，收入行金额作为合计。"""
from __future__ import annotations

from typing import Any, Dict, List

from custom.service.HKCO_FN_PRODUCT_extraction.common import (
    PATTERNS,
    _column_header,
    _clean_name,
    _fact,
    _label_kind,
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


def _row_label(row) -> str:
    return str(row[0] or "").strip() if row else ""


def _is_revenue_row(row) -> bool:
    cells = [str(cell or "").strip() for cell in row]
    return any(_matches("revenue_label", cell) for cell in cells[:2])


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
        # 回退：表头无年份 → 从全文提取所有年份，赋予数值列
        import re as _re
        all_years = []
        for m in _re.finditer(PATTERNS["year"], text):
            y = _year(m.group(0))
            if y is not None and y not in all_years:
                all_years.append(y)
        all_years.sort()
        data_cols = [c for c in range(width) if c > 0 and any(
            c < len(row) and _number(row[c]) is not None for row in rows
        )]
        for i, col in enumerate(data_cols):
            y = all_years[i] if i < len(all_years) else (all_years[-1] if all_years else None)
            if y:
                year_columns.append((col, y))
    if not year_columns:
        return []

    revenue_index = next(
        (index for index, row in enumerate(rows) if _is_revenue_row(row)),
        None,
    )
    if revenue_index is None:
        # 回退：revenue_label 要求完整匹配，"收益:" 或 "收入 " 等会失败
        for index, row in enumerate(rows):
            cells = [str(cell or "").strip() for cell in row]
            if any(_matches("revenue", c) for c in cells[:2]) and any(_number(cell) is not None for cell in row):
                revenue_index = index
                break
    if revenue_index is None:
        # 回退：找含产品关键词或"-"前缀的行作为数据行
        for index, row in enumerate(rows):
            label = _row_label(row)
            if not label:
                continue
            if label.startswith("-") or _name_overlap(label, prior_names):
                if any(_number(cell) is not None for cell in row[1:]):
                    revenue_index = index
                    break
    if revenue_index is None:
        return []

    product_indexes = []
    for index in range(revenue_index - 1, max(-1, revenue_index - 9), -1):
        label = _row_label(rows[index])
        if _label_kind(label) in ("subtotal", "final"):
            break
        if not label or (_matches("pl_line", label) and not _name_overlap(label, prior_names)):
            break
        if any(_number(cell) is not None for cell in rows[index]):
            product_indexes.append(index)
    product_indexes.reverse()

    total_index = revenue_index
    if not product_indexes:
        for index in range(revenue_index + 1, min(len(rows), revenue_index + 9)):
            label = _row_label(rows[index])
            if _label_kind(label) in ("subtotal", "final"):
                break
            if not label:
                if any(_number(cell) is not None for cell in rows[index]):
                    total_index = index
                break
            if _matches("pl_line", label) and not _name_overlap(label, prior_names):
                break
            if any(_number(cell) is not None for cell in rows[index]):
                product_indexes.append(index)

    if not product_indexes:
        # 回退：产品名和数值分两行的格式（"-銷售金屬"行+下一行数值）
        for index in range(revenue_index + 1, min(len(rows), revenue_index + 30)):
            label = _row_label(rows[index])
            if label and label.startswith("-") and not any(_number(cell) is not None for cell in rows[index]):
                next_idx = index + 1
                if next_idx < len(rows) and any(_number(cell) is not None for cell in rows[next_idx]):
                    product_indexes.append(next_idx)
                    rows[next_idx] = list(rows[next_idx])
                    if len(rows[next_idx]) > 0:
                        rows[next_idx][0] = _clean_name(label)
            elif label and _label_kind(label) in ("subtotal", "final"):
                break
    if not product_indexes:
        # 纯损益表无产品分项：直接用收入行作为"合计"
        facts = []
        for column, year in year_columns:
            amount = _number(rows[revenue_index][column]) if column < len(rows[revenue_index]) else None
            if amount is None:
                continue
            end = get_end_date(text) or f"{year}-12-31"
            if int(end[:4]) != year:
                end = f"{year}{end[4:]}"
            start = get_start_date(None, end, f"{year}年", text)
            facts.append(_fact(table, "合计", amount, start, end, currency, unit))
        return facts

    facts = []
    sums = {}
    for index in product_indexes:
        name = _clean_name(_row_label(rows[index]))
        for column, year in year_columns:
            amount = _number(rows[index][column]) if column < len(rows[index]) else None
            if amount is None:
                continue
            end = get_end_date(text) or f"{year}-12-31"
            if int(end[:4]) != year:
                end = f"{year}{end[4:]}"
            start = get_start_date(None, end, f"{year}年", text)
            facts.append(_fact(table, name, amount, start, end, currency, unit))
            key = (start, end)
            sums[key] = sums.get(key, 0) + amount
    total_added = False
    for column, year in year_columns:
        amount = _number(rows[total_index][column]) if column < len(rows[total_index]) else None
        if amount is None:
            continue
        end = get_end_date(text) or f"{year}-12-31"
        if int(end[:4]) != year:
            end = f"{year}{end[4:]}"
        start = get_start_date(None, end, f"{year}年", text)
        facts.append(_fact(table, "合计", amount, start, end, currency, unit))
        total_added = True
    if not total_added:
        for (start, end), amount in sums.items():
            facts.append(_fact(table, "合计", amount, start, end, currency, unit))
    return facts
