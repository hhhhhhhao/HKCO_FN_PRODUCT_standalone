# -*- coding: utf-8 -*-
"""Materialize a revenue plan and assemble compatible cross-table facts."""
from custom.service.HKCO_FN_PRODUCT_compatibility import align_fact_measurement
from custom.service.HKCO_FN_PRODUCT_extractors import (
    EXPLICIT_METRIC_EXTRACTOR,
    EXTRACTORS,
    _close,
    _identity_key,
)
from custom.service.HKCO_FN_PRODUCT_fact_model import ExtractionResult


class RevenuePlanMaterializer:
    """Materialize exactly the already-chosen primary plan.

    Plan discovery is a semantic decision, not a list of extraction fallbacks.
    If the chosen plan has an unsupported geometry, returning an explicit
    failure is safer than allowing a lower-ranked table of another granularity
    to acquire primary-fact status merely because its parser succeeds.
    """

    def materialize_primary(self, plan):
        attempted = []
        if not plan.primary_candidates:
            return None, attempted, []
        candidate = plan.primary_candidates[0]
        parts = [
            EXTRACTORS[classification.table_type].extract(classification)
            for classification in candidate.tables
        ]
        if len(parts) == 1:
            result = parts[0]
        else:
            result = _close(ExtractionResult(
                candidate.tables[0],
                [fact for part in parts for fact in part.facts],
            ))
            if result.closed_periods:
                result.rejection_reasons = []
                result.facts = [
                    fact for fact in result.facts
                    if fact.metric != "MBREVENUE"
                    or (fact.start_date, fact.end_date) in result.closed_periods
                ]
        attempted.append(result)
        return (result, attempted, parts) if result.qualified else (None, attempted, parts)


class SupplementalRevenueAssembler:
    """Add only explicitly compatible current-period revenue disclosures.

    Equal totals alone never authorize a merge: geography, channel and product
    disclosures commonly share the same total.  Compatibility first requires
    the same semantic axis/basis/period/measurement.  Within that boundary, a
    strictly finer disclosure replaces coarse identities instead of being
    unioned with them; otherwise parents and leaves would be double reported.
    """

    def assemble(self, primary, plan, classifications, context=None):
        context = context or {}
        revenue = [fact for fact in primary.facts if fact.metric == "MBREVENUE"]
        metrics = [fact for fact in primary.facts if fact.metric != "MBREVENUE"]
        accepted = []
        # Same total and a coarse shared axis do not establish one disclosure
        # plan: geography, industry and operating-segment tables often satisfy
        # both.  Only an explicitly supplemental table may enter here; period
        # siblings and continuations were already fixed in the primary plan.
        candidates = list(plan.supplemental_candidates)
        primary_totals = self._totals(revenue)
        for classification in candidates:
            if classification.semantic_axis != primary.classification.semantic_axis:
                continue
            if classification.revenue_basis != primary.classification.revenue_basis:
                continue
            explicitly_supplemental = classification in plan.supplemental_candidates
            result = EXTRACTORS[classification.table_type].extract(classification)
            if not result.qualified:
                continue
            compatible_periods = self._compatible_periods(
                revenue, primary_totals, result.facts, self._totals(result.facts), context
            )
            if not compatible_periods:
                continue
            existing = {
                (_identity_key(fact.product_name), fact.start_date, fact.end_date)
                for fact in revenue if fact.product_name != "合计"
            }
            raw_additions = [
                fact for fact in result.facts
                if fact.metric == "MBREVENUE" and fact.product_name != "合计"
                and (fact.start_date, fact.end_date) in compatible_periods
                and (_identity_key(fact.product_name), fact.start_date, fact.end_date) not in existing
            ]
            references = {
                (fact.start_date, fact.end_date): fact for fact in revenue
                if fact.metric == "MBREVENUE" and fact.product_name == "合计"
            }
            additions = []
            for fact in raw_additions:
                reference = references.get((fact.start_date, fact.end_date))
                if reference is None:
                    continue
                aligned, _ = align_fact_measurement(
                    fact,
                    reference,
                    context.get("stable_currency", ""),
                    context.get("stable_unit", ""),
                )
                if aligned is not None:
                    additions.append(aligned)
            if not additions:
                continue
            # A same-axis table with more current identities and the same
            # disclosed total is a finer disclosure, not an additive source.
            # Replace the coarse skeleton for those periods to avoid emitting
            # parent and leaf identities together.
            replacement_periods = {
                period for period in compatible_periods
                if self._identity_count(result.facts, period)
                > self._identity_count(revenue, period)
            }
            if replacement_periods:
                revenue = [
                    fact for fact in revenue
                    if (fact.start_date, fact.end_date) not in replacement_periods
                ]
                revenue.extend(
                    fact for fact in result.facts
                    if fact.metric == "MBREVENUE"
                    and (fact.start_date, fact.end_date) in replacement_periods
                )
            revenue.extend(
                fact for fact in additions
                if explicitly_supplemental
                and (fact.start_date, fact.end_date) not in replacement_periods
            )
            if not replacement_periods and not explicitly_supplemental:
                continue
            metrics.extend(fact for fact in result.facts if fact.metric != "MBREVENUE")
            accepted.append(result)
        return revenue, metrics, accepted

    @staticmethod
    def _identity_count(facts, period):
        return len({
            _identity_key(fact.product_name) for fact in facts
            if fact.metric == "MBREVENUE" and fact.product_name != "合计"
            and (fact.start_date, fact.end_date) == period
        })

    @staticmethod
    def _totals(facts):
        return {
            (fact.start_date, fact.end_date): fact.amount
            for fact in facts if fact.metric == "MBREVENUE" and fact.product_name == "合计"
        }

    @staticmethod
    def _compatible_periods(primary_facts, primary_totals, supplement_facts, supplement_totals,
                            context=None):
        context = context or {}
        primary_total_facts = {
            (fact.start_date, fact.end_date): fact for fact in primary_facts
            if fact.metric == "MBREVENUE" and fact.product_name == "合计"
        }
        supplement_total_facts = {
            (fact.start_date, fact.end_date): fact for fact in supplement_facts
            if fact.metric == "MBREVENUE" and fact.product_name == "合计"
        }
        compatible = set()
        for period in set(primary_totals) & set(supplement_totals):
            left, right = primary_totals[period], supplement_totals[period]
            if left is None or right is None:
                continue
            aligned, _ = align_fact_measurement(
                supplement_total_facts[period],
                primary_total_facts[period],
                context.get("stable_currency", ""),
                context.get("stable_unit", ""),
            )
            if aligned is None:
                continue
            right = aligned.amount
            if abs(left - right) <= max(1.0, abs(left) * 1e-8):
                compatible.add(period)
        return compatible


class ExplicitMetricFactCollector:
    """Scan all tables for explicit cost/GP facts after revenue identities are fixed."""

    def collect(self, classifications, primary, revenue_facts, initial_facts=(), context=None):
        context = context or {}
        facts = list(initial_facts)
        rejected = []
        occupied = {
            (_identity_key(fact.product_name), fact.start_date, fact.end_date, fact.metric)
            for fact in facts
        }
        references = {
            (_identity_key(fact.product_name), fact.start_date, fact.end_date): fact
            for fact in revenue_facts
        }
        for classification in classifications:
            item = classification.evidence
            if classification.revenue_basis != primary.classification.revenue_basis:
                continue
            if classification.semantic_axis not in {
                    primary.classification.semantic_axis, "unknown",
                    "product_service", "business"}:
                continue
            extracted = []
            if "cost" in item.title_signals:
                extracted.extend(EXPLICIT_METRIC_EXTRACTOR.extract(item, "MBCOST", revenue_facts))
            if "gross_profit" in item.title_signals:
                extracted.extend(EXPLICIT_METRIC_EXTRACTOR.extract(
                    item, "GROSS_PROFIT", revenue_facts
                ))
            identity_count = len({
                _identity_key(fact.product_name) for fact in extracted
                if fact.product_name != "合计"
            })
            if classification.semantic_axis == "unknown" and identity_count < 2:
                if extracted:
                    rejected.append({
                        "table_id": item.table.table_id,
                        "reason": "unknown_axis_without_identity_matrix",
                    })
                continue
            for fact in extracted:
                key3 = (_identity_key(fact.product_name), fact.start_date, fact.end_date)
                reference = references.get(key3)
                if reference is None:
                    continue
                aligned, reason = align_fact_measurement(
                    fact,
                    reference,
                    context.get("stable_currency", ""),
                    context.get("stable_unit", ""),
                )
                if aligned is None:
                    rejected.append({"table_id": item.table.table_id, "reason": reason})
                    continue
                key4 = (*key3, fact.metric)
                if key4 in occupied:
                    continue
                occupied.add(key4)
                facts.append(aligned)
        return facts, rejected
