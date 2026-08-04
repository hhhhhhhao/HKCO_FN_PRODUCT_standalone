# -*- coding: utf-8 -*-
"""Classify only the shape and semantic axis of current revenue candidates.

The classifier does not rank tables and never reads prior amounts.  Every OCR
table is scanned, including segment reports, statements of profit or loss and
notes.  A table becomes a revenue candidate only when two independent facts
coexist: a current revenue relationship and a plausible identity axis.  Prior
product names are the strongest way to locate that axis; current identities may
freely be added, removed or renamed.
"""
import re

from custom.service.HKCO_FN_PRODUCT_fact_model import TableClassification


DETAIL = re.compile(r"明細|明细|分析|分拆|細分|细分|分類|分类|劃分|划分|構成|构成", re.I)


class TableClassifier:
    def classify(self, evidence):
        return [self._classify(item) for item in evidence]

    @staticmethod
    def _axis(item):
        axes = item.axis_signals
        # An axis stated in the current title/header is stronger than a weak
        # continuity collision such as the generic identity "其他".  Body-only
        # non-product signals do not get this privilege because a single table
        # may contain product, customer and geography sections together.
        for signal, axis in (
            ("geography", "geography"),
            ("sales_channel", "sales_channel"),
            ("customer", "customer"),
            ("recognition_time", "recognition_time"),
            ("measurement_method", "measurement_method"),
        ):
            if signal in axes and "body_product_service" not in axes:
                return axis
        # Continuity locates the relevant identity axis inside mixed tables.
        # A later geography/channel section must not re-label matched product
        # rows merely because the whole table contains several disclosures.
        if item.prior_identity_hits:
            if "body_product_service" in axes:
                return "product_service"
            if "body_business" in axes:
                return "business"
            if "business" in axes and "product_service" not in axes:
                return "business"
            if "product_service" in axes:
                return "product_service"
        # Explicit current non-product dimensions always win over continuity.
        for signal, axis in (
            ("body_geography", "geography"),
            ("body_sales_channel", "sales_channel"),
            ("body_recognition_time", "recognition_time"),
            ("body_measurement_method", "measurement_method"),
        ):
            if signal in axes:
                return axis
        if "body_business" in axes:
            return "business"
        if "body_product_service" in axes:
            return "product_service"
        # A prior hit locates the identity axis but does not dictate its name.
        # Current segment wording therefore remains business; all other valid
        # revenue identities use the task's product/service namespace.
        if item.prior_identity_hits:
            return "business" if "business" in axes else "product_service"
        for axis in ("geography", "sales_channel", "customer",
                     "recognition_time", "measurement_method"):
            if axis in axes:
                return axis
        if "business" in axes:
            return "business"
        if "product_service" in axes:
            return "product_service"
        if ("row_identity" in item.layout_signals
                and ((item.revenue_relation in {"title", "embedded_heading"}
                      and DETAIL.search(item.table.title))
                     or ("revenue" in item.title_signals
                         and "mixed_measurement_columns" in item.layout_signals))):
            return "product_service"
        return "unknown"

    @staticmethod
    def _geometry(item):
        signals = item.layout_signals
        # Prior identity location is the most reliable orientation evidence.
        if "multi_section" in signals:
            return "multi_section_row"
        if item.prior_axis == "row" and "row_identity" in signals:
            if "explicit_total_column" in signals:
                return "row_identity_total_period"
            if "mixed_measurement_columns" in signals:
                return "row_measurement_period"
            if ("multi_financial_metric_columns" in signals
                    or ("repeated_period_columns" in signals
                        and "metric_columns" in signals
                        and "amount_percentage_columns" not in signals)):
                return "row_metric_period"
            if "hierarchy" in signals:
                return "mixed_hierarchy"
            return "row_period"
        if "mixed_measurement_columns" in signals and "row_identity" in signals:
            return "row_measurement_period"
        if (item.prior_axis == "column" and item.prior_column_hits
                and "revenue_metric_row" in signals):
            return "segment_matrix_period"
        if "column_identity" in signals and "revenue_metric_row" in signals:
            return "segment_matrix_period"
        if ("row_identity" in signals and (
                "multi_financial_metric_columns" in signals
                or ("repeated_period_columns" in signals
                    and "metric_columns" in signals
                    and "amount_percentage_columns" not in signals))):
            return "row_metric_period"
        if "explicit_total_column" in signals and "row_identity" in signals:
            return "row_identity_total_period"
        if "hierarchy" in signals:
            return "mixed_hierarchy"
        if "row_identity" in signals:
            return "row_period"
        return "unsupported"

    def _classify(self, item):
        axis = self._axis(item)
        table_type = self._geometry(item)
        reasons = []
        title = item.title_signals
        if not item.revenue_relation:
            reasons.append("no_current_revenue_relationship")
        if axis not in {"product_service", "business"}:
            reasons.append("non_product_identity_axis")
        if "non_revenue_measure" in title:
            reasons.append("explicit_non_revenue_measure")
        if "aging_schedule" in title:
            reasons.append("non_revenue_aging_schedule")
        if "other_income" in title and "primary_with_other_income" not in title:
            reasons.append("non_primary_revenue_title")
        if "other_income_section_only" in title:
            reasons.append("continuation_contains_only_other_income")
        if "alternative_basis" in title:
            reasons.append("alternative_revenue_basis")
        if ("expense_ledger" in item.layout_signals
                and item.revenue_relation in {"title", "identity_total"}
                and not item.prior_identity_hits):
            reasons.append("expense_ledger_without_revenue_metric")
        if ("metric_ledger" in item.layout_signals and not item.prior_identity_hits
                and not (
                    "financial_statement" in title
                    and "total_row" in item.layout_signals
                    and "body_product_service" in item.axis_signals
                )):
            reasons.append("metric_ledger_requires_identity_materializer")
        if ("financial_statement" in title and not item.prior_identity_hits
                and axis == "unknown"):
            reasons.append("financial_statement_requires_identity_anchor")
        if ("expense" in title
                and "multi_financial_metric_columns" not in item.layout_signals
                and "revenue_metric_row" not in item.layout_signals):
            reasons.append("cost_or_expense_disclosure")
        if table_type == "unsupported":
            reasons.append("unsupported_candidate_geometry")
        if table_type == "multi_section_row":
            section_axes = {marker["axis"] for marker in item.section_markers}
            if "product_service" not in section_axes:
                reasons.append("no_product_service_section")
            else:
                axis = "product_service"
                reasons = [reason for reason in reasons if reason != "non_product_identity_axis"]
        basis = "alternative" if "alternative_basis" in title else "reported"
        return TableClassification(
            item, table_type, axis, basis, not reasons, reasons,
        )
