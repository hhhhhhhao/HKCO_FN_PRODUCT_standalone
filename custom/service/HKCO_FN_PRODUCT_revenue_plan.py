# -*- coding: utf-8 -*-
"""Choose one prior-identity-anchored primary revenue plan."""
import re

from custom.service.HKCO_FN_PRODUCT_fact_model import PrimaryRevenuePlanCandidate, RevenuePlan


CONTINUATION = re.compile(r"[（(]?續[）)]?|[（(]?续[）)]?|continued", re.I)
CURRENT_DETAIL = re.compile(
    r"產品|产品|商品|貨品|货品|服務|服务|業務|业务|分部|"
    r"明細|明细|分析|分拆|細分|细分|分類|分类|劃分|划分|構成|构成",
    re.I,
)
PRODUCT_DETAIL = re.compile(
    r"(?:產品|产品|商品|貨品|货品|服務|服务).{0,20}(?:收入|收益|營業額|营业额|銷售額|销售额)|"
    r"(?:收入|收益|營業額|营业额|銷售額|销售额).{0,20}(?:產品|产品|商品|貨品|货品|服務|服务)",
    re.I,
)


def _title_family(value):
    text = CONTINUATION.sub("", str(value or "").lower())
    text = re.sub(r"20\d{2}|二零[〇零一二三四五六七八九]{2}", "", text)
    return re.sub(r"[\s:：,，。;；()（）\[\]【】\d.、\-–—]+", "", text)


class RevenuePlanDiscoverer:
    """Select by prior identity count, never by prior amounts.

    Corpus rule and architecture boundary:

    * every table type may compete once current revenue evidence qualifies it;
    * the table matching the most prior product identities wins, including a
      segment or profit-and-loss layout;
    * match strength and axis concentration only break equal-hit ties;
    * current identities are not filtered by the prior set after selection;
    * extraction failure never promotes the next candidate.
    """

    def discover(self, classifications, limit=None):
        eligible = [item for item in classifications
                    if item.supported and not self.is_supplemental(item)]
        if not eligible:
            return RevenuePlan([], [item for item in classifications
                                    if item.supported and self.is_supplemental(item)])
        anchor = self._choose(eligible)
        siblings = [item for item in eligible if item is not anchor
                    and item.evidence.prior_identity_hits == anchor.evidence.prior_identity_hits
                    and self.is_explicit_sibling(anchor, item)]
        tables = sorted([anchor] + siblings, key=lambda item: (
            int(item.evidence.table.page or 10 ** 9), item.evidence.table.table_id
        ))
        relation = "explicit_sibling_family" if siblings else "single_table"
        supplemental = [item for item in classifications
                        if item.supported and self.is_supplemental(item)]
        return RevenuePlan([PrimaryRevenuePlanCandidate(tables, relation)], supplemental)

    def _choose(self, eligible):
        max_hits = max(item.evidence.prior_identity_hits for item in eligible)
        if max_hits:
            pool = [item for item in eligible
                    if item.evidence.prior_identity_hits == max_hits]
            return max(pool, key=self._continuity_tie_break)
        return max(eligible, key=self._current_structure_fallback)

    @staticmethod
    def _continuity_tie_break(item):
        evidence = item.evidence
        concentrated = (
            evidence.prior_axis in {"row", "column"}
            and min(evidence.prior_row_hits, evidence.prior_column_hits) == 0
        )
        current_revenue_strength = {
            "metric_row": 4, "metric_line": 3, "metric_column": 3,
            "embedded_heading": 2, "product_breakdown": 2, "title": 1,
        }.get(evidence.revenue_relation, 0)
        complete_shape = bool(evidence.layout_signals.intersection({
            "total_row", "explicit_total_column", "revenue_metric_row",
        }))
        explicit_identity_detail = bool(
            PRODUCT_DETAIL.search(evidence.table.title)
            or "embedded_revenue_heading" in evidence.layout_signals
            or "product_service_breakdown" in evidence.title_signals
        )
        return (
            evidence.prior_identity_strength,
            evidence.unit_continuity,
            concentrated,
            "external_revenue_metric_row" in evidence.layout_signals,
            "financial_statement" not in evidence.title_signals,
            ("closed_monetary_identity_rows" in evidence.layout_signals
             and item.table_type == "row_period"),
            explicit_identity_detail,
            current_revenue_strength,
            complete_shape,
            evidence.identity_axis != "mixed",
            -int(evidence.table.page or 10 ** 9),
        )

    @staticmethod
    def _current_structure_fallback(item):
        evidence = item.evidence
        explicit_detail = bool(
            CURRENT_DETAIL.search(evidence.table.title)
            or "embedded_revenue_heading" in evidence.layout_signals
            or "primary_revenue_analysis" in evidence.title_signals
        )
        return (
            bool(PRODUCT_DETAIL.search(evidence.table.title)
                 or "embedded_revenue_heading" in evidence.layout_signals),
            explicit_detail,
            item.semantic_axis == "product_service",
            evidence.revenue_relation in {"metric_row", "metric_column"},
            evidence.current_identity_count,
            -int(evidence.table.page or 10 ** 9),
        )

    @staticmethod
    def is_supplemental(item):
        return "supplemental_revenue" in item.evidence.title_signals

    @staticmethod
    def is_explicit_sibling(left, right):
        if (left.semantic_axis != right.semantic_axis
                or left.revenue_basis != right.revenue_basis
                or left.table_type != right.table_type):
            return False
        left_title, right_title = left.evidence.table.title, right.evidence.table.title
        if not _title_family(left_title) or _title_family(left_title) != _title_family(right_title):
            return False
        left_page, right_page = left.evidence.table.page, right.evidence.table.page
        adjacent = (left_page is not None and right_page is not None
                    and abs(left_page - right_page) <= 2)
        distinct_periods = bool(
            set(left.evidence.period_tokens) and set(right.evidence.period_tokens)
            and set(left.evidence.period_tokens).isdisjoint(right.evidence.period_tokens)
        )
        return bool(CONTINUATION.search(left_title) or CONTINUATION.search(right_title)
                    or (adjacent and distinct_periods))
