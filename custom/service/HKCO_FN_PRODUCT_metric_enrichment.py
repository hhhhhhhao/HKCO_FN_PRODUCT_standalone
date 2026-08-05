# -*- coding: utf-8 -*-
"""只从其他物理表为主表已有产品补成本和毛利。"""
import re

from custom.service.HKCO_FN_PRODUCT_extraction import COST, GROSS_PROFIT, _number, _year
from custom.service.HKCO_FN_PRODUCT_extraction.common import _column_header


def _product_key(value):
    return re.sub(r"\s+", "", str(value or "")).lower()


def _metric_year_columns(rows):
    width = max((len(row) for row in rows), default=0)
    columns = []
    for column in range(width):
        header = _column_header(rows, width, column)
        if re.search(r"附註|附注|%|毛利率|百分比|note", header, re.I):
            continue
        year = _year(header)
        if year:
            columns.append((column, year))
    return columns


def _metric_identity_column(rows, products):
    width = max((len(row) for row in rows), default=0)
    best_column, best_score = 0, -1
    for column in range(width):
        labels = [
            str(row[column] or "").strip()
            for row in rows
            if column < len(row) and str(row[column] or "").strip()
        ]
        score = sum(1 for label in labels if _product_key(label) in products)
        if score > best_score:
            best_column, best_score = column, score
    return best_column


def _enrich_from_metric_sections(related_inner_lines, main_inner_lines, revenue_facts):
    products = {}
    for fact in revenue_facts:
        products.setdefault(_product_key(fact["product_name"]), []).append(fact)
    if not products:
        return [], {"stage": "metric_enrichment_skipped", "source_tables": []}
    period_years = sorted(
        {fact["end_date"][:4] for facts in products.values() for fact in facts},
        reverse=True,
    )
    selected_physical_table = next(
        (line for line in main_inner_lines if line.get("is_table") and line.get("table")),
        None,
    )
    facts = []
    source_tables = []
    physical_index = 0
    for inner_lines in related_inner_lines or ():
        section_text = " ".join(line.get("text", "") for line in inner_lines)
        metric = ""
        if re.search(r"毛利|毛利率|gross profit", section_text, re.I):
            metric = "GROSS_PROFIT"
        elif re.search(r"成本|cost", section_text, re.I):
            metric = "MBCOST"
        if not metric:
            continue
        for table in inner_lines:
            if not table.get("is_table") or not table.get("table"):
                continue
            table_id = f"p{table.get('page_number', 'x')}:{physical_index}"
            physical_index += 1
            if table is selected_physical_table:
                continue
            rows = [list(row) for row in table["table"] if isinstance(row, (list, tuple))]
            year_columns = _metric_year_columns(rows)
            if not year_columns:
                continue
            identity_column = _metric_identity_column(rows, products)
            for row in rows:
                label = str(row[identity_column] or "").strip() if identity_column < len(row) else ""
                key = _product_key(label) if label else "合计"
                group = products.get(key)
                if group is None:
                    group = next(
                        (value for name, value in products.items() if key in name or name in key),
                        None,
                    )
                if group is None:
                    continue
                for index, (column, year) in enumerate(year_columns):
                    amount = _number(row[column]) if column < len(row) else None
                    if amount is None:
                        continue
                    target_year = period_years[index] if index < len(period_years) else year
                    reference = next(
                        (fact for fact in group if fact["end_date"][:4] == target_year),
                        None,
                    )
                    if reference is None:
                        continue
                    facts.append({
                        **reference,
                        "table_id": table_id,
                        "metric": metric,
                        "amount": amount,
                        "row_index": 0,
                        "column_index": column,
                    })
                    if table_id not in source_tables:
                        source_tables.append(table_id)
    unique = {}
    for fact in facts:
        key = (
            _product_key(fact["product_name"]),
            fact["start_date"],
            fact["end_date"],
            fact["metric"],
        )
        unique.setdefault(key, fact)
    facts = list(unique.values())
    return facts, {
        "stage": "metric_enrichment_finished",
        "source_tables": source_tables,
        "fact_count": len(facts),
    }


def enrich_metrics(related_inner_lines, main_inner_lines, revenue_facts, required_metrics):
    """其他表不能新增产品、期间或收入，只返回可回填的指标事实和 debug。"""
    required_metrics = set(required_metrics or ())
    if not main_inner_lines:
        return [], {"stage": "metric_enrichment_skipped", "source_tables": []}
    if not required_metrics:
        return _enrich_from_metric_sections(related_inner_lines, main_inner_lines, revenue_facts)
    selected_physical_table = next(
        (
            line for line in main_inner_lines
            if line.get("is_table") and line.get("table")
        ),
        None,
    )
    products = {}
    for fact in revenue_facts:
        products.setdefault(str(fact["product_name"]).strip().lower(), []).append(fact)
    facts, new_debug = _enrich_from_metric_sections(related_inner_lines, main_inner_lines, revenue_facts)
    old_facts = []
    physical_index = 0
    for inner_lines in related_inner_lines or ():
        for table in inner_lines:
            if not table.get("is_table") or not table.get("table"):
                continue
            table_id = f"p{table.get('page_number', 'x')}:{physical_index}"
            physical_index += 1
            if table is selected_physical_table:
                continue
            rows = table["table"]
            width = max((len(row) for row in rows), default=0)
            for column in range(width):
                header = " ".join(str(row[column] or "") for row in rows[:5] if column < len(row))
                metric = (
                    "MBCOST" if "MBCOST" in required_metrics and COST.search(header)
                    else "GROSS_PROFIT" if "GROSS_PROFIT" in required_metrics and GROSS_PROFIT.search(header)
                    else ""
                )
                year = _year(header)
                if not metric or not year:
                    continue
                for row_index, row in enumerate(rows[1:], start=1):
                    amount = _number(row[column]) if column < len(row) else None
                    if amount is None:
                        continue
                    product = next(
                        (
                            str(cell or "").strip().lower()
                            for cell in row
                            if str(cell or "").strip().lower() in products
                        ),
                        "",
                    )
                    if not product:
                        continue
                    reference = next(
                        (fact for fact in products[product] if int(fact["end_date"][:4]) == year),
                        None,
                    )
                    if reference:
                        old_facts.append({
                            **reference,
                            "table_id": table_id,
                            "metric": metric,
                            "amount": amount,
                            "row_index": row_index,
                            "column_index": column,
                        })
    unique = {}
    for fact in facts + old_facts:
        key = (
            str(fact["product_name"]).strip().lower(),
            fact["start_date"],
            fact["end_date"],
            fact["metric"],
        )
        unique.setdefault(key, fact)
    facts = list(unique.values())
    source_tables = list(dict.fromkeys(list(new_debug.get("source_tables", [])) + [fact["table_id"] for fact in facts]))
    return facts, {
        "stage": "metric_enrichment_finished",
        "source_tables": source_tables,
        "fact_count": len(facts),
    }
