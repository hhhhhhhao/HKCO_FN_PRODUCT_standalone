# -*- coding: utf-8 -*-
"""Default validation, selection, projection and formatting components."""
from collections import defaultdict
from copy import deepcopy
import re

from custom.service.HKCO_FN_PRODUCT_fact_model import CompatibilityResult


class DisclosureValidator:
    def validate(self, hypothesis):
        facts = hypothesis.facts
        products = {f.product_name for f in facts if f.product_name != "合计"}
        periods = {(f.start_date, f.end_date) for f in facts if f.end_date}
        currencies = {f.currency for f in facts if f.currency}
        units = {f.unit for f in facts if f.unit}
        geography_labels = sum(bool(re.search(
            r"中國|中国|香港|海外|境外|亞洲|亚洲|歐洲|欧洲|美洲|地區|地区|"
            r"上海|北京|天津|重慶|重庆|廣東|广东|浙江|江蘇|江苏|山東|山东|福建|"
            r"四川|湖北|湖南|河南|河北|安徽|江西|遼寧|辽宁|陝西|陕西|雲南|云南|"
            r"貴州|贵州|廣西|广西|海南|山西|吉林|黑龍江|黑龙江|內蒙古|内蒙古|新疆|西藏|寧夏|宁夏|甘肅|甘肃|青海",
            name,
        )) for name in products)
        accounting_labels = sum(bool(re.fullmatch(
            r"(?:財務|财务|利息|其他)?(?:收入|收益)|"
            r"(?:銷售|销售|營業|营业)?成本|毛利|"
            r"(?:分部)?(?:利潤|利润|溢利|虧損|亏损)|"
            r"所得稅|所得税|折舊及攤銷|折旧及摊销",
            name,
            re.I,
        )) for name in products)
        period_identity_labels = sum(bool(re.search(
            r"20\d{2}|二零[〇零一二三四五六七八九]{2}|千元|萬元|万元|百萬元|百万元|未經審核|未经审核",
            name,
        )) for name in products)
        numbered_identity_labels = sum(bool(re.fullmatch(r"\d+\.?", name.strip())) for name in products)
        title = str(hypothesis.evidence.get("title") or "")
        signals = set(hypothesis.evidence.get("structure_signals") or [])
        explicit_composition = bool(re.search(
            r"(?:收入|收益|營業額|营业额).*(?:構成|构成|分類|分类|明細|明细|按.*(?:產品|产品|業務|业务|分部))|"
            r"(?:產品|产品|業務|业务|分部).*(?:收入|收益).*(?:構成|构成|分類|分类|明細|明细)",
            title,
            re.I,
        ))

        by_period = defaultdict(list)
        totals = {}
        for fact in facts:
            key = (fact.start_date, fact.end_date)
            if fact.product_name == "合计":
                totals[key] = fact.amount
            elif fact.amount is not None:
                by_period[key].append(fact.amount)
        closed = any(
            key in totals and len(values) >= 2
            and abs(sum(values) - totals[key]) <= max(1.0, abs(totals[key]) * 1e-8)
            for key, values in by_period.items()
        )
        structure_ok = (
            hypothesis.kind != "unresolved_revenue_disclosure"
            and (
                hypothesis.kind == "column_identity_metric_row"
                and ("external_revenue_basis" in signals or closed)
                or hypothesis.kind == "row_identity_matrix_total" and closed
                or hypothesis.kind in {"row_identity_section", "row_identity_section_parent", "row_identity_section_leaf"}
                and hypothesis.semantic_axis in {"product_service", "business", "brand", "channel"}
                or hypothesis.kind in {"row_identity_period_column", "row_identity_section", "row_identity_section_parent",
                                       "row_identity_section_leaf", "hierarchy_parent", "hierarchy_leaf"}
                and (explicit_composition or closed)
            )
        )
        if "multi_semantic_sections" in signals and not hypothesis.kind.startswith("row_identity_section"):
            structure_ok = False
        explicit_revenue_title = bool(re.search(
            r"(?:收入|收益).*(?:分部資料|分部资料|明細|明细|構成|构成|分類|分类)|"
            r"(?:商品|產品|产品|服務|服务)類型",
            title, re.I,
        ))
        if "non_revenue_title" in signals and not explicit_revenue_title:
            structure_ok = False
        elif ("non_revenue_metric" in signals
              and hypothesis.kind != "column_identity_metric_row"
              and not explicit_revenue_title):
            structure_ok = False
        results = [
            CompatibilityResult("identity", len(products) >= 2, "fewer than two product identities"),
            CompatibilityResult(
                "semantic_axis",
                hypothesis.semantic_axis != "unknown"
                and hypothesis.semantic_axis != "geography"
                and hypothesis.semantic_axis not in {"property_project", "customer_industry"}
                and geography_labels < max(1, len(products))
                and accounting_labels < max(1, len(products)),
                "unknown, geography, or accounting metric axis",
            ),
            CompatibilityResult(
                "identity_label_quality",
                period_identity_labels == 0,
                "period or unit labels were materialized as product identities",
            ),
            CompatibilityResult(
                "identity_label_shape",
                numbered_identity_labels == 0,
                "numbered rows were materialized as product identities",
            ),
            CompatibilityResult("disclosure_evidence", structure_ok, "weak or unresolved revenue disclosure"),
            CompatibilityResult("period", bool(periods) and all(a and b and a <= b for a, b in periods), "missing or invalid period"),
            CompatibilityResult("unit", len(units) <= 1, "conflicting units"),
            CompatibilityResult("currency", len(currencies) <= 1, "conflicting currencies"),
            CompatibilityResult("granularity", hypothesis.granularity in {"product", "segment"}, "unsupported granularity"),
            CompatibilityResult("revenue_basis", hypothesis.revenue_basis != "alternative", "alternative basis cannot lead reported revenue"),
        ]
        for item in results:
            if item.compatible:
                item.reason = ""
        return results


class DeterministicHypothesisSelector:
    """Deterministic disclosure evidence ordering, without learned weights or GT."""
    def select(self, qualified):
        if not qualified:
            return None

        def closure(hypothesis):
            by_period = defaultdict(list)
            totals = {}
            for fact in hypothesis.facts:
                key = (fact.start_date, fact.end_date)
                if fact.product_name == "合计":
                    totals[key] = fact.amount
                elif fact.amount is not None:
                    by_period[key].append(fact.amount)
            return any(key in totals and len(values) >= 2 and abs(sum(values) - totals[key]) <= max(1.0, abs(totals[key]) * 1e-8)
                       for key, values in by_period.items())

        basis = {"external": 3, "reported": 2, "unknown": 1}
        structure = {"column_identity_metric_row": 3, "row_identity_period_column": 3,
                     "row_identity_matrix_total": 6,
                     "row_identity_section": 5,
                     "row_identity_section_parent": 7, "row_identity_section_leaf": 6,
                     "hierarchy_leaf": 4, "hierarchy_parent": 4,
                     "unresolved_revenue_disclosure": 1}
        def prior_coverage(hypothesis):
            prior = hypothesis.evidence.get("context", {}).get("prior_product_names", [])
            normalize = lambda value: re.sub(r"[\s:：()（）\-–—_/]+", "", str(value or "")).lower()
            prior_keys = [normalize(name) for name in prior]
            current = {normalize(f.product_name) for f in hypothesis.facts if f.product_name != "合计"}
            return sum(any(key == old or (len(key) >= 3 and (key in old or old in key))
                           for old in prior_keys) for key in current)

        return max(qualified, key=lambda h: (
            basis.get(h.revenue_basis, 0),
            structure.get(h.kind, 0),
            closure(h),
            prior_coverage(h),
            len({f.product_name for f in h.facts if f.product_name != "合计"}),
            -min((h.evidence.get("page") or 10 ** 9,), default=10 ** 9),
        ))


class ExplicitMetricMerger:
    """Framework boundary for post-selection cost/GP facts; no equations."""
    def merge(self, revenue, evidence, materializers):
        from custom.service.HKCO_FN_PRODUCT_materializers import ExplicitCostProfitMaterializer
        facts = ExplicitCostProfitMaterializer().materialize(revenue, evidence)
        revenue_currency = {f.currency for f in revenue.facts if f.currency}
        revenue_unit = {f.unit for f in revenue.facts if f.unit}
        normalize = lambda value: re.sub(r"\s+", "", str(value or "")).replace("人民幣", "人民币").replace("萬", "万")
        compatible = lambda value, expected: (
            not value or not expected
            or any(normalize(value) == normalize(item)
                   or normalize(value) in normalize(item)
                   or normalize(item) in normalize(value) for item in expected)
        )
        return [fact for fact in facts
                if compatible(fact.currency, revenue_currency)
                and compatible(fact.unit, revenue_unit)]


class IdentityHierarchyProjector:
    def project(self, hypothesis):
        projected = deepcopy(hypothesis)
        seen = set()
        projected.facts = [fact for fact in projected.facts
                           if not ((fact.product_name, fact.start_date, fact.end_date, fact.metric) in seen
                                  or seen.add((fact.product_name, fact.start_date, fact.end_date, fact.metric)))]
        return projected


class RecordFormatter:
    def format(self, hypothesis, metric_facts):
        metrics = {(f.product_name, f.start_date, f.end_date, f.metric): f for f in metric_facts}
        output = []
        for fact in hypothesis.facts:
            if fact.metric != "MBREVENUE":
                continue
            cost = metrics.get((fact.product_name, fact.start_date, fact.end_date, "MBCOST"))
            profit = metrics.get((fact.product_name, fact.start_date, fact.end_date, "GROSS_PROFIT"))
            output.append({
                "product_name": fact.product_name,
                "mbrevenue": fact.amount,
                "mbcost": cost.amount if cost else "",
                "gross_profit": profit.amount if profit else "",
                "start_date": fact.start_date,
                "end_date": fact.end_date,
                "currency": fact.currency,
                "unit": fact.unit,
            })
        revenue_keys = {(fact.product_name, fact.start_date, fact.end_date)
                        for fact in hypothesis.facts if fact.metric == "MBREVENUE"}
        metric_only = {}
        for fact in metric_facts:
            key = (fact.product_name, fact.start_date, fact.end_date)
            if key not in revenue_keys:
                metric_only.setdefault(key, {})[fact.metric] = fact
        for (name, start, end), item in metric_only.items():
            cost = item.get("MBCOST")
            profit = item.get("GROSS_PROFIT")
            exemplar = cost or profit
            output.append({
                "product_name": name, "mbrevenue": "",
                "mbcost": cost.amount if cost else "",
                "gross_profit": profit.amount if profit else "",
                "start_date": start, "end_date": end,
                "currency": exemplar.currency, "unit": exemplar.unit,
            })
        return output
