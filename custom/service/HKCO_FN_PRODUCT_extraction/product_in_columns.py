# -*- coding: utf-8 -*-
"""产品名纵向在第一列：按年份列取每行收入。"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

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
    width = max((len(row) for row in rows), default=0)
    text = table.get("period_text") or " ".join(str(cell or "") for row in rows for cell in row)
    currency, unit = get_currency([text]), get_unit([text])
    prior_names = context.get("prior_product_names") or ()

    year_columns = []
    # 检测"本年/上年"隐式年份：扫描前3行找到含关键词的列，分配隐式年份
    implicit_this_cols = set()
    implicit_prior_cols = set()
    for row in rows[:3]:
        for ci, cell in enumerate(row):
            cs = str(cell or "")
            if "本年" in cs or "本期" in cs:
                implicit_this_cols.add(ci)
            if "上年" in cs or "上期" in cs:
                implicit_prior_cols.add(ci)
    implicit_year_base = _year(text)

    for column in range(width):
        header = _column_header(rows, width, column)
        if _matches("note", header):
            continue
        year = _year(header)
        if year is None:
            if implicit_year_base and column in implicit_this_cols:
                year = implicit_year_base
            elif implicit_year_base and column in implicit_prior_cols:
                year = implicit_year_base - 1
            else:
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
    # 多段表同一年份有多列 → 只保留每年最后一列（總計列）
    deduped: Dict[int, Tuple[int, int]] = {}
    for col, year in year_columns:
        deduped[year] = (col, year)
    year_columns = list(deduped.values())

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

    # 规则：名称含 revenue 关键词且不在 prior_names 中 → 疑似分类标签。
    # 若有合计行且该标签金额等于合计 → 确认为分类汇总，移除。
    total_rows = final_rows or subtotal_rows
    if total_rows and facts:
        # 计算合计值
        import math
        total_amounts = {}
        for _name, row in total_rows:
            for column, year in year_columns:
                amt = _number(row[column]) if column < len(row) else None
                if amt is None: continue
                end = get_end_date(text) or f"{year}-12-31"
                if int(end[:4]) != year: end = f"{year}{end[4:]}"
                start = get_start_date(None, end, f"{year}年", text)
                total_amounts[(start, end)] = amt
        # 过滤
        kept = []
        sums = {}
        for f in facts:
            name = f["product_name"]
            # 分类标签特征：含收入/收益关键词 + 不在 prior_names 中
            # 附加条件避免误杀「銷售原鋁及合金」（含銷售但不是分类标签）
            is_category_label = (
                not _name_overlap(name, prior_names)
                and _matches("revenue", name)
                and (_matches("metric_contains", name) or _matches("revenue_label", name))
            )
            if is_category_label:
                period = (f["start_date"], f["end_date"])
                total = total_amounts.get(period)
                if total is not None and math.isclose(f["amount"], total, rel_tol=0.01, abs_tol=1):
                    continue  # 分类汇总，值与合计一致 → 跳过
            kept.append(f)
            key = (f["start_date"], f["end_date"])
            sums[key] = sums.get(key, 0) + f["amount"]
        facts = kept

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
