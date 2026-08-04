# -*- coding: utf-8 -*-
"""Single production path for HKCO product revenue extraction.

流程（这是架构边界，不是候选优先级说明）::

    上期产品身份
        -> 轻量扫描全部表：表名、表型、身份命中、收入基准
        -> 确定主收入计划
           （通常一张；只有明确续表、同轴分表、分期兄弟表才是多张）
        -> 只物化主收入计划，形成 MBREVENUE 骨架
        -> 全公告做字段专用扫描
           （明确 MBCOST、明确 GROSS_PROFIT、兼容的补充 MBREVENUE）
        -> 按当前身份、期间、单位、币种、粒度、收入基准兼容合并
        -> 混合层级投影和输出

Architecture invariant::

    prior product identities
        -> cheap scan of every table: title, shape, identity hits, revenue basis
        -> choose the primary revenue plan
           (normally one table; multiple tables only for an explicit continuation,
            same-axis split disclosure, or period siblings)
        -> materialize only the primary revenue plan into an MBREVENUE skeleton
        -> metric-specific whole-document scans for explicitly disclosed MBCOST,
           GROSS_PROFIT, and compatible supplemental MBREVENUE
        -> merge by current identity, period, unit, currency, granularity, and basis
        -> mixed-hierarchy projection and formatting

Prior amounts never participate. Prior identities guide continuity but are not
a closed current-period product set, and current disclosure names always win.
Scanning every table does not grant every table primary-revenue status.
"""

from custom.service.HKCO_FN_PRODUCT_classifier import TableClassifier
from custom.service.HKCO_FN_PRODUCT_evidence import TableEvidenceScanner
from custom.service.HKCO_FN_PRODUCT_fact_assembly import (
    ExplicitMetricFactCollector,
    RevenuePlanMaterializer,
    SupplementalRevenueAssembler,
)
from custom.service.HKCO_FN_PRODUCT_fact_model import PipelineDecision
from custom.service.HKCO_FN_PRODUCT_projector import RecordProjector
from custom.service.HKCO_FN_PRODUCT_revenue_plan import RevenuePlanDiscoverer


class ClassifiedTablePipeline:
    def __init__(self):
        self.scanner = TableEvidenceScanner()
        self.classifier = TableClassifier()
        self.plan_discoverer = RevenuePlanDiscoverer()
        self.materializer = RevenuePlanMaterializer()
        self.revenue_assembler = SupplementalRevenueAssembler()
        self.metric_collector = ExplicitMetricFactCollector()
        self.projector = RecordProjector()

    def run(self, tables, context=None):
        context = context or {}
        evidence = self.scanner.scan(
            tables,
            context.get("document_period_text", ""),
            context.get("prior_product_names", []),
            context.get("prior_fiscal_month_day", ()),
            context.get("stable_unit", ""),
        )
        classifications = self.classifier.classify(evidence)
        plan = self.plan_discoverer.discover(classifications)
        selected, attempted, primary_parts = self.materializer.materialize_primary(plan)
        if selected is None:
            return [], PipelineDecision(
                None, classifications, attempted, evidence,
                debug={
                    "stage": "no_qualified_revenue_plan",
                    "planned_tables": [
                        table.evidence.table.table_id
                        for candidate in plan.primary_candidates for table in candidate.tables
                    ],
                },
            )
        revenue_facts, embedded_metrics, supplemental_results = self.revenue_assembler.assemble(
            selected, plan, classifications, context
        )
        metric_facts, metric_rejections = self.metric_collector.collect(
            classifications, selected, revenue_facts, embedded_metrics, context
        )
        records = self.projector.project(revenue_facts, metric_facts)
        rejected = [result for result in attempted if result is not selected]
        return records, PipelineDecision(
            selected, classifications, rejected, evidence, metric_facts,
            {
                "stage": "formatted",
                "planned_tables": [
                    table.evidence.table.table_id
                    for candidate in plan.primary_candidates for table in candidate.tables
                ],
                "materialized_tables": [
                    item.classification.evidence.table.table_id for item in primary_parts
                ],
                "supplemental_tables": [
                    item.classification.evidence.table.table_id for item in supplemental_results
                ],
                "metric_merge_rejections": metric_rejections,
            },
        )


def build_default_pipeline():
    return ClassifiedTablePipeline()
