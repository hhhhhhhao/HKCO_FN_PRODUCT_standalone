# -*- coding: utf-8 -*-
"""Compatibility gates shared by all cross-table fact assembly."""
from dataclasses import replace


def canonical_currency(value):
    text = str(value or "").strip()
    return {
        "人民幣": "人民币",
        "港幣": "港元",
        "歐元": "欧元",
        "日圓": "日元",
        "日圆": "日元",
    }.get(text, text)


def unit_scale(value):
    text = str(value or "").strip()
    if not text:
        return None
    if text in {"004", "百万元", "百萬元"} or "百萬" in text or "百万" in text:
        return 1_000_000
    if text in {"002", "千元", "千港元"} or "千" in text:
        return 1_000
    if text in {"001", "元", "港元", "人民币", "人民幣"}:
        return 1
    return None


def align_fact_measurement(fact, reference, stable_currency="", stable_unit=""):
    """Express a supplemental fact in the primary fact's measurement.

    Missing source measurement may inherit the primary measurement.  A known
    source measurement never defines a missing primary measurement: that would
    let a secondary table silently change the MBREVENUE skeleton.
    """
    target_currency = canonical_currency(reference.currency or stable_currency)
    source_currency = canonical_currency(fact.currency or stable_currency or target_currency)
    if target_currency and source_currency and target_currency != source_currency:
        return None, "currency_mismatch"
    if not target_currency and source_currency:
        return None, "primary_currency_unknown"

    target_unit = str(reference.unit or stable_unit or "").strip()
    source_unit = str(fact.unit or stable_unit or target_unit).strip()
    if not target_unit and source_unit:
        return None, "primary_unit_unknown"
    target_scale, source_scale = unit_scale(target_unit), unit_scale(source_unit)
    if target_unit and target_scale is None:
        return None, "unsupported_primary_unit"
    if source_unit and source_scale is None:
        return None, "unsupported_source_unit"

    amount = fact.amount
    if amount is not None and target_scale and source_scale:
        amount = amount * source_scale / target_scale
    return replace(
        fact,
        amount=amount,
        currency=reference.currency or stable_currency,
        unit=reference.unit or stable_unit,
    ), ""
