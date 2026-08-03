from custom.service.HKCO_FN_PRODUCT_fact_model import (
    CompatibilityResult,
    DisclosureHypothesis,
    FieldFact,
    TableEvidence,
    TableRef,
)
from custom.service.HKCO_FN_PRODUCT_pipeline import DisclosurePipeline


class Scanner:
    def scan(self, tables):
        return [TableEvidence(table, len(table.rows), 2) for table in tables]


class Builder:
    def build(self, evidence):
        return [DisclosureHypothesis("revenue:1", "single_table", (evidence[0].table.table_id,))]


class Materializer:
    def supports(self, hypothesis, evidence_by_id):
        return True

    def materialize(self, hypothesis, evidence_by_id):
        return [FieldFact(hypothesis.table_ids[0], "MBREVENUE", 10, "产品A")]


class Validator:
    def validate(self, hypothesis):
        return [CompatibilityResult("period", True)]


class Selector:
    def select(self, qualified):
        return qualified[0] if qualified else None


class MetricMerger:
    def merge(self, revenue, evidence, materializers):
        return []


class Projector:
    def project(self, hypothesis):
        return hypothesis


class Formatter:
    def format(self, hypothesis, metric_facts):
        return [{"PRODUCTNAME": fact.product_name, "MBREVENUE": fact.amount}
                for fact in hypothesis.facts]


def test_pipeline_materializes_only_after_hypothesis_building():
    pipeline = DisclosurePipeline(
        Scanner(), Builder(), [Materializer()], Validator(), Selector(),
        MetricMerger(), Projector(), Formatter(),
    )
    output, decision = pipeline.run([TableRef("p1:0", 1, "收入", [["产品A", "10"]])])
    assert output == [{"PRODUCTNAME": "产品A", "MBREVENUE": 10}]
    assert decision.selected.hypothesis_id == "revenue:1"


def test_pipeline_stops_before_metric_merge_without_qualified_hypothesis():
    class RejectingValidator:
        def validate(self, hypothesis):
            return [CompatibilityResult("currency", False, "conflicting currencies")]

    pipeline = DisclosurePipeline(
        Scanner(), Builder(), [Materializer()], RejectingValidator(), Selector(),
        MetricMerger(), Projector(), Formatter(),
    )
    output, decision = pipeline.run([TableRef("p1:0", 1, "收入", [["产品A", "10"]])])
    assert output == []
    assert decision.selected is None
    assert decision.debug["stage"] == "no_qualified_hypothesis"
