# -*- coding: utf-8 -*-
"""把已经抽取的事实转换为最终入库字段。"""
def _number(value):
    if value is None:
        return ""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _unit(value):
    text = str(value or "").strip()
    if "百万" in text or "百萬" in text:
        return "004"
    if "千" in text:
        return "002"
    if text:
        return "001"
    return ""


def format_records(revenue_facts, metric_facts):
    metrics = {
        (str(fact["product_name"]).strip().lower(), fact["start_date"], fact["end_date"], fact["metric"]): fact
        for fact in metric_facts or ()
    }
    records = []
    for fact in revenue_facts or ():
        key = (str(fact["product_name"]).strip().lower(), fact["start_date"], fact["end_date"])
        cost = metrics.get((*key, "MBCOST"), {})
        gross_profit = metrics.get((*key, "GROSS_PROFIT"), {})
        records.append({
            "PRODUCTNAME": fact["product_name"],
            "STARTDATE": fact["start_date"] + "T00:00:00.000Z",
            "REPORTDATE": fact["end_date"] + "T00:00:00.000Z",
            "CURRENCY": fact.get("currency", ""),
            "UNIT": _unit(fact.get("unit", "")),
            "MBREVENUE": _number(fact.get("amount")),
            "MBCOST": _number(cost.get("amount")),
            "GROSS_PROFIT": _number(gross_profit.get("amount")),
        })
    return records
