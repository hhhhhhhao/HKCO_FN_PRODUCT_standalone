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
    find_section_break,
    get_currency,
    get_end_date,
    get_start_date,
    get_unit,
)


def extract(table: Dict[str, Any], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = _rows(table)
    prior_names = context.get("prior_product_names") or ()
    rows = rows[:find_section_break(rows, prior_names)]
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
        # 回退 1：表内有收入关键词 → 找关键词下方数值最多的行
        has_revenue_context = any(
            _matches("revenue", " ".join(str(cell or "") for cell in row))
            for row in rows
        )
        if has_revenue_context:
            revenue_keyword_rows = [
                idx for idx, row in enumerate(rows)
                if _matches("revenue", " ".join(str(cell or "") for cell in row))
            ]
            best_fallback = None
            best_num_count = 1
            search_start = min(revenue_keyword_rows)
            for idx in range(search_start, min(len(rows), search_start + 8)):
                row = rows[idx]
                num_count = sum(1 for cell in row if _number(cell) is not None)
                row_text = " ".join(str(cell or "") for cell in row)
                if num_count > best_num_count and not _matches("pl_line", row_text):
                    best_num_count = num_count
                    best_fallback = idx
            if best_fallback is not None:
                metric_index = best_fallback
    if metric_index is None:
        # 回退 2：表内无收入关键词但有数值列 → 找数值最多的非表头行
        first_row_has_year = rows and any(_year(cell) is not None for cell in rows[0])
        data_col_count = sum(1 for col in range(len(rows[0]) if rows else 0)
                            if any(_number(row[col]) is not None for row in rows if col < len(row)))
        # 需要至少 1 列数据 + 表头有年份，或至少 2 列数据（多产品多年份）
        if (first_row_has_year and data_col_count >= 1) or data_col_count >= 2:
            best_idx = None
            best_cnt = 0
            for idx, row in enumerate(rows):
                num_count = sum(1 for cell in row if _number(cell) is not None)
                row_text = " ".join(str(cell or "") for cell in row)
                if _matches("header", row_text) or _matches("pl_line", row_text):
                    continue
                if num_count > best_cnt:
                    best_cnt = num_count
                    best_idx = idx
            if best_idx is not None:
                metric_index = best_idx
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
        # 回退：单行表格，第一列是产品名
        first_cell = str(metric_row[0] or "").strip() if metric_row else ""
        if (first_cell and _number(first_cell) is None
                and _year(first_cell) is None
                and first_cell not in {"-", "–", "—"}
                and not _matches("revenue_label", first_cell)
                and not _matches("metric", first_cell)):
            label_index = metric_index
        else:
            return []

    label_row = rows[label_index]
    full_width = max(len(metric_row), len(label_row))
    label_row = list(label_row) + [""] * (full_width - len(label_row))
    first_data_col = next(
        (c for c in range(len(metric_row)) if _number(metric_row[c]) is not None),
        0,
    )
    first_label_col = next(
        (c for c in range(len(label_row)) if label_row[c] != ""),
        0,
    )
    offset = first_data_col - first_label_col
    # 提前扫描"本年/上年"隐式年份关键词
    table_text_for_year = " ".join(str(c or "") for row in rows for c in row)
    implicit_year_base = _year(text) if ("本年" in table_text_for_year or "上年" in table_text_for_year) else None
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
            if implicit_year_base and ("本年" in header or "本期" in header):
                year = implicit_year_base
            elif implicit_year_base and ("上年" in header or "上期" in header):
                year = implicit_year_base - 1
            else:
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
