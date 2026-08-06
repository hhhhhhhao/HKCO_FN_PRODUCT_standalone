# -*- coding: utf-8 -*-
"""公告级抽取入口：按表格 classification 分发到对应分类的抽取函数。"""
from __future__ import annotations

from custom.service.HKCO_FN_PRODUCT_extraction.common import (
    COST,
    GROSS_PROFIT,
    _matches,
    _number,
    _year,
)
from custom.service.HKCO_FN_PRODUCT_extraction import (
    product_in_columns,
    product_in_rows,
    profit_loss,
)


EXTRACTORS = {
    "product_in_rows": product_in_rows.extract,
    "product_in_columns": product_in_columns.extract,
    "profit_loss": profit_loss.extract,
}


def _canonical_product(name):
    return str(name or "").strip()


def _is_product(name):
    if not name or name == "合计":
        return False
    if _matches("metric", name) or _matches("subtotal", name):
        return False
    return True


def _merge_facts(base, extra):
    base_names = {
        str(fact.get("product_name")).replace(" ", "").lower()
        for fact in base
        if str(fact.get("product_name")) != "合计"
    }
    extra_names = {
        str(fact.get("product_name")).replace(" ", "").lower()
        for fact in extra
        if str(fact.get("product_name")) != "合计"
    }
    if base and extra and base_names and extra_names and not (base_names & extra_names):
        return list(base)

    # 两张表都有「合计」但金额不同 → 是不同的拆解维度（如总表 vs 子产品明细），
    # 只保留第一张表的合计和产品行，不合并第二张表。
    if base and extra and base_names and extra_names:
        base_totals = {
            (f.get("start_date"), f.get("end_date")): f.get("amount")
            for f in base if str(f.get("product_name")) == "合计"
        }
        extra_totals = {
            (f.get("start_date"), f.get("end_date")): f.get("amount")
            for f in extra if str(f.get("product_name")) == "合计"
        }
        common_keys = set(base_totals) & set(extra_totals)
        if common_keys and base_totals and extra_totals:
            # 任一期间合计值差异超过 1% → 不合并
            if any(
                abs((base_totals[k] or 0) - (extra_totals[k] or 0)) > abs(base_totals[k] or 0) * 0.01
                for k in common_keys
            ):
                return list(base)

    merged = {}
    for fact in base + extra:
        key = (
            str(fact.get("product_name")).replace(" ", "").lower(),
            fact.get("start_date"),
            fact.get("end_date"),
            fact.get("metric"),
        )
        merged.setdefault(key, fact)
    return list(merged.values())


def extract_main_table(main_inner_lines, context):
    measurement = " ".join(line.get("text", "") for line in main_inner_lines or ())
    facts = []
    for inner_lines in main_inner_lines or ():
        if not inner_lines.get("is_table") or not inner_lines.get("table"):
            continue
        extractor = EXTRACTORS.get(inner_lines.get("classification"))
        if extractor is None:
            continue
        inner_lines.setdefault("period_text", measurement)
        facts = _merge_facts(facts, extractor(inner_lines, context))
    return facts
