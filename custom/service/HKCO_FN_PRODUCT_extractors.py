# -*- coding: utf-8 -*-
"""One extractor per classified table shape."""
import datetime
import calendar
from collections import defaultdict
import re

from custom.service.HKCO_FN_PRODUCT_evidence import (
    CURRENCY,
    EXTERNAL_REVENUE_METRIC_LABEL,
    METRIC_LABEL,
    NON_REVENUE_METRIC_IDENTITY,
    PHYSICAL_MEASUREMENT,
    REVENUE_METRIC_LABEL,
    UNIT,
)
from custom.service.HKCO_FN_PRODUCT_fact_model import ExtractionResult, FieldFact
from custom.service.HKCO_FN_PRODUCT_identity import identity_key


NUMBER = re.compile(r"^\s*\(?-?[\d,]+(?:\.\d+)?\)?\s*$")
YEAR = re.compile(r"20\d{2}|二零[〇零一二三四五六七八九]{2}|二〇[〇零一二三四五六七八九]{2}")
TOTAL = re.compile(
    r"^(?:合計|合计|合共|總計|总计|總額|总额|總收入|总收入|總收益|总收益|收入合計|收入合计|total|"
    r"(?:淨|净)?(?:收入|收益|營業額|营业额|銷售額|销售额).*(?:總額|总额|合計|合计|總計|总计))$",
    re.I,
)
TOTAL_COLUMN = re.compile(
    r"^(?:(?:分部|可呈報分部|可呈报分部|綜合|综合)?(?:合計|合计|總計|总计|總額|总额)|"
    r"本集團|本集团|綜合|综合|consolidated|the group|total)$",
    re.I,
)
TOTAL_PERIOD_HEADER = re.compile(
    r"(?:合計|合计|總計|总计|總額|总额|總收入|总收入|總收益|总收益|"
    r"本集團|本集团|綜合|综合|consolidated|the group|total)", re.I,
)
SUBTOTAL = re.compile(r"^(?:小計|小计|subtotal)$", re.I)
REVENUE = re.compile(r"收入|收益|營業額|营业额|銷售額|销售额|revenue|turnover|sales", re.I)
COST = re.compile(
    r"^(?:分部)?(?:銷售成本|销售成本|營業成本|营业成本|收入成本|已售貨品成本|已售货品成本|成本)$|"
    r"^(?:segment )?(?:cost of sales|cost of goods sold)$",
    re.I,
)
PROFIT = re.compile(r"毛利|毛損|毛损|gross profit|gross loss", re.I)
NON_REVENUE_COLUMN = re.compile(r"成本|毛利|利潤|利润|溢利|EBITDA|百分比|%|數目|数目|cost|margin|profit|expense|number", re.I)
EXTERNAL_BASIS_COLUMN = re.compile(r"對外|对外|外部|外界(?:客戶|客户)|external", re.I)
INTERSEGMENT_COLUMN = re.compile(r"分部間|分部间|內部|内部|inter-?segment", re.I)
NOISE_IDENTITY = re.compile(
    r"期間|期间|年度|截至|人民幣|人民币|港元|千元|百萬|百万|"
    r"(?:收入|收益).{0,5}(?:確認|确认).{0,3}(?:時間|时间)|"
    r"地區市場|地区市场|地理市場|地理市场|"
    r"(?:於|于|在)?(?:某一)?(?:時間點|时间点|時點|时点)(?:確認|确认)?(?:收入|收益)?|"
    r"(?:隨|随|一段)時間(?:內|内)?確認(?:收入|收益)|"
    r"(?:收入|收益).{0,5}(?:確認|确认).{0,3}(?:時間|时间)",
    re.I,
)
AGGREGATE_IDENTITY = re.compile(
    r"^(?:來自|来自)?客[戶户](?:合約|合同)(?:的|之)?(?:收入|收益)$|"
    r"^(?:產品|产品|貨品|货品)銷售(?:收入|收益)?$",
    re.I,
)
HIERARCHY_PARENT_IDENTITY = re.compile(
    r"(?:業務|业务|分部|板塊|板块)$|^(?:銷售|销售|提供)(?:產品|产品|貨品|货品|服務|服务)$|"
    r"(?:產品|产品|服務|服务|收入|收益)(?:類別|类别|分類|分类)$",
    re.I,
)
MEASUREMENT_SUFFIX = re.compile(
    r"\s*(?:(?:人民幣|人民币|港幣|港币|美元|歐元|欧元|日圓|日圆|日元|"
    r"百萬元|百万元|萬元|万元|千港元|千美元|千歐元|千欧元|千日圓|千日元|千令吉特|港元|千元|元)\s*)+(?:\([^)]*\))?$",
    re.I,
)
CN = {"〇": "0", "零": "0", "一": "1", "二": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}


def _cn_number(token):
    if token == "十": return 10
    if "十" in token:
        left, right = token.split("十", 1)
        return (int(CN[left]) if left else 1) * 10 + (int(CN[right]) if right else 0)
    return int("".join(CN[char] for char in token))


def _number(value):
    text = str(value or "").strip().replace("，", ",")
    if not NUMBER.match(text): return None
    negative = text.startswith("(") and text.endswith(")")
    value = float(text.strip("()").replace(",", ""))
    return -value if negative else value


def _year(value):
    match = YEAR.search(str(value or ""))
    if not match: return None
    token = match.group(0)
    if token.isdigit(): return int(token)
    return 2000 + int("".join(CN[char] for char in token[2:]))


def _period(year, context, prior_fiscal_month_day=()):
    duration = 6 if re.search(r"六個月|六个月|中期|半年", context) else 3 if re.search(r"三個月|三个月|季度", context) else 9 if re.search(r"九個月|九个月", context) else 12
    month, day = 12, 31
    explicit_end = False
    matches = re.findall(r"(\d{1,2})月(\d{1,2})日", context)
    if matches:
        month, day = map(int, matches[-1])
        explicit_end = True
    else:
        chinese_matches = re.findall(r"([一二三四五六七八九十]{1,3})月([一二三四五六七八九十]{1,3})日", context)
        if chinese_matches:
            month, day = map(_cn_number, chinese_matches[-1])
            explicit_end = True
    if not explicit_end and prior_fiscal_month_day:
        month, day = prior_fiscal_month_day
    try:
        end = datetime.date(year, month, day)
    except ValueError:
        end = datetime.date(year, 12, 31)
    if not explicit_end and prior_fiscal_month_day and duration < 12:
        shift = 12 - duration
        month_index = end.year * 12 + end.month - 1 - shift
        end_year, zero_month = divmod(month_index, 12)
        end_month = zero_month + 1
        fiscal_month_end = day == calendar.monthrange(year, month)[1]
        end_day = calendar.monthrange(end_year, end_month)[1] if fiscal_month_end else min(
            day, calendar.monthrange(end_year, end_month)[1]
        )
        end = datetime.date(end_year, end_month, end_day)
    index = end.year * 12 + end.month - duration
    start_year, start_zero = divmod(index, 12)
    start = datetime.date(start_year, start_zero + 1, 1)
    return start.isoformat(), end.isoformat()


def _measurement(item):
    currency = item.currency_tokens[0] if item.currency_tokens else ""
    raw_unit = item.unit_tokens[0] if item.unit_tokens else ""
    # UNIT is a scale, while OCR commonly fuses scale and currency (for
    # example 百萬美元).  Normalize at fact creation so every extractor and
    # cross-table compatibility gate uses the same three scale codes.
    unit = ("004" if re.search(r"百萬|百万|million", raw_unit, re.I) else
            "002" if re.search(r"千|thousand", raw_unit, re.I) else
            "001" if raw_unit else "")
    return currency, unit


def _period_context(item, local_context):
    """Prefer this table's fiscal end; otherwise use the document fiscal heading."""
    if re.search(r"(?:\d{1,2}|[一二三四五六七八九十]{1,3})月"
                 r"(?:\d{1,2}|[一二三四五六七八九十]{1,3})日", local_context):
        return local_context
    if item.prior_fiscal_month_day:
        return local_context
    return " ".join(filter(None, (local_context, item.table.context,
                                   item.document_period_text)))


def _clean_name(value):
    name = re.sub(r"^(?:其中[:：]?\s*|[-–—·•]{1,3}\s*)", "", str(value or "").strip().replace("\n", " "))
    return re.sub(
        r"^按(?:主要)?(?:產品|产品|商品|服務|服务|業務|业务)?(?:線|线)?(?:劃分|划分)",
        "", name,
    ).strip()


def _revenue_metric_priority(value):
    """Return the disclosed revenue basis rank; zero means not a revenue metric."""
    label = re.sub(
        r"[（(](?=[^（）()]*?(?:人民幣|人民币|港元|美元|歐元|欧元|日圓|日元|千|萬|万|百萬|百万))[^（）()]*[）)]\s*$",
        "", _clean_name(value).strip(), flags=re.I,
    ).rstrip(":：")
    if EXTERNAL_REVENUE_METRIC_LABEL.search(label):
        return 3
    if REVENUE_METRIC_LABEL.fullmatch(label):
        return 2 if re.match(r"^(?:總|总|本集團|本集团|集團|集团)", label) else 1
    return 0


def _column_name(value):
    name = MEASUREMENT_SUFFIX.sub("", str(value or "").strip().replace("\n", ""))
    return "合计" if TOTAL_COLUMN.match(name) or name in {"綜合", "综合"} else name


def _identity_header(cells):
    names = [
        _column_name(cell) for cell in cells
        if str(cell or "").strip() not in {"", "-", "–", "—"}
        and _number(cell) is None
    ]
    return len([name for name in names if name and not YEAR.search(name)]) >= 2


def _close(result):
    grouped = defaultdict(list)
    totals = {}
    total_facts = defaultdict(list)
    for fact in result.facts:
        if fact.metric != "MBREVENUE": continue
        key = (fact.start_date, fact.end_date)
        if fact.product_name == "合计":
            totals[key] = fact.amount
            total_facts[key].append(fact)
        elif fact.amount is not None: grouped[key].append(fact.amount)
    for key, values in grouped.items():
        total = totals.get(key)
        if total is not None and len(values) >= 1 and abs(sum(values) - total) <= max(1.0, abs(total) * 1e-8):
            result.closed_periods.add(key)
    if result.closed_periods:
        keep_totals = {id(total_facts[key][-1]) for key in result.closed_periods if total_facts[key]}
        result.facts = [fact for fact in result.facts
                        if fact.metric != "MBREVENUE"
                        or fact.product_name != "合计"
                        or id(fact) in keep_totals]
    else:
        result.rejection_reasons.append("revenue_facts_do_not_close")
    return result


def _project_intermediate_aggregates(result):
    """Remove or promote explicitly closed aggregate rows before final closure."""
    revenue = [fact for fact in result.facts if fact.metric == "MBREVENUE"]
    total_rows = {fact.row_index for fact in revenue if fact.product_name == "合计"}
    rows = defaultdict(list)
    for fact in revenue:
        if fact.product_name != "合计":
            rows[fact.row_index].append(fact)
    accumulated = defaultdict(float)
    replacements, removed = {}, set()
    for row_index in sorted(rows, key=lambda value: -1 if value is None else value):
        facts = rows[row_index]
        name = facts[0].product_name
        periods = {(fact.start_date, fact.end_date): fact.amount for fact in facts}
        closes_prior = bool(accumulated) and all(
            amount is not None
            and abs(amount - accumulated[period]) <= max(1.0, abs(amount) * 1e-8)
            for period, amount in periods.items()
            if period in accumulated
        ) and all(period in accumulated for period in periods)
        if AGGREGATE_IDENTITY.search(name) and closes_prior:
            later_total = any(index is not None and row_index is not None and index > row_index
                              for index in total_rows)
            if later_total:
                removed.update(id(fact) for fact in facts)
            else:
                replacements.update({id(fact): "合计" for fact in facts})
            continue
        for period, amount in periods.items():
            if amount is not None:
                accumulated[period] += amount
    result.facts = [
        fact if id(fact) not in replacements else FieldFact(
            fact.table_id, fact.metric, fact.amount, replacements[id(fact)],
            fact.start_date, fact.end_date, fact.currency, fact.unit,
            fact.row_index, fact.column_index, fact.evidence,
        )
        for fact in result.facts if id(fact) not in removed
    ]
    return result


def _synthesize_missing_totals(result):
    """Close a disclosed product section using only its current table amounts."""
    grouped, totals = defaultdict(list), set()
    for fact in result.facts:
        if fact.metric != "MBREVENUE":
            continue
        period = (fact.start_date, fact.end_date)
        if fact.product_name == "合计":
            totals.add(period)
        elif fact.amount is not None:
            grouped[period].append(fact)
    additions = []
    for period, facts in grouped.items():
        if period in totals or not facts:
            continue
        reference = facts[-1]
        additions.append(FieldFact(
            reference.table_id, "MBREVENUE", sum(fact.amount for fact in facts), "合计",
            period[0], period[1], reference.currency, reference.unit,
            reference.row_index, reference.column_index,
            {"derived_from_current_disclosed_rows": True},
        ))
    result.facts.extend(additions)
    return result


def _project_current_identities(result):
    """Comparatives describe current products; they do not create products alone."""
    revenue = [fact for fact in result.facts if fact.metric == "MBREVENUE"]
    if not revenue:
        return result
    current_end = max(fact.end_date for fact in revenue)
    current_names = {
        fact.product_name for fact in revenue
        if fact.end_date == current_end and fact.product_name != "合计"
    }
    if not current_names:
        return result
    result.facts = [
        fact for fact in result.facts
        if fact.metric != "MBREVENUE"
        or fact.product_name == "合计"
        or fact.product_name in current_names
    ]
    return result


def _project_arithmetic_hierarchy(result, matched_identity_keys=()):
    """Choose one frontier from a locally closed current hierarchy.

    Parents and children are discovered only from current-table arithmetic.
    Prior identities choose the disclosed frontier: a matched parent keeps its
    subtree aggregated; otherwise disclosed children replace the parent and
    new siblings remain eligible.  No prior amount or closed name set is used.
    """
    revenue = [fact for fact in result.facts if fact.metric == "MBREVENUE"]
    by_row = defaultdict(list)
    for fact in revenue:
        if fact.row_index is not None:
            by_row[fact.row_index].append(fact)
    ordered = [(row, sorted(facts, key=lambda fact: (fact.end_date, fact.column_index or -1)))
               for row, facts in sorted(by_row.items())]
    if len(ordered) < 4:
        return result

    def vector(facts):
        return {(fact.start_date, fact.end_date): fact.amount for fact in facts}

    def equal(left, right):
        return (left.keys() == right.keys() and left
                and all(a is not None and b is not None
                        and abs(a - b) <= max(1.0, abs(a) * 1e-8)
                        for key in left for a, b in [(left[key], right[key])]))

    def add(vectors):
        keys = set.intersection(*(set(item) for item in vectors)) if vectors else set()
        return {key: sum(item[key] or 0.0 for item in vectors) for key in keys}

    root_pos = None
    end_pos = len(ordered)
    for left in range(len(ordered) - 2):
        left_vector = vector(ordered[left][1])
        if not (REVENUE.search(ordered[left][1][0].product_name)
                or TOTAL.match(ordered[left][1][0].product_name)):
            continue
        for right in range(left + 3, len(ordered)):
            if equal(left_vector, vector(ordered[right][1])):
                root_pos, end_pos = left, right
                break
        if root_pos is not None:
            break
    if root_pos is None:
        for index, (_, facts) in enumerate(ordered[:-2]):
            name = facts[0].product_name
            if TOTAL.match(name) or REVENUE_METRIC_LABEL.fullmatch(name):
                root_pos = index
                break
    if root_pos is None or end_pos - root_pos < 3:
        return result

    root_row, root_facts = ordered[root_pos]
    nodes = [{"row": row, "facts": facts, "children": []}
             for row, facts in ordered[root_pos + 1:end_pos]]
    active = []
    for node in reversed(nodes):
        matched_children = None
        node_name = node["facts"][0].product_name
        may_be_parent = (identity_key(node_name) not in set(matched_identity_keys or ())
                         or HIERARCHY_PARENT_IDENTITY.search(node_name))
        if may_be_parent:
            for count in range(2, len(active) + 1):
                if equal(vector(node["facts"]), add([
                        vector(child["facts"]) for child in active[:count]
                ])):
                    matched_children = active[:count]
                    break
        if matched_children:
            node["children"] = matched_children
            active = [node] + active[len(matched_children):]
        else:
            active.insert(0, node)
    if len(active) < 2 or not equal(vector(root_facts), add([
            vector(node["facts"]) for node in active
    ])):
        return result

    matched = set(matched_identity_keys or ())

    def frontier(node):
        name_key = identity_key(node["facts"][0].product_name)
        if name_key in matched or not node["children"]:
            return node["facts"]
        return [fact for child in node["children"] for fact in frontier(child)]

    selected = [fact for node in active for fact in frontier(node)]
    totals = [FieldFact(
        fact.table_id, fact.metric, fact.amount, "合计", fact.start_date,
        fact.end_date, fact.currency, fact.unit, root_row, fact.column_index,
        {**fact.evidence, "arithmetic_hierarchy_total": True},
    ) for fact in root_facts]
    other = [fact for fact in result.facts if fact.metric != "MBREVENUE"]
    result.facts = other + selected + totals
    return result


def _identity_column(rows):
    positions = []
    for row in rows:
        numeric = [index for index, cell in enumerate(row) if _number(cell) is not None]
        if numeric: positions.append(min(numeric))
    if not positions: return 0
    # Later reconciliation rows are often populated only in profit/total
    # columns.  Their modal numeric position must not move the identity stub;
    # the leftmost disclosed amount identifies the table's body geometry.
    first_numeric = min(positions)
    return max(0, first_numeric - 1)


def _header(rows, col, end):
    width = max((len(row) for row in rows), default=0)
    values = []
    for row in rows[:end]:
        first = str(row[0] or "").strip() if row else ""
        merged_metric_group = bool(
            len(row) > 1 and len(row) < width and not first
            and (width - 1) % (len(row) - 1) == 0
            and all(METRIC_LABEL.search(str(cell or "")) for cell in row[1:])
        )
        header_only = bool(
            YEAR.search(first) or MEASUREMENT_SUFFIX.search(first)
            or re.search(r"截至|年度|期間|期间|財年|财年|人民幣|人民币|港元|千元|百萬|百万", first)
        )
        if merged_metric_group:
            span = (width - 1) // (len(row) - 1)
            index = 1 + (col - 1) // span
        else:
            index = col if len(row) >= width else col - 1 if header_only else col - (width - len(row))
        values.append(str(row[index] if 0 <= index < len(row) else ""))
    return " ".join(values)


class RowExtractor:
    table_types = {"row_period", "row_metric_period", "row_measurement_period"}

    def extract(self, classification):
        item = classification.evidence
        rows = [list(row) for row in item.table.rows]
        identity_col = _identity_column(rows)
        body = next((index for index, row in enumerate(rows)
                     if identity_col < len(row) and str(row[identity_col] or "").strip()
                     and any(_number(cell) is not None for cell in row[identity_col + 1:])), len(rows))
        context = _period_context(item, " ".join(
            [item.table.title] + [str(cell or "") for row in rows[:body + 1] for cell in row]
        ))
        columns = {}
        for col in range(identity_col + 1, item.column_count):
            descriptor = _header(rows, col, body)
            year = _year(descriptor)
            if year is None: continue
            if classification.table_type == "row_metric_period":
                if not REVENUE.search(descriptor) or NON_REVENUE_COLUMN.search(descriptor): continue
            elif classification.table_type == "row_measurement_period":
                # In year × (amount, volume, average price) tables, only the
                # monetary subcolumn is revenue.  Selecting all numeric columns
                # confuses measurement coordinates with separate disclosures.
                if (PHYSICAL_MEASUREMENT.search(descriptor)
                        or not (CURRENCY.search(descriptor) or UNIT.search(descriptor))):
                    continue
            elif NON_REVENUE_COLUMN.search(descriptor):
                continue
            columns[col] = (year, descriptor)
        if classification.table_type == "row_metric_period" and columns:
            external = {col: value for col, value in columns.items()
                        if EXTERNAL_BASIS_COLUMN.search(value[1])}
            if external:
                columns = external
            else:
                non_intersegment = {col: value for col, value in columns.items()
                                    if not INTERSEGMENT_COLUMN.search(value[1])}
                if non_intersegment:
                    columns = non_intersegment
        if not columns and classification.table_type == "row_period":
            fallback_year = _year(" ".join((item.table.title, item.table.context,
                                             item.document_period_text)))
            sample = rows[body] if body < len(rows) else []
            numeric_columns = [
                col for col in range(identity_col + 1, item.column_count)
                if col < len(sample) and _number(sample[col]) is not None
            ]
            if fallback_year is not None:
                for offset, col in enumerate(numeric_columns):
                    columns[col] = (fallback_year - offset, "")
        if not columns and classification.table_type == "row_metric_period":
            fallback_year = _year(" ".join((item.table.title, item.table.context,
                                             item.document_period_text)))
            if fallback_year is not None:
                candidates = {}
                for col in range(identity_col + 1, item.column_count):
                    descriptor = _header(rows, col, body)
                    if (REVENUE.search(descriptor)
                            and not NON_REVENUE_COLUMN.search(descriptor)
                            and not INTERSEGMENT_COLUMN.search(descriptor)):
                        candidates[col] = (fallback_year, descriptor)
                external = {col: value for col, value in candidates.items()
                            if EXTERNAL_BASIS_COLUMN.search(value[1])}
                columns = external or candidates
        result = ExtractionResult(classification)
        if not columns:
            result.rejection_reasons.append("no_revenue_period_columns")
            return result
        currency, unit = _measurement(item)
        for row_index, row in enumerate(rows[body:], start=body):
            if identity_col >= len(row): continue
            raw_name = _clean_name(row[identity_col])
            if SUBTOTAL.match(raw_name):
                continue
            has_value = any(col < len(row) and _number(row[col]) is not None for col in columns)
            if not has_value:
                if result.facts and (NOISE_IDENTITY.search(raw_name)
                                     or any(marker["row"] == row_index
                                            for marker in item.section_markers)):
                    break
                continue
            if not raw_name and has_value: name = "合计"
            elif TOTAL.match(raw_name): name = "合计"
            else: name = raw_name
            if not name:
                continue
            if NOISE_IDENTITY.search(name):
                if result.facts:
                    break
                continue
            if (result.facts and NON_REVENUE_METRIC_IDENTITY.search(name)
                    and not REVENUE.search(name)):
                break
            for col, (year, descriptor) in columns.items():
                if col >= len(row): continue
                amount = _number(row[col])
                if amount is None: continue
                column_context = " ".join(filter(None, (descriptor, context)))
                start, end = _period(year, column_context, item.prior_fiscal_month_day)
                result.facts.append(FieldFact(item.table.table_id, "MBREVENUE", amount, name, start, end, currency, unit, row_index, col))
            # A P&L may contain a genuine product-revenue section followed by
            # cost and profit metrics.  The explicit revenue total closes the
            # section; later statement rows are not additional products.
            if name == "合计" and "financial_statement" in item.title_signals:
                break
        result = _project_arithmetic_hierarchy(
            result, item.prior_matched_row_keys
        )
        result = _project_intermediate_aggregates(result)
        if "revenue" in item.title_signals and "financial_statement" not in item.title_signals:
            result = _synthesize_missing_totals(result)
        return _project_current_identities(_close(result))


class RowIdentityTotalPeriodExtractor:
    """Products are rows; dimensions and periods are columns; use explicit totals."""
    table_types = {"row_identity_total_period"}

    def extract(self, classification):
        item = classification.evidence
        rows = [list(row) for row in item.table.rows]
        identity_col = _identity_column(rows)
        body = next((index for index, row in enumerate(rows)
                     if identity_col < len(row) and str(row[identity_col] or "").strip()
                     and any(_number(cell) is not None for cell in row[identity_col + 1:])), len(rows))
        context = _period_context(item, " ".join(
            [item.table.title] + [str(cell or "") for row in rows[:body + 1] for cell in row]
        ))
        columns = {}
        for col in range(identity_col + 1, item.column_count):
            descriptor = _header(rows, col, body)
            year = (_year(descriptor) or _year(item.table.context)
                    or _year(item.table.title))
            if year is not None and TOTAL_PERIOD_HEADER.search(descriptor):
                columns[col] = year
        result = ExtractionResult(classification)
        if not columns:
            result.rejection_reasons.append("no_explicit_total_period_columns")
            return result
        currency, unit = _measurement(item)
        numeric_row_indexes = [
            row_index for row_index, row in enumerate(rows[body:], start=body)
            if any(col < len(row) and _number(row[col]) is not None for col in columns)
        ]
        final_numeric_row = numeric_row_indexes[-1] if numeric_row_indexes else -1
        for row_index, row in enumerate(rows[body:], start=body):
            raw_name = _clean_name(row[identity_col] if identity_col < len(row) else "")
            has_value = any(col < len(row) and _number(row[col]) is not None for col in columns)
            named_total = bool(re.search(r"(?:小計|小计|合計|合计|總計|总计|總額|总额)$", raw_name))
            if named_total and row_index != final_numeric_row:
                continue
            name = "合计" if (named_total or TOTAL.match(raw_name)
                              or REVENUE_METRIC_LABEL.fullmatch(_clean_name(raw_name))
                              or EXTERNAL_REVENUE_METRIC_LABEL.search(_clean_name(raw_name))
                              or (not raw_name and has_value)) else raw_name
            if not name or NOISE_IDENTITY.search(name):
                continue
            for col, year in columns.items():
                if col >= len(row):
                    continue
                amount = _number(row[col])
                if amount is None:
                    continue
                start, end = _period(year, context, item.prior_fiscal_month_day)
                result.facts.append(FieldFact(
                    item.table.table_id, "MBREVENUE", amount, name, start, end,
                    currency, unit, row_index, col,
                ))
        return _project_current_identities(_close(result))


class ColumnMetricExtractor:
    table_types = {"column_metric_period", "segment_matrix_period"}

    def extract(self, classification):
        item = classification.evidence
        rows = [list(row) for row in item.table.rows]
        result = ExtractionResult(classification)
        currency, unit = _measurement(item)
        current_year = None
        period_context = item.table.title
        headers = None
        revenue_groups = []
        other_facts = []
        for row_index, row in enumerate(rows):
            row_text = " ".join(str(cell or "") for cell in row)
            found_year = _year(row_text)
            if found_year and sum(_number(cell) is not None for cell in row) == 0:
                current_year = found_year
                period_context = " ".join([item.table.title, row_text])
            text_cells = [str(cell or "").strip().replace("\n", " ") for cell in row]
            if _identity_header(text_cells[1:]):
                headers = text_cells
                continue
            label = text_cells[0] or " ".join(text_cells[:2])
            revenue_priority = _revenue_metric_priority(label)
            clean_metric_label = re.sub(
                r"[（(](?=[^（）()]*?(?:人民幣|人民币|港元|美元|歐元|欧元|日圓|日元|千|萬|万|百萬|百万))[^（）()]*[）)]\s*$",
                "", label, flags=re.I,
            ).rstrip(":：")
            metric = ("MBREVENUE" if revenue_priority else "MBCOST" if COST.search(clean_metric_label)
                      else "GROSS_PROFIT" if PROFIT.search(clean_metric_label) else None)
            if metric is None or headers is None: continue
            year = (current_year or _year(item.table.context) or _year(item.table.title)
                    or _year(item.document_period_text))
            if year is None: continue
            offset = max(0, len(row) - len(headers))
            row_facts = []
            for col in range(1, len(row)):
                amount = _number(row[col])
                header_col = col - offset
                name = _column_name(headers[header_col]) if 0 <= header_col < len(headers) else ""
                if not name:
                    continue
                explicit_elimination = bool(re.search(
                    r"抵銷|抵销|對銷|对销|elimination", name, re.I
                ))
                # A dash in an explicitly disclosed elimination column is a
                # current fact, not a missing identity.  Keep the identity with
                # a null amount; it contributes zero to arithmetic closure.
                if amount is None and not (
                        metric == "MBREVENUE" and explicit_elimination
                        and identity_key(name) in item.prior_matched_column_keys
                        and str(row[col] or "").strip() in {"-", "–", "—"}):
                    continue
                column_year = _year(_header(rows, col, row_index)) or year
                start, end = _period(
                    column_year, " ".join([period_context, row_text]), item.prior_fiscal_month_day
                )
                if metric == "MBCOST" and not re.search(r"抵銷|抵销|對銷|对销|elimination", name, re.I):
                    amount = abs(amount)
                if explicit_elimination: pass
                row_facts.append(FieldFact(item.table.table_id, metric, amount, name, start, end, currency, unit, row_index, col))
            if metric == "MBREVENUE":
                by_period = defaultdict(list)
                for fact in row_facts:
                    by_period[(fact.start_date, fact.end_date)].append(fact)
                for period, period_facts in by_period.items():
                    totals = [fact.amount for fact in period_facts if fact.product_name == "合计"]
                    parts = [fact.amount for fact in period_facts
                             if fact.product_name != "合计" and fact.amount is not None]
                    if totals and len(parts) >= 1 and abs(sum(parts) - totals[-1]) <= max(1.0, abs(totals[-1]) * 1e-8):
                        revenue_groups.append((period, revenue_priority, row_index, period_facts))
            else:
                other_facts.extend(row_facts)
        last_revenue = {}
        for period, priority, row_index, facts in revenue_groups:
            previous = last_revenue.get(period)
            if previous is None or (priority, row_index) > previous[:2]:
                last_revenue[period] = (priority, row_index, facts)
        result.facts = other_facts + [fact for _, _, facts in last_revenue.values() for fact in facts]
        return _project_current_identities(_close(result))


class MixedHierarchyExtractor:
    """Project atomic rows from a locally closed mixed parent/leaf revenue table."""
    table_types = {"mixed_hierarchy"}

    def extract(self, classification):
        item = classification.evidence
        rows = [list(row) for row in item.table.rows]
        identity_col = _identity_column(rows)
        body = next((index for index, row in enumerate(rows)
                     if identity_col < len(row)
                     and any(_number(cell) is not None for cell in row[identity_col + 1:])), len(rows))
        context = _period_context(item, " ".join(
            [item.table.title] + [str(cell or "") for row in rows[:body + 1] for cell in row]
        ))
        columns = {}
        for col in range(identity_col + 1, item.column_count):
            year = _year(_header(rows, col, body))
            if year is not None and not NON_REVENUE_COLUMN.search(_header(rows, col, body)):
                columns[col] = year
        result = ExtractionResult(classification)
        if not columns:
            result.rejection_reasons.append("no_revenue_period_columns")
            return result
        currency, unit = _measurement(item)
        numeric_rows = []
        expanded_parents = set()
        labels = [_clean_name(row[identity_col]) if identity_col < len(row) else "" for row in rows[body - 1:]]
        for label in labels:
            if not label:
                continue
            if any(other != label and re.fullmatch(
                    re.escape(label) + r"(?:總額|总额|合計|合计|淨額|净额)", other
            ) for other in labels):
                expanded_parents.add(label)
        pending_parent = ""
        if body > 0 and identity_col < len(rows[body - 1]):
            preceding = _clean_name(rows[body - 1][identity_col])
            if preceding and not NOISE_IDENTITY.search(preceding) and not YEAR.search(preceding):
                pending_parent = preceding
        for row_index, row in enumerate(rows[body:], start=body):
            vector = tuple(_number(row[col]) if col < len(row) else None for col in columns)
            raw_label = str(row[identity_col] if identity_col < len(row) else "").strip()
            if not any(value is not None for value in vector):
                label = _clean_name(raw_label)
                if label and not NOISE_IDENTITY.search(label) and not TOTAL.match(label):
                    pending_parent = label
                continue
            is_leaf = bool(re.match(r"^(?:其中[:：]?|[-–—·•]{1,3})", raw_label))
            parent_name = pending_parent if is_leaf and pending_parent not in expanded_parents else ""
            numeric_rows.append((row_index, row, vector, parent_name))
            if not is_leaf:
                pending_parent = ""
        if len(numeric_rows) < 3:
            result.rejection_reasons.append("too_few_hierarchy_rows")
            return result
        final_index = numeric_rows[-1][0]
        atomic_vectors = []
        for row_index, row, vector, parent_name in numeric_rows:
            raw_name = _clean_name(row[identity_col] if identity_col < len(row) else "")
            if row_index == final_index:
                name = "合计"
            else:
                is_subtotal = bool(atomic_vectors) and all(
                    value is not None and abs(value - sum((values[col_index] or 0.0) for values in atomic_vectors)) <= max(1.0, abs(value) * 1e-8)
                    for col_index, value in enumerate(vector)
                )
                if is_subtotal:
                    continue
                name = f"{parent_name}-{raw_name}" if parent_name else raw_name
                atomic_vectors.append(vector)
            if not name or NOISE_IDENTITY.search(name):
                continue
            for col_index, (col, year) in enumerate(columns.items()):
                amount = vector[col_index]
                if amount is None:
                    continue
                start, end = _period(year, context, item.prior_fiscal_month_day)
                result.facts.append(FieldFact(item.table.table_id, "MBREVENUE", amount, name, start, end, currency, unit, row_index, col))
        return _project_current_identities(_close(result))


class MultiSectionRowExtractor:
    """Extract atomic rows from repeated semantic sections and one final total."""
    table_types = {"multi_section_row"}

    def extract(self, classification):
        item = classification.evidence
        rows = [list(row) for row in item.table.rows]
        identity_col = _identity_column(rows)
        body = next((index for index, row in enumerate(rows)
                     if identity_col < len(row)
                     and any(_number(cell) is not None for cell in row[identity_col + 1:])), len(rows))
        context = _period_context(item, " ".join(
            [item.table.title] + [str(cell or "") for row in rows[:body + 1] for cell in row]
        ))
        columns = {}
        for col in range(identity_col + 1, item.column_count):
            descriptor = _header(rows, col, body)
            year = _year(descriptor)
            if year is not None and not NON_REVENUE_COLUMN.search(descriptor):
                columns[col] = year
        if not columns:
            fallback_year = _year(" ".join((item.table.title, item.table.context,
                                             item.document_period_text)))
            sample = rows[body] if body < len(rows) else []
            numeric_columns = [
                col for col in range(identity_col + 1, item.column_count)
                if col < len(sample) and _number(sample[col]) is not None
            ]
            if fallback_year is not None:
                columns = {col: fallback_year - offset
                           for offset, col in enumerate(numeric_columns)}
        result = ExtractionResult(classification)
        if not columns:
            result.rejection_reasons.append("no_revenue_period_columns")
            return result
        markers = {marker["row"]: marker for marker in item.section_markers}
        currency, unit = _measurement(item)
        section = next((marker for marker in reversed(item.section_markers)
                        if marker["row"] <= body), {"axis": "unknown", "label": ""})
        entries = []
        for row_index, row in enumerate(rows[body:], start=body):
            if row_index in markers:
                section = markers[row_index]
                continue
            if section.get("axis") != "product_service":
                continue
            raw_name = _clean_name(row[identity_col] if identity_col < len(row) else "")
            vector = tuple(_number(row[col]) if col < len(row) else None for col in columns)
            if not any(value is not None for value in vector) or SUBTOTAL.match(raw_name):
                continue
            name = "合计" if TOTAL.match(raw_name) or not raw_name else raw_name
            entries.append([row_index, section.get("label", ""), name, vector])
        seen_sections = defaultdict(set)
        for _, section_name, name, _ in entries:
            if name != "合计":
                seen_sections[name].add(section_name)
        emitted = set()
        for row_index, section_name, name, vector in entries:
            if name != "合计" and len(seen_sections[name]) > 1:
                key = (name, section_name)
                if name in emitted:
                    name = f"{section_name}:{name}"
                emitted.add(name.split(":")[-1])
                emitted.add(key[0])
            for col_index, (col, year) in enumerate(columns.items()):
                amount = vector[col_index]
                if amount is None:
                    continue
                start, end = _period(year, context, item.prior_fiscal_month_day)
                result.facts.append(FieldFact(item.table.table_id, "MBREVENUE", amount, name, start, end, currency, unit, row_index, col))
        result = _project_intermediate_aggregates(result)
        result = _synthesize_missing_totals(result)
        return _project_current_identities(_close(result))


EXTRACTORS = {
    "row_period": RowExtractor(),
    "row_metric_period": RowExtractor(),
    "row_measurement_period": RowExtractor(),
    "row_identity_total_period": RowIdentityTotalPeriodExtractor(),
    "column_metric_period": ColumnMetricExtractor(),
    "segment_matrix_period": ColumnMetricExtractor(),
    "mixed_hierarchy": MixedHierarchyExtractor(),
    "multi_section_row": MultiSectionRowExtractor(),
}


def _identity_key(value):
    return re.sub(r"[\s:：()（）\-–—_/]+", "", str(value or "")).lower()


class ExplicitMetricTableExtractor:
    """Read a separately titled cost/GP row table after revenue is fixed."""
    def extract(self, item, metric, revenue_facts):
        rows = [list(row) for row in item.table.rows]
        identities = {_identity_key(fact.product_name): fact.product_name for fact in revenue_facts}
        identity_col = _identity_column(rows)
        body = next((index for index, row in enumerate(rows)
                     if identity_col < len(row) and _identity_key(row[identity_col]) in identities
                     and any(_number(cell) is not None for cell in row[identity_col + 1:])), len(rows))
        context = " ".join([item.table.title] + [str(cell or "") for row in rows[:body + 1] for cell in row])
        periods = {(fact.start_date, fact.end_date) for fact in revenue_facts}
        columns = {}
        for col in range(identity_col + 1, item.column_count):
            year = _year(_header(rows, col, body))
            if year is not None: columns[col] = year
        column_periods = {
            col: _period(year, context, item.prior_fiscal_month_day)
            for col, year in columns.items()
        }
        disclosed_periods = sorted(set(column_periods.values()), reverse=True)
        revenue_periods = sorted(periods, reverse=True)
        if len(disclosed_periods) == len(revenue_periods) and set(disclosed_periods) != periods:
            rank_map = dict(zip(disclosed_periods, revenue_periods))
            column_periods = {col: rank_map[period] for col, period in column_periods.items()}
        currency, unit = _measurement(item)
        facts = []
        for row_index, row in enumerate(rows[body:], start=body):
            if identity_col >= len(row): continue
            raw_name = str(row[identity_col] or "").strip().replace("\n", " ")
            has_amount = any(col < len(row) and _number(row[col]) is not None for col in columns)
            name = "合计" if TOTAL.match(raw_name) or (not raw_name and has_amount) else identities.get(_identity_key(raw_name))
            if not name: continue
            for col, year in columns.items():
                if col >= len(row): continue
                amount = _number(row[col])
                if amount is None: continue
                start, end = column_periods[col]
                if (start, end) not in periods: continue
                facts.append(FieldFact(item.table.table_id, metric, amount, name, start, end, currency, unit, row_index, col))
        return facts


EXPLICIT_METRIC_EXTRACTOR = ExplicitMetricTableExtractor()
