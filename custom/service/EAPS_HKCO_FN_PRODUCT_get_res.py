# -*- coding: utf-8 -*-
"""Public entry for the HKCO product-disclosure pipeline.

Keep this module small.  Pipeline orchestration belongs in
``HKCO_FN_PRODUCT_pipeline``; structure-specific parsers belong in dedicated
materializer modules.  The legacy implementation is imported only through the
temporary compatibility boundary below.
"""
from custom.service.HKCO_FN_PRODUCT_legacy import _legacy_get_res_adapter
from custom.service.HKCO_FN_PRODUCT_evidence import table_refs_from_sources
from custom.service.HKCO_FN_PRODUCT_pipeline import build_default_pipeline


def _prior_consensus(items, field):
    values = [str(item.get(field) or "").strip() for item in items or []
              if isinstance(item, dict) and str(item.get(field) or "").strip()]
    return max(set(values), key=values.count) if values else ""


def get_res(selected, info_code, reason_arr, notice_date="", last_period_data=None,
            source_tables=None, document_period_text=""):
    """Run the single-track pipeline, with explicit unsupported fallback."""
    sources = list(source_tables or [])
    if isinstance(selected, dict) and selected.get("target_table"):
        sources.append(selected)
    prior_names = [str(item.get("PRODUCTNAME") or "").strip()
                   for item in last_period_data or [] if isinstance(item, dict)
                   and str(item.get("PRODUCTNAME") or "").strip() not in ("合计", "合計")]
    records, decision = build_default_pipeline().run(
        table_refs_from_sources(sources),
        context={"prior_product_names": prior_names},
    )
    if records:
        stable_currency = _prior_consensus(last_period_data, "CURRENCY")
        stable_unit = _prior_consensus(last_period_data, "UNIT")
        for record in records:
            if stable_currency:
                record["currency"] = stable_currency
            if stable_unit:
                record["unit"] = stable_unit
        selected_hypothesis = decision.selected
        return {
            "target_res": records,
            "pipe_meta": {
                "pipeline_framework": "evidence_hypothesis_materialization",
                "materializer_boundary": "native",
                "selection_mode": "hypothesis_only",
                "selected_count": len(selected_hypothesis.table_ids),
                "source_pages": [selected_hypothesis.evidence.get("page")],
                "selected_hypothesis": selected_hypothesis.hypothesis_id,
                "selected_kind": selected_hypothesis.kind,
                "semantic_axis": selected_hypothesis.semantic_axis,
                "granularity": selected_hypothesis.granularity,
                "revenue_basis": selected_hypothesis.revenue_basis,
                "validations": [
                    {"dimension": item.dimension, "compatible": item.compatible,
                     "reason": item.reason}
                    for item in selected_hypothesis.validations
                ],
                "rejected_hypotheses": [
                    {
                        "hypothesis_id": item.hypothesis_id,
                        "kind": item.kind,
                        "role": item.semantic_axis,
                        "fact_count": len(item.facts),
                        "fact_identities": [fact.product_name for fact in item.facts[:20]],
                        "rejection_reasons": [check.reason for check in item.validations if not check.compatible],
                    }
                    for item in decision.rejected
                ],
                "evidence_summary": [
                    {
                        "table_id": item.table.table_id,
                        "page": item.table.page,
                        "title": item.table.title,
                        "metrics": sorted(item.candidate_metrics),
                        "axes": sorted(item.candidate_axes),
                        "sections": item.semantic_sections,
                        "first_labels": [str(row[0] or "").strip() for row in item.table.rows[:12] if row],
                    }
                    for item in decision.all_evidence
                ],
                "merged_metric_facts": [
                    {"table_id": fact.table_id, "metric": fact.metric,
                     "product_name": fact.product_name, "amount": fact.amount,
                     "currency": fact.currency, "unit": fact.unit}
                    for fact in decision.metric_facts
                ],
            },
        }
    result = _legacy_get_res_adapter(
        selected,
        info_code,
        reason_arr,
        notice_date=notice_date,
        last_period_data=last_period_data,
        source_tables=source_tables,
        document_period_text=document_period_text,
    )
    result.setdefault("pipe_meta", {}).update({
        "pipeline_framework": "evidence_hypothesis_materialization",
        "materializer_boundary": "legacy",
        "fallback_reason": decision.debug.get("stage", "unsupported_structure"),
        "native_rejected_hypotheses": [
            {
                "hypothesis_id": item.hypothesis_id,
                "kind": item.kind,
                "role": item.semantic_axis,
                "fact_count": len(item.facts),
                "rejection_reasons": [check.reason for check in item.validations if not check.compatible],
            }
            for item in decision.rejected
        ],
        "native_evidence_summary": [
            {
                "table_id": item.table.table_id,
                "page": item.table.page,
                "title": item.table.title,
                "metrics": sorted(item.candidate_metrics),
                "axes": sorted(item.candidate_axes),
                "structures": sorted(item.structure_signals),
                "sections": item.semantic_sections,
                "first_labels": [str(row[0] or "").strip() for row in item.table.rows[:12] if row],
            }
            for item in decision.all_evidence
        ],
    })
    return result
