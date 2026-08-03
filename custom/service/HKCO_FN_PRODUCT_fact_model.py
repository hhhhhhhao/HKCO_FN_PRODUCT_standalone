# -*- coding: utf-8 -*-
"""Domain objects for the HKCO product-disclosure pipeline.

These objects contain no extraction rules and have no dependency on GT or prior
period amounts.  A table first becomes lightweight evidence.  Facts are created
only after a disclosure hypothesis asks a materializer to inspect that table.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


@dataclass(frozen=True)
class TableRef:
    table_id: str
    page: Optional[int]
    title: str
    rows: Sequence[Sequence[Any]]


@dataclass
class TableEvidence:
    table: TableRef
    row_count: int
    column_count: int
    text_signals: Set[str] = field(default_factory=set)
    candidate_metrics: Set[str] = field(default_factory=set)
    candidate_axes: Set[str] = field(default_factory=set)
    period_tokens: Tuple[str, ...] = ()
    currency_tokens: Tuple[str, ...] = ()
    unit_tokens: Tuple[str, ...] = ()
    structure_signals: Set[str] = field(default_factory=set)
    semantic_sections: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class FieldFact:
    table_id: str
    metric: str
    amount: Optional[float]
    product_name: str = ""
    start_date: str = ""
    end_date: str = ""
    currency: str = ""
    unit: str = ""
    semantic_axis: str = "unknown"
    granularity: str = "unknown"
    revenue_basis: str = "unknown"
    row_index: Optional[int] = None
    column_index: Optional[int] = None
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CompatibilityResult:
    dimension: str
    compatible: bool
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DisclosureHypothesis:
    hypothesis_id: str
    kind: str
    table_ids: Tuple[str, ...]
    intended_metric: str = "MBREVENUE"
    semantic_axis: str = "unknown"
    granularity: str = "unknown"
    revenue_basis: str = "unknown"
    facts: List[FieldFact] = field(default_factory=list)
    validations: List[CompatibilityResult] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def qualified(self) -> bool:
        return bool(self.facts) and all(item.compatible for item in self.validations)


@dataclass
class PipelineDecision:
    selected: Optional[DisclosureHypothesis]
    rejected: List[DisclosureHypothesis] = field(default_factory=list)
    all_evidence: List[TableEvidence] = field(default_factory=list)
    metric_facts: List[FieldFact] = field(default_factory=list)
    debug: Dict[str, Any] = field(default_factory=dict)
