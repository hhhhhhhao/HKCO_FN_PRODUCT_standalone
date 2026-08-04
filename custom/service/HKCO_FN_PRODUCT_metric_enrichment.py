# -*- coding: utf-8 -*-
"""只从其他物理表为主表已有产品补成本和毛利。"""
import re

from custom.service.HKCO_FN_PRODUCT_extraction import COST, GROSS_PROFIT, _number, _year
from custom.service.HKCO_FN_PRODUCT_selector import identity_key


def _matches_product(value, products):
    key = identity_key(value)
    return key if key in products else None


def _row_metric_facts(table, products, required_metrics):
    rows = table.get("rows", [])
    width = max((len(row) for row in rows), default=0)
    facts = []
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
            product = next((_matches_product(cell, products) for cell in row if _matches_product(cell, products)), None)
            if not product:
                continue
            reference = next((fact for fact in products[product] if int(fact["end_date"][:4]) == year), None)
            if not reference:
                continue
            facts.append({
                **reference, "table_id": table["id"], "metric": metric, "amount": amount,
                "row_index": row_index, "column_index": column,
            })
    return facts


def enrich_metrics(sections, main_table, revenue_facts, required_metrics):
    """其他表不能新增产品、期间或收入，只返回可回填的指标事实和 debug。"""
    required_metrics = set(required_metrics or ())
    if not required_metrics or not main_table:
        return [], {"stage": "metric_enrichment_skipped", "source_tables": []}
    products = {}
    for fact in revenue_facts:
        products.setdefault(identity_key(fact["product_name"]), []).append(fact)
    metric_facts = []
    for section in sections or ():
        for table in section.get("tables", []):
            if table.get("id") == main_table.get("id"):
                continue
            metric_facts.extend(_row_metric_facts(table, products, required_metrics))
    unique = {}
    for fact in metric_facts:
        key = (identity_key(fact["product_name"]), fact["start_date"], fact["end_date"], fact["metric"])
        unique.setdefault(key, fact)
    metric_facts = list(unique.values())
    return metric_facts, {
        "stage": "metric_enrichment_finished",
        "source_tables": list(dict.fromkeys(fact["table_id"] for fact in metric_facts)),
        "fact_count": len(metric_facts),
    }
