# -*- coding: utf-8 -*-
"""Framework-only orchestration for HKCO product disclosures.

The pipeline deliberately separates cheap table evidence from expensive fact
materialization.  Concrete announcement parsing rules belong in registered
materializers, not in this module.
"""
from dataclasses import asdict
from typing import Callable, Dict, Iterable, List, Optional, Protocol, Sequence

from custom.service.HKCO_FN_PRODUCT_fact_model import (
    CompatibilityResult,
    DisclosureHypothesis,
    FieldFact,
    PipelineDecision,
    TableEvidence,
    TableRef,
)


class EvidenceScanner(Protocol):
    def scan(self, tables: Sequence[TableRef]) -> List[TableEvidence]: ...


class HypothesisBuilder(Protocol):
    def build(self, evidence: Sequence[TableEvidence]) -> List[DisclosureHypothesis]: ...


class FactMaterializer(Protocol):
    def supports(self, hypothesis: DisclosureHypothesis,
                 evidence_by_id: Dict[str, TableEvidence]) -> bool: ...

    def materialize(self, hypothesis: DisclosureHypothesis,
                    evidence_by_id: Dict[str, TableEvidence]) -> List[FieldFact]: ...


class HypothesisValidator(Protocol):
    def validate(self, hypothesis: DisclosureHypothesis) -> List[CompatibilityResult]: ...


class HypothesisSelector(Protocol):
    def select(self, qualified: Sequence[DisclosureHypothesis]) -> Optional[DisclosureHypothesis]: ...


class MetricMerger(Protocol):
    def merge(self, revenue: DisclosureHypothesis, evidence: Sequence[TableEvidence],
              materializers: Sequence[FactMaterializer]) -> List[FieldFact]: ...


class HierarchyProjector(Protocol):
    def project(self, hypothesis: DisclosureHypothesis) -> DisclosureHypothesis: ...


class OutputFormatter(Protocol):
    def format(self, hypothesis: DisclosureHypothesis,
               metric_facts: Sequence[FieldFact]) -> List[dict]: ...


class DisclosurePipeline:
    """Single-track pipeline; it contains orchestration, never extraction rules."""

    def __init__(self, scanner: EvidenceScanner, builder: HypothesisBuilder,
                 materializers: Sequence[FactMaterializer], validator: HypothesisValidator,
                 selector: HypothesisSelector, metric_merger: MetricMerger,
                 projector: HierarchyProjector, formatter: OutputFormatter):
        self.scanner = scanner
        self.builder = builder
        self.materializers = list(materializers)
        self.validator = validator
        self.selector = selector
        self.metric_merger = metric_merger
        self.projector = projector
        self.formatter = formatter

    def run(self, tables: Sequence[TableRef], context: Optional[dict] = None) -> tuple[List[dict], PipelineDecision]:
        evidence = self.scanner.scan(tables)
        evidence_by_id = {item.table.table_id: item for item in evidence}
        hypotheses = self.builder.build(evidence)
        for hypothesis in hypotheses:
            if context:
                hypothesis.evidence["context"] = dict(context)
            materializer = next((item for item in self.materializers
                                 if item.supports(hypothesis, evidence_by_id)), None)
            hypothesis.facts = materializer.materialize(
                hypothesis, evidence_by_id
            ) if materializer else []
            hypothesis.validations = self.validator.validate(hypothesis)
        qualified = [item for item in hypotheses if item.qualified]
        selected = self.selector.select(qualified)
        rejected = [item for item in hypotheses if item is not selected]
        if selected is None:
            return [], PipelineDecision(None, rejected, evidence, debug={"stage": "no_qualified_hypothesis"})
        metric_facts = self.metric_merger.merge(selected, evidence, self.materializers)
        projected = self.projector.project(selected)
        output = self.formatter.format(projected, metric_facts)
        return output, PipelineDecision(
            projected, rejected, evidence, metric_facts,
            debug={
                "stage": "formatted",
                "selected_hypothesis": projected.hypothesis_id,
                "rejected_hypotheses": [item.hypothesis_id for item in rejected],
            },
        )


def decision_debug(decision: PipelineDecision) -> dict:
    """Serializable decision trace for pipe_meta/debug."""
    return asdict(decision)


def build_default_pipeline() -> DisclosurePipeline:
    """Build the production framework from independent components."""
    from custom.service.HKCO_FN_PRODUCT_evidence import (
        EvidenceHypothesisBuilder,
        RegexTableEvidenceScanner,
    )
    from custom.service.HKCO_FN_PRODUCT_materializers import (
        ColumnIdentityMetricRowMaterializer,
        RowIdentityMaterializer,
        RowIdentityMatrixTotalMaterializer,
    )
    from custom.service.HKCO_FN_PRODUCT_runtime import (
        DeterministicHypothesisSelector,
        DisclosureValidator,
        ExplicitMetricMerger,
        IdentityHierarchyProjector,
        RecordFormatter,
    )
    return DisclosurePipeline(
        RegexTableEvidenceScanner(),
        EvidenceHypothesisBuilder(),
        [ColumnIdentityMetricRowMaterializer(), RowIdentityMatrixTotalMaterializer(), RowIdentityMaterializer()],
        DisclosureValidator(),
        DeterministicHypothesisSelector(),
        ExplicitMetricMerger(),
        IdentityHierarchyProjector(),
        RecordFormatter(),
    )
