# -*- coding: utf-8 -*-
"""Lightweight table evidence and disclosure-hypothesis construction.

This module never parses amounts.  It only decides which table structures are
worth asking a fact materializer to inspect.
"""
import re
from typing import Iterable, List, Sequence

from custom.service.HKCO_FN_PRODUCT_fact_model import (
    DisclosureHypothesis,
    TableEvidence,
    TableRef,
)


_METRIC_PATTERNS = {
    "MBREVENUE": re.compile(
        r"收入|收益|營業額|营业额|銷售額|销售额|"
        r"(?:向|來自|来自)?外部.*(?:銷售|销售)|對外銷售|对外销售|"
        r"revenue|turnover|sales", re.I
    ),
    "MBCOST": re.compile(r"銷售成本|销售成本|營業成本|营业成本|成本總額|成本总额|cost\s+of\s+sales", re.I),
    "GROSS_PROFIT": re.compile(r"毛利|毛損|毛损|gross\s+(?:profit|loss)", re.I),
}

_AXIS_PATTERNS = {
    "property_project": re.compile(r"地塊|地块|物業項目|物业项目|商場|商场|廣場|广场|天地|中心項目|中心项目", re.I),
    "customer_industry": re.compile(r"下游行業|下游行业|客戶行業|客户行业|行業分類|行业分类|機械|机械|石化|鋼廠|钢厂|汽車/運輸|汽车/运输", re.I),
    "geography": re.compile(
        r"地區|地区|地理|國家|国家|省份|省市|region|geograph|country|"
        r"上海|北京|天津|重慶|重庆|廣東|广东|浙江|江蘇|江苏|山東|山东|福建|"
        r"四川|湖北|湖南|河南|河北|安徽|江西|遼寧|辽宁|陝西|陕西|雲南|云南|"
        r"貴州|贵州|廣西|广西|海南|山西|吉林|黑龍江|黑龙江|內蒙古|内蒙古|新疆|西藏|寧夏|宁夏|甘肅|甘肃|青海",
        re.I,
    ),
    "brand": re.compile(r"品牌|brand", re.I),
    "channel": re.compile(r"渠道|通路|線上|线上|線下|线下|channel", re.I),
    "customer_contract": re.compile(r"客戶合約|客户合约|customer\s+contract", re.I),
    "product_service": re.compile(r"產品|产品|商品|服務|服务|product|service", re.I),
    "business": re.compile(r"業務|业务|分部|板塊|板块|segment|business", re.I),
}

_PERIOD_RE = re.compile(
    r"20\d{2}|二零[〇零一二三四五六七八九]{2}|"
    r"截至.{0,16}(?:止|日)|(?:三|六|九|3|6|9|十二|12)個?月|季度|中期|年度",
    re.I,
)
_CURRENCY_RE = re.compile(r"人民幣|人民币|港幣|港币|美元|美金|歐元|欧元|HKD|RMB|CNY|USD|EUR", re.I)
_UNIT_RE = re.compile(r"百萬元|百万元|萬元|万元|千元|元|million|thousand", re.I)


def table_refs_from_sources(sources: Sequence[dict]) -> List[TableRef]:
    refs = []
    for index, source in enumerate(sources or []):
        if not isinstance(source, dict):
            continue
        rows = source.get("target_table")
        if not isinstance(rows, list) or not rows:
            continue
        page = source.get("page_number")
        refs.append(TableRef(
            table_id=f"p{page if page is not None else 'x'}:{index}",
            page=page,
            title=str(source.get("title") or ""),
            rows=rows,
        ))
    return refs


class RegexTableEvidenceScanner:
    """Cheap structural scan over every table; no amount materialization."""

    def scan(self, tables: Sequence[TableRef]) -> List[TableEvidence]:
        return [self._scan_one(table) for table in tables]

    def _scan_one(self, table: TableRef) -> TableEvidence:
        rows = [list(row) for row in table.rows if isinstance(row, (list, tuple))]
        width = max((len(row) for row in rows), default=0)
        cells = [str(cell or "").strip() for row in rows for cell in row]
        text = " ".join([table.title] + cells)
        first_column = " ".join(str(row[0] or "") for row in rows if row)
        header = " ".join(str(cell or "") for row in rows[:5] for cell in row)

        metrics = {name for name, pattern in _METRIC_PATTERNS.items() if pattern.search(text)}
        axes = {name for name, pattern in _AXIS_PATTERNS.items() if pattern.search(text)}
        signals = set()
        structures = set()
        title_header = " ".join([table.title, header, first_column[:500]])
        non_revenue_pattern = re.compile(
            r"投資收益|投资收益|利息收入|利息收益|其他收益|公允價值|公允价值|"
            r"資產減值|资产减值|融資收入|融资收入|investment\s+income|interest\s+income",
            re.I,
        )
        if non_revenue_pattern.search(title_header):
            structures.add("non_revenue_metric")
        if non_revenue_pattern.search(table.title):
            structures.add("non_revenue_title")
        section_markers = []
        for row_index, row in enumerate(rows):
            label = str(row[0] or "").strip() if row else ""
            if not re.search(r"按.+(?:劃分|划分|分類|分类|分析)", label):
                continue
            if re.search(r"地區|地区|地域", label):
                axis = "geography"
            elif re.search(r"行業|行业|客戶|客户", label):
                axis = "customer_industry"
            elif re.search(r"分部|業務|业务", label):
                axis = "business"
            elif re.search(r"產品|产品|服務|服务", label):
                axis = "product_service"
            else:
                axis = "unknown"
            section_markers.append((row_index, axis, label))
        semantic_sections = []
        for index, (start, axis, label) in enumerate(section_markers):
            end = section_markers[index + 1][0] if index + 1 < len(section_markers) else len(rows)
            section_labels = [str(row[0] or "").strip() for row in rows[start + 1:end] if row]
            hierarchy = sum(bool(re.search(r"(?:分部|segment)$", value, re.I)) for value in section_labels) >= 2
            semantic_sections.append({
                "start_row": start + 1, "end_row": end, "axis": axis,
                "label": label, "hierarchy": hierarchy,
            })
        if len(semantic_sections) >= 2:
            structures.add("multi_semantic_sections")

        if any(_METRIC_PATTERNS["MBREVENUE"].search(str(row[0] or "")) for row in rows if row):
            signals.add("revenue_metric_in_rows")
        if _METRIC_PATTERNS["MBREVENUE"].search(header):
            signals.add("revenue_metric_in_header")
        if sum(bool(_AXIS_PATTERNS["business"].search(cell)) for cell in cells[: max(width * 5, 1)]) >= 2:
            structures.add("business_labels_in_header")
        if sum(bool(
            _AXIS_PATTERNS["business"].search(cell)
            or _AXIS_PATTERNS["product_service"].search(cell)
        ) for cell in cells[: max(width * 5, 1)]) >= 2:
            structures.add("identity_labels_in_header")
        if sum(bool(_AXIS_PATTERNS["product_service"].search(str(row[0] or ""))) for row in rows if row) >= 2:
            structures.add("product_labels_in_rows")
        if re.search(r"其中|包括|包含|分為|分为|--|^-", first_column, re.M):
            structures.add("hierarchy_markers")
        segment_parent_count = sum(bool(re.search(r"分部$", str(row[0] or "").strip())) for row in rows if row)
        if segment_parent_count >= 2 and len(rows) >= segment_parent_count + 2:
            structures.add("hierarchy_markers")
        if re.search(r"合計|合计|總計|总计|總額|总额|total", text, re.I):
            structures.add("explicit_total")
        if re.search(r"抵銷|抵销|對銷|对销|elimination|inter-?segment", text, re.I):
            structures.add("elimination")
        if re.search(r"外部客戶|外部客户|對外|对外|external\s+customer", text, re.I):
            structures.add("external_revenue_basis")
        if re.search(r"基本|underlying|adjusted|經調整|经调整", text, re.I):
            structures.add("alternative_revenue_basis")
        if re.search(r"續|续|continued", table.title, re.I):
            structures.add("continuation")

        return TableEvidence(
            table=table,
            row_count=len(rows),
            column_count=width,
            text_signals=signals,
            candidate_metrics=metrics,
            candidate_axes=axes,
            period_tokens=tuple(dict.fromkeys(_PERIOD_RE.findall(text))),
            currency_tokens=tuple(dict.fromkeys(match.group(0) for match in _CURRENCY_RE.finditer(text))),
            unit_tokens=tuple(dict.fromkeys(match.group(0) for match in _UNIT_RE.finditer(text))),
            structure_signals=structures,
            semantic_sections=semantic_sections,
        )


class EvidenceHypothesisBuilder:
    """Create alternatives from evidence without parsing fields or ranking tables."""

    def build(self, evidence: Sequence[TableEvidence]) -> List[DisclosureHypothesis]:
        hypotheses: List[DisclosureHypothesis] = []
        for item in evidence:
            if "MBREVENUE" not in item.candidate_metrics:
                continue
            axis = self._axis(item)
            basis = self._basis(item)
            structure = self._structure(item)
            common = dict(
                table_ids=(item.table.table_id,),
                intended_metric="MBREVENUE",
                semantic_axis=axis,
                revenue_basis=basis,
                evidence={
                    "page": item.table.page,
                    "title": item.table.title,
                    "structure_signals": sorted(item.structure_signals),
                    "text_signals": sorted(item.text_signals),
                    "period_tokens": list(item.period_tokens),
                    "currency_tokens": list(item.currency_tokens),
                    "unit_tokens": list(item.unit_tokens),
                },
            )
            hypotheses.append(DisclosureHypothesis(
                hypothesis_id=f"{item.table.table_id}:revenue:{structure}",
                kind=structure,
                granularity="segment" if axis == "business" else "product",
                **common,
            ))
            if (
                "revenue_metric_in_rows" in item.text_signals
                and "identity_labels_in_header" in item.structure_signals
                and "product_labels_in_rows" in item.structure_signals
                and "explicit_total" in item.structure_signals
            ):
                hypotheses.append(DisclosureHypothesis(
                    hypothesis_id=f"{item.table.table_id}:revenue:row-matrix-total",
                    kind="row_identity_matrix_total",
                    granularity="product",
                    **common,
                ))
            for section_index, section in enumerate(item.semantic_sections):
                levels = ("parent", "leaf") if section.get("hierarchy") else (None,)
                for level in levels:
                    kind = f"row_identity_section_{level}" if level else "row_identity_section"
                    hypotheses.append(DisclosureHypothesis(
                        hypothesis_id=f"{item.table.table_id}:revenue:section:{section_index}"
                                      + (f":{level}" if level else ""),
                        kind=kind,
                        granularity="segment" if level == "parent" or section["axis"] == "business" else "product",
                        semantic_axis=section["axis"],
                        table_ids=(item.table.table_id,), intended_metric="MBREVENUE",
                        revenue_basis=basis,
                        evidence={**common["evidence"], "section": dict(section), "hierarchy_level": level},
                    ))
            if (
                "hierarchy_markers" in item.structure_signals
                and structure == "row_identity_period_column"
            ):
                for level in ("parent", "leaf"):
                    hypotheses.append(DisclosureHypothesis(
                        hypothesis_id=f"{item.table.table_id}:revenue:hierarchy:{level}",
                        kind=f"hierarchy_{level}",
                        granularity="segment" if level == "parent" and axis == "business" else "product",
                        **common,
                    ))
        return hypotheses

    @staticmethod
    def _axis(item: TableEvidence) -> str:
        # Explicit geographic identity evidence outranks generic words such as
        # "business" or "service" that commonly occur in surrounding prose.
        priority = ("property_project", "customer_industry", "product_service", "geography",
                    "business", "customer_contract", "brand", "channel")
        return next((axis for axis in priority if axis in item.candidate_axes), "unknown")

    @staticmethod
    def _basis(item: TableEvidence) -> str:
        if "external_revenue_basis" in item.structure_signals:
            return "external"
        if "alternative_revenue_basis" in item.structure_signals:
            return "alternative"
        return "reported"

    @staticmethod
    def _structure(item: TableEvidence) -> str:
        if "identity_labels_in_header" in item.structure_signals and "revenue_metric_in_rows" in item.text_signals:
            return "column_identity_metric_row"
        if "revenue_metric_in_header" in item.text_signals:
            return "row_identity_period_column"
        return "unresolved_revenue_disclosure"
