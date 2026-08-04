# -*- coding: utf-8 -*-
"""只从其他物理表为主表已有产品补成本和毛利。"""
from custom.service.HKCO_FN_PRODUCT_extraction import COST, GROSS_PROFIT, _number, _year
def enrich_metrics(related_inner_lines, main_inner_lines, revenue_facts, required_metrics):
    """其他表不能新增产品、期间或收入，只返回可回填的指标事实和 debug。"""
    required_metrics = set(required_metrics or ())
    if not required_metrics or not main_inner_lines:
        return [], {"stage": "metric_enrichment_skipped", "source_tables": []}
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
    facts = []
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
                        facts.append({
                            **reference,
                            "table_id": table_id,
                            "metric": metric,
                            "amount": amount,
                            "row_index": row_index,
                            "column_index": column,
                        })
    unique = {}
    for fact in facts:
        key = (
            str(fact["product_name"]).strip().lower(),
            fact["start_date"],
            fact["end_date"],
            fact["metric"],
        )
        unique.setdefault(key, fact)
    facts = list(unique.values())
    return facts, {
        "stage": "metric_enrichment_finished",
        "source_tables": list(dict.fromkeys(fact["table_id"] for fact in facts)),
        "fact_count": len(facts),
    }
