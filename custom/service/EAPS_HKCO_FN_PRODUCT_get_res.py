# -*- coding: utf-8 -*-
"""Public entry for the HKCO product-disclosure pipeline.

Keep this module small.  Pipeline orchestration belongs in
``HKCO_FN_PRODUCT_pipeline``; structure-specific parsers belong in dedicated
extractor modules. Unsupported structures remain explicit pipeline failures
so that missing structural coverage cannot be hidden by a second extractor.
"""
import re
import calendar
import datetime

from custom.service.HKCO_FN_PRODUCT_evidence import table_refs_from_sources
from custom.service.HKCO_FN_PRODUCT_pipeline import build_default_pipeline


def _prior_consensus(items, field):
    values = [str(item.get(field) or "").strip() for item in items or []
              if isinstance(item, dict) and str(item.get(field) or "").strip()]
    return max(set(values), key=values.count) if values else ""


def _canonical_currency(value):
    return {
        "人民幣": "人民币",
        "港幣": "港元",
        "歐元": "欧元",
        "日圓": "日元",
        "日圆": "日元",
    }.get(str(value or "").strip(), str(value or "").strip())


def _prior_fiscal_month_day(items):
    values = []
    for item in items or []:
        start_match = re.search(
            r"(20\d{2})[/\-](\d{1,2})[/\-](\d{1,2})", str(item.get("STARTDATE") or "")
        )
        end_match = re.search(
            r"(20\d{2})[/\-](\d{1,2})[/\-](\d{1,2})", str(item.get("REPORTDATE") or "")
        )
        if not end_match:
            continue
        end = datetime.date(*map(int, end_match.groups()))
        duration = 12
        if start_match:
            start = datetime.date(*map(int, start_match.groups()))
            duration = max(1, (end.year - start.year) * 12 + end.month - start.month + 1)
        remaining = max(0, 12 - min(duration, 12))
        month_index = end.year * 12 + end.month - 1 + remaining
        fiscal_year, zero_month = divmod(month_index, 12)
        fiscal_month = zero_month + 1
        month_end = end.day == calendar.monthrange(end.year, end.month)[1]
        fiscal_day = (calendar.monthrange(fiscal_year, fiscal_month)[1]
                      if month_end else min(end.day, calendar.monthrange(fiscal_year, fiscal_month)[1]))
        values.append((fiscal_month, fiscal_day))
    return max(set(values), key=values.count) if values else ()


def get_res(selected, info_code, reason_arr, notice_date="", last_period_data=None,
            source_tables=None, document_period_text=""):
    """Run the single-track evidence/fact pipeline."""
    sources = list(source_tables or [])
    if isinstance(selected, dict) and selected.get("target_table"):
        sources.append(selected)
    prior_names = [str(item.get("PRODUCTNAME") or "").strip()
                   for item in last_period_data or [] if isinstance(item, dict)
                   and str(item.get("PRODUCTNAME") or "").strip() not in ("合计", "合計")]
    records, decision = build_default_pipeline().run(
        table_refs_from_sources(sources),
        context={
            "prior_product_names": prior_names,
            "prior_fiscal_month_day": _prior_fiscal_month_day(last_period_data),
            "stable_currency": _prior_consensus(last_period_data, "CURRENCY"),
            "stable_unit": _prior_consensus(last_period_data, "UNIT"),
            "document_period_text": document_period_text,
        },
    )
    if records:
        stable_currency = _prior_consensus(last_period_data, "CURRENCY")
        stable_unit = _prior_consensus(last_period_data, "UNIT")
        for record in records:
            current_currency = record.get("currency") or stable_currency
            if current_currency:
                record["currency"] = _canonical_currency(current_currency)
            if stable_unit and not record.get("unit"):
                record["unit"] = stable_unit
        selected_result = decision.selected
        selected_class = selected_result.classification
        selected_table = selected_class.evidence.table
        accepted_table_ids = list(dict.fromkeys(
            decision.debug.get("materialized_tables", [])
            + decision.debug.get("supplemental_tables", [])
        ))
        page_by_table = {
            item.table.table_id: item.table.page for item in decision.all_evidence
        }
        source_pages = list(dict.fromkeys(
            page_by_table[table_id] for table_id in accepted_table_ids
            if page_by_table.get(table_id) is not None
        ))
        return {
            "target_res": records,
            "pipe_meta": {
                "pipeline_framework": "table_classification_extraction",
                "materializer_boundary": "native",
                "selection_mode": "classified_table_only",
                "selected_count": len(accepted_table_ids),
                "source_pages": source_pages,
                "selected_table": selected_table.table_id,
                "selected_kind": selected_class.table_type,
                "semantic_axis": selected_class.semantic_axis,
                "revenue_basis": selected_class.revenue_basis,
                "closed_periods": sorted(selected_result.closed_periods),
                "planned_tables": decision.debug.get("planned_tables", []),
                "materialized_tables": decision.debug.get("materialized_tables", []),
                "supplemental_tables": decision.debug.get("supplemental_tables", []),
                "metric_merge_rejections": decision.debug.get("metric_merge_rejections", []),
                "rejected_hypotheses": [
                    {
                        "table_id": item.classification.evidence.table.table_id,
                        "kind": item.classification.table_type,
                        "role": item.classification.semantic_axis,
                        "fact_count": len(item.facts),
                        "fact_identities": [fact.product_name for fact in item.facts[:20]],
                        "rejection_reasons": item.rejection_reasons,
                    }
                    for item in decision.rejected
                ],
                "table_classifications": [
                    {
                        "table_id": item.evidence.table.table_id,
                        "page": item.evidence.table.page,
                        "title": item.evidence.table.title,
                        "table_type": item.table_type,
                        "semantic_axis": item.semantic_axis,
                        "supported": item.supported,
                        "reasons": item.reasons,
                    }
                    for item in decision.classifications
                ],
                "evidence_summary": [
                    {
                        "table_id": item.table.table_id,
                        "page": item.table.page,
                        "title": item.table.title,
                        "title_signals": sorted(item.title_signals),
                        "axes": sorted(item.axis_signals),
                        "structures": sorted(item.layout_signals),
                        "sections": item.section_markers,
                        "identity_axis": item.identity_axis,
                        "prior_axis": item.prior_axis,
                        "prior_identity_hits": item.prior_identity_hits,
                        "prior_row_hits": item.prior_row_hits,
                        "prior_column_hits": item.prior_column_hits,
                        "prior_identity_strength": item.prior_identity_strength,
                        "prior_matched_row_keys": item.prior_matched_row_keys,
                        "prior_matched_column_keys": item.prior_matched_column_keys,
                        "prior_identity_coverage": item.prior_identity_coverage,
                        "unit_continuity": item.unit_continuity,
                        "revenue_relation": item.revenue_relation,
                        "current_identity_count": item.current_identity_count,
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
    return {
        "target_res": [],
        "pipe_meta": {
            "pipeline_framework": "table_classification_extraction",
            "materializer_boundary": "native",
            "selection_mode": "no_qualified_classified_table",
            "failure_stage": decision.debug.get("stage", "unsupported_structure"),
            "planned_tables": decision.debug.get("planned_tables", []),
            "rejected_hypotheses": [
                {
                    "table_id": item.classification.evidence.table.table_id,
                    "kind": item.classification.table_type,
                    "role": item.classification.semantic_axis,
                    "fact_count": len(item.facts),
                    "rejection_reasons": item.rejection_reasons,
                }
                for item in decision.rejected
            ],
            "evidence_summary": [
                {
                    "table_id": item.table.table_id,
                    "page": item.table.page,
                    "title": item.table.title,
                    "title_signals": sorted(item.title_signals),
                    "axes": sorted(item.axis_signals),
                    "structures": sorted(item.layout_signals),
                    "sections": item.section_markers,
                    "identity_axis": item.identity_axis,
                    "prior_axis": item.prior_axis,
                    "prior_identity_hits": item.prior_identity_hits,
                    "prior_row_hits": item.prior_row_hits,
                    "prior_column_hits": item.prior_column_hits,
                    "prior_identity_strength": item.prior_identity_strength,
                    "prior_matched_row_keys": item.prior_matched_row_keys,
                    "prior_matched_column_keys": item.prior_matched_column_keys,
                    "prior_identity_coverage": item.prior_identity_coverage,
                    "unit_continuity": item.unit_continuity,
                    "revenue_relation": item.revenue_relation,
                    "current_identity_count": item.current_identity_count,
                    "first_labels": [str(row[0] or "").strip() for row in item.table.rows[:12] if row],
                }
                for item in decision.all_evidence
            ],
            "table_classifications": [
                {
                    "table_id": item.evidence.table.table_id,
                    "page": item.evidence.table.page,
                    "title": item.evidence.table.title,
                    "table_type": item.table_type,
                    "semantic_axis": item.semantic_axis,
                    "supported": item.supported,
                    "reasons": item.reasons,
                }
                for item in decision.classifications
            ],
        },
    }
