# -*- coding: utf-8 -*-
"""Project assembled current facts into the public record shape."""
from custom.service.HKCO_FN_PRODUCT_extractors import _identity_key


class RecordProjector:
    def project(self, revenue_facts, metric_facts):
        metrics = {
            (_identity_key(fact.product_name), fact.start_date, fact.end_date, fact.metric): fact
            for fact in metric_facts
        }
        records, seen = [], set()
        for fact in revenue_facts:
            key = (_identity_key(fact.product_name), fact.start_date, fact.end_date)
            if key in seen:
                continue
            seen.add(key)
            cost = metrics.get((*key, "MBCOST"))
            profit = metrics.get((*key, "GROSS_PROFIT"))
            records.append({
                "product_name": fact.product_name,
                "mbrevenue": fact.amount,
                "mbcost": cost.amount if cost else "",
                "gross_profit": profit.amount if profit else "",
                "start_date": fact.start_date,
                "end_date": fact.end_date,
                "currency": fact.currency,
                "unit": fact.unit,
            })
        return records

