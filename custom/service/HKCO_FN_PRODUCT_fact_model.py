# -*- coding: utf-8 -*-
"""Small domain model for table classification before extraction."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple


@dataclass(frozen=True)
class TableRef:
    table_id: str
    page: Optional[int]
    title: str
    rows: Sequence[Sequence[Any]]
    context: str = ""


@dataclass
class TableEvidence:
    table: TableRef
    row_count: int
    column_count: int
    title_signals: Set[str] = field(default_factory=set)
    layout_signals: Set[str] = field(default_factory=set)
    axis_signals: Set[str] = field(default_factory=set)
    period_tokens: Tuple[str, ...] = ()
    currency_tokens: Tuple[str, ...] = ()
    unit_tokens: Tuple[str, ...] = ()
    section_markers: List[Dict[str, Any]] = field(default_factory=list)
    document_period_text: str = ""
    identity_axis: str = ""
    prior_axis: str = ""
    prior_row_hits: int = 0
    prior_column_hits: int = 0
    prior_identity_hits: int = 0
    prior_identity_strength: float = 0.0
    prior_matched_row_keys: Tuple[str, ...] = ()
    prior_matched_column_keys: Tuple[str, ...] = ()
    prior_identity_coverage: float = 0.0
    unit_continuity: int = 0
    revenue_relation: str = ""
    current_identity_count: int = 0
    prior_fiscal_month_day: Tuple[int, int] = ()


@dataclass
class TableClassification:
    evidence: TableEvidence
    table_type: str
    semantic_axis: str
    revenue_basis: str
    supported: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class FieldFact:
    table_id: str
    metric: str
    amount: Optional[float]
    product_name: str
    start_date: str
    end_date: str
    currency: str = ""
    unit: str = ""
    row_index: Optional[int] = None
    column_index: Optional[int] = None
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractionResult:
    classification: TableClassification
    facts: List[FieldFact] = field(default_factory=list)
    closed_periods: Set[Tuple[str, str]] = field(default_factory=set)
    rejection_reasons: List[str] = field(default_factory=list)

    @property
    def qualified(self):
        identities = {fact.product_name for fact in self.facts if fact.product_name != "合计"}
        return len(identities) >= 1 and bool(self.closed_periods) and not self.rejection_reasons


@dataclass
class PrimaryRevenuePlanCandidate:
    """One primary hypothesis, optionally formed by explicit sibling tables."""
    tables: List[TableClassification] = field(default_factory=list)
    relation: str = "single_table"


@dataclass
class RevenuePlan:
    primary_candidates: List[PrimaryRevenuePlanCandidate] = field(default_factory=list)
    supplemental_candidates: List[TableClassification] = field(default_factory=list)


@dataclass
class PipelineDecision:
    selected: Optional[ExtractionResult]
    classifications: List[TableClassification] = field(default_factory=list)
    rejected: List[ExtractionResult] = field(default_factory=list)
    all_evidence: List[TableEvidence] = field(default_factory=list)
    metric_facts: List[FieldFact] = field(default_factory=list)
    debug: Dict[str, Any] = field(default_factory=dict)
