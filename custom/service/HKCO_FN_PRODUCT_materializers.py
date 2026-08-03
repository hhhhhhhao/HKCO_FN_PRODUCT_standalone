# -*- coding: utf-8 -*-
"""Independent fact materializers for common HKCO disclosure structures."""
import datetime
import re
from typing import Dict, List

from custom.service.HKCO_FN_PRODUCT_fact_model import DisclosureHypothesis, FieldFact, TableEvidence


_NUMBER = re.compile(r"^\s*\(?-?[\d,]+(?:\.\d+)?\)?\s*$")
_YEAR = re.compile(r"20\d{2}|二零[〇零一二三四五六七八九]{2}")
_TOTAL = re.compile(
    r"^(?:(?:營業|营业)?(?:收入|收益)\s*)?(?:合計|合计|總計|总计|總額|总额|total)"
    r"(?:\s*[（(]?\d+[）)]?)?$", re.I,
)
_NOISE = re.compile(r"成本|毛利|利潤|利润|費用|费用|稅|税|資產|资产|負債|负债|百分比|%", re.I)
_HEADER_NOISE = re.compile(
    r"(?:人民幣|人民币|港幣|港币|美元|歐元|欧元)?\s*"
    r"(?:百萬元|百万元|萬元|万元|千元|元)?\s*"
    r"(?:\(?(?:未經審核|未经审核|經審核|经审核)\)?)?\s*$",
    re.I,
)


def _number(value):
    text = str(value or "").strip().replace("，", ",")
    if text in ("", "-", "—", "–", "N/A") or not _NUMBER.match(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "")
    try:
        value = float(text)
        return -value if negative else value
    except ValueError:
        return None


_CN_DIGIT = {"〇": "0", "零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
             "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}


def _year(text):
    match = _YEAR.search(str(text or ""))
    if not match:
        return None
    token = match.group(0)
    return int(token) if token.isdigit() else int("".join(_CN_DIGIT[c] for c in token[2:])) + 2000


def _period(year, text):
    duration = 12
    if re.search(r"三個月|三个月|3\s*months?|季度", text, re.I):
        duration = 3
    elif re.search(r"六個月|六个月|6\s*months?|中期|半年", text, re.I):
        duration = 6
    elif re.search(r"九個月|九个月|9\s*months?", text, re.I):
        duration = 9
    end_month = 12
    match = re.search(r"(?:截至)?\s*(\d{1,2})月(\d{1,2})日", text)
    if match:
        end_month, end_day = map(int, match.groups())
    else:
        end_day = 31
    try:
        end = datetime.date(year, end_month, end_day)
    except ValueError:
        end = datetime.date(year, end_month, 28)
    month_index = year * 12 + end.month - 1 - (duration - 1)
    start_year, start_month = divmod(month_index, 12)
    return datetime.date(start_year, start_month + 1, 1).isoformat(), end.isoformat()


def _context(evidence):
    return " ".join([evidence.table.title] + [str(c or "") for r in evidence.table.rows[:5] for c in r])


def _currency_unit(evidence):
    currency = evidence.currency_tokens[0] if evidence.currency_tokens else ""
    unit = evidence.unit_tokens[0] if evidence.unit_tokens else ""
    return currency, unit


class RowIdentityMaterializer:
    def supports(self, hypothesis, evidence_by_id):
        return hypothesis.kind in {"row_identity_period_column", "row_identity_section",
                                   "row_identity_section_parent", "row_identity_section_leaf",
                                   "hierarchy_parent", "hierarchy_leaf", "unresolved_revenue_disclosure"}

    def materialize(self, hypothesis, evidence_by_id):
        evidence = evidence_by_id[hypothesis.table_ids[0]]
        rows = [list(row) for row in evidence.table.rows]
        section = hypothesis.evidence.get("section")
        row_offset = 0
        if section:
            row_offset = int(section.get("start_row") or 0)
            rows = rows[row_offset:int(section.get("end_row") or len(rows))]
            if hypothesis.kind == "row_identity_section_parent":
                # A section heading normally follows the table-wide total.  The
                # total remains an explicitly disclosed fact of this axis even
                # though it lies just outside the section's row interval.
                rows = rows + [row for row in evidence.table.rows
                               if row and _TOTAL.match(str(row[0] or "").strip())]
        width = max((len(row) for row in rows), default=0)
        context = _context(evidence)
        column_years = {}
        for col in range(1, width):
            header = " ".join(str(row[col] or "") for row in rows[:5] if col < len(row))
            found = _year(header)
            if found:
                column_years[col] = found
        if not column_years:
            found = _year(context)
            if found:
                numeric_cols = [c for c in range(1, width) if any(c < len(r) and _number(r[c]) is not None for r in rows[1:])]
                if numeric_cols:
                    column_years[numeric_cols[0]] = found
        currency, unit = _currency_unit(evidence)
        facts = []
        parent = ""
        for local_row_index, row in enumerate(rows):
            row_index = row_offset + local_row_index
            if not row:
                continue
            raw_name = str(row[0] or "").strip()
            name = re.sub(r"^[\-–—]+", "", raw_name).strip()
            if not name or _NOISE.search(name) or re.search(r"截至|年度|期間|期间", name):
                continue
            if _TOTAL.match(name):
                name = "合计"
            is_child = bool(re.search(r"其中|^[\-–—]", raw_name))
            is_section_parent = bool(re.search(r"(?:分部|segment)$", name, re.I))
            is_unallocated = bool(re.search(r"未分配|unallocated", name, re.I))
            if (hypothesis.kind == "row_identity_section_parent"
                    and not (is_section_parent or is_unallocated or name == "合计")):
                continue
            if hypothesis.kind == "row_identity_section_leaf" and is_section_parent:
                parent = name
                continue
            if hypothesis.kind == "hierarchy_parent" and is_child:
                continue
            if hypothesis.kind == "hierarchy_leaf":
                if is_child:
                    name = re.sub(r"^其中[:：]?", "", name).strip()
                    if parent and parent not in name:
                        name = f"{parent}:{name}"
                elif name != "合计":
                    parent = name
                    continue
            elif not is_child and name != "合计":
                parent = name
            for col, year in column_years.items():
                if col >= len(row):
                    continue
                amount = _number(row[col])
                if amount is None:
                    continue
                start, end = _period(year, context)
                facts.append(FieldFact(
                    evidence.table.table_id, "MBREVENUE", amount, name, start, end,
                    currency, unit, hypothesis.semantic_axis, hypothesis.granularity,
                    hypothesis.revenue_basis, row_index, col,
                    evidence={"raw_label": raw_name},
                ))
        return facts


class ColumnIdentityMetricRowMaterializer:
    def supports(self, hypothesis, evidence_by_id):
        return hypothesis.kind == "column_identity_metric_row"

    def materialize(self, hypothesis, evidence_by_id):
        evidence = evidence_by_id[hypothesis.table_ids[0]]
        rows = [list(row) for row in evidence.table.rows]
        context = _context(evidence)
        year = _year(context)
        if not year:
            return []
        currency, unit = _currency_unit(evidence)
        metric_pattern = re.compile(r"外部.*(?:收入|收益|銷售|销售)|對外|对外|收入總額|收入总额|revenue", re.I)
        for metric_index, row in enumerate(rows):
            if not row or not metric_pattern.search(str(row[0] or "")):
                continue
            values = row[1:]
            for header_index in range(metric_index - 1, max(-1, metric_index - 8), -1):
                headers = [str(cell or "").strip() for cell in rows[header_index]]
                if len(headers) != len(values):
                    continue
                if sum(bool(name) and _number(name) is None for name in headers) < 2:
                    continue
                facts = []
                for col, (name, raw_value) in enumerate(zip(headers, values), start=1):
                    amount = _number(raw_value)
                    if amount is None or re.search(r"抵銷|抵销|elimination", name, re.I):
                        continue
                    name = _HEADER_NOISE.sub("", name.replace("\n", " ")).strip()
                    if not name or _YEAR.fullmatch(name):
                        continue
                    if _TOTAL.search(name):
                        name = "合计"
                    start, end = _period(year, context)
                    facts.append(FieldFact(
                        evidence.table.table_id, "MBREVENUE", amount, name, start, end,
                        currency, unit, hypothesis.semantic_axis, hypothesis.granularity,
                        hypothesis.revenue_basis, metric_index, col,
                        evidence={"metric_label": str(row[0] or "")},
                    ))
                if len([fact for fact in facts if fact.product_name != "合计"]) >= 2:
                    return facts
        return []


class RowIdentityMatrixTotalMaterializer:
    """Products in rows, disclosure dimensions in columns, explicit total column."""

    def supports(self, hypothesis, evidence_by_id):
        return hypothesis.kind == "row_identity_matrix_total"

    def materialize(self, hypothesis, evidence_by_id):
        evidence = evidence_by_id[hypothesis.table_ids[0]]
        rows = [list(row) for row in evidence.table.rows]
        context = _context(evidence)
        year = _year(" ".join(str(cell or "") for row in rows[:3] for cell in row)) or _year(context)
        if not year:
            return []
        width = max((len(row) for row in rows), default=0)
        total_col = None
        for header_row in rows[:3]:
            for cell_index, raw_cell in enumerate(header_row):
                cell = _HEADER_NOISE.sub("", str(raw_cell or "").replace("\n", " ")).strip()
                if not _TOTAL.match(cell):
                    continue
                # MinerU sometimes omits the blank top-left header cell while
                # retaining it on data rows.  Align by row width, not position.
                total_col = cell_index + (1 if len(header_row) == width - 1 else 0)
                break
            if total_col is not None:
                break
        if total_col is None:
            return []
        currency, unit = _currency_unit(evidence)
        start, end = _period(year, context)
        facts = []
        pending_total = None
        for row_index, row in enumerate(rows):
            if total_col >= len(row):
                continue
            amount = _number(row[total_col])
            if amount is None:
                continue
            raw_name = str(row[0] or "").strip() if row else ""
            if not raw_name:
                pending_total = (row_index, amount)
                continue
            if _NOISE.search(raw_name) or _YEAR.search(raw_name):
                continue
            name = "合计" if _TOTAL.match(raw_name) else raw_name
            facts.append(FieldFact(
                evidence.table.table_id, "MBREVENUE", amount, name, start, end,
                currency, unit, hypothesis.semantic_axis, hypothesis.granularity,
                hypothesis.revenue_basis, row_index, total_col,
                evidence={"raw_label": raw_name, "explicit_total_column": total_col},
            ))
        if pending_total and len(facts) >= 2 and not any(f.product_name == "合计" for f in facts):
            row_index, amount = pending_total
            facts.append(FieldFact(
                evidence.table.table_id, "MBREVENUE", amount, "合计", start, end,
                currency, unit, hypothesis.semantic_axis, hypothesis.granularity,
                hypothesis.revenue_basis, row_index, total_col,
                evidence={"explicit_total_column": total_col, "blank_total_row": True},
            ))
        return facts


class ExplicitCostProfitMaterializer:
    """Materialize only explicitly disclosed cost/GP facts after revenue selection."""
    metric_patterns = {
        "MBCOST": re.compile(r"銷售成本|销售成本|營業成本|营业成本|成本總額|成本总额|cost\s+of\s+sales", re.I),
        "GROSS_PROFIT": re.compile(r"毛利|毛損|毛损|gross\s+(?:profit|loss)", re.I),
    }

    @staticmethod
    def _key(value):
        value = re.sub(r"(?:人民幣|人民币|港幣|港币|美元|歐元|欧元)?"
                       r"(?:百萬元|百万元|萬元|万元|千元|元)$", "", str(value or "").strip(), flags=re.I)
        value = re.sub(r"[（(]\s*\d+\s*[）)]$", "", value)
        if re.fullmatch(r"(?:合計|合计|總計|总计|總額|总额|total)", value, re.I):
            value = "合计"
        return re.sub(r"[\s:：()（）\-–—_/]+", "", value).lower()

    def _resolve_product(self, value, products):
        key = self._key(value)
        if key in products:
            return products[key]
        matches = [name for old, name in products.items()
                   if min(len(key), len(old)) >= 4 and (key in old or old in key)]
        return matches[0] if len(set(matches)) == 1 else None

    def materialize(self, revenue, evidence_items):
        products = {self._key(f.product_name): f.product_name for f in revenue.facts if f.product_name}
        periods = {(f.start_date, f.end_date) for f in revenue.facts if f.end_date}
        facts = []
        for evidence in evidence_items:
            rows = [list(row) for row in evidence.table.rows]
            context = _context(evidence)
            year = _year(context)
            if not year:
                continue
            start, end = _period(year, context)
            if periods and (start, end) not in periods:
                continue
            currency, unit = _currency_unit(evidence)
            # Products in columns, metric in rows.
            for row_index, row in enumerate(rows):
                if not row:
                    continue
                metric = next((name for name, pattern in self.metric_patterns.items()
                               if pattern.search(str(row[0] or ""))), None)
                if not metric:
                    continue
                for header_index in range(row_index - 1, max(-1, row_index - 8), -1):
                    values = row[1:]
                    raw_headers = [str(cell or "").strip() for cell in rows[header_index]]
                    header_variants = [raw_headers]
                    if len(raw_headers) > 1:
                        header_variants.append(raw_headers[1:])
                    matched = []
                    for headers in header_variants:
                        if len(headers) != len(values):
                            continue
                        candidate = []
                        for col, (header, raw_value) in enumerate(zip(headers, values), start=1):
                            key = self._key(_HEADER_NOISE.sub("", header.replace("\n", " ")).strip())
                            product = self._resolve_product(key, products)
                            amount = _number(raw_value)
                            if product and amount is not None:
                                candidate.append(FieldFact(
                                    evidence.table.table_id, metric, amount,
                                    product, start, end, currency, unit,
                                    revenue.semantic_axis, revenue.granularity,
                                    revenue.revenue_basis, row_index, col,
                                    evidence={"metric_label": str(row[0] or "")},
                                ))
                        if len(candidate) >= 2:
                            for col, (header, raw_value) in enumerate(zip(headers, values), start=1):
                                amount = _number(raw_value)
                                if amount is None or not re.search(r"抵銷|抵销|對銷|对销|elimination", header, re.I):
                                    continue
                                label = re.sub(r"\s*[（(]?\d+[）)]?\s*$", "", header).strip()
                                candidate.append(FieldFact(
                                    evidence.table.table_id, metric, amount, label,
                                    start, end, currency, unit, revenue.semantic_axis,
                                    revenue.granularity, revenue.revenue_basis,
                                    row_index, col, evidence={"metric_label": str(row[0] or "")},
                                ))
                        if len(candidate) > len(matched):
                            matched = candidate
                    if matched:
                        facts.extend(matched)
                        break
            # Products in rows, metric declared by table title/section.
            table_metric = next((name for name, pattern in self.metric_patterns.items()
                                 if pattern.search(evidence.table.title)), None)
            if table_metric:
                for row_index, row in enumerate(rows):
                    if len(row) < 2:
                        continue
                    product = self._resolve_product(row[0], products)
                    amount = next((_number(cell) for cell in row[1:] if _number(cell) is not None), None)
                    if product and amount is not None:
                        facts.append(FieldFact(
                            evidence.table.table_id, table_metric,
                            amount,
                            product, start, end, currency, unit,
                            revenue.semantic_axis, revenue.granularity,
                            revenue.revenue_basis, row_index, 1,
                            evidence={"table_title": evidence.table.title},
                        ))
        unique = {}
        for fact in facts:
            unique[(fact.product_name, fact.start_date, fact.end_date, fact.metric)] = fact
        return list(unique.values())
