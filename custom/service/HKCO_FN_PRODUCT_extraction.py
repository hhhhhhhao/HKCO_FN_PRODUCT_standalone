# -*- coding: utf-8 -*-
"""对唯一主表分类，再按分类结果抽取产品和收入。"""
import calendar
import datetime
import re

from custom.service.HKCO_FN_PRODUCT_selector import identity_key


NUMBER = re.compile(r"^\s*\(?-?[\d,]+(?:\.\d+)?\)?\s*$")
YEAR = re.compile(r"20\d{2}")
REVENUE = re.compile(r"收入|收益|營業額|营业额|銷售額|销售额|revenue|turnover|sales", re.I)
COST = re.compile(r"成本|cost", re.I)
GROSS_PROFIT = re.compile(r"毛利|毛損|毛损|gross profit|gross loss", re.I)
TOTAL = re.compile(r"^(?:合計|合计|總計|总计|總額|总额|total)$", re.I)
NOISE = re.compile(
    r"^(?:截至|年度|期間|期间|人民幣|人民币|港元|美元|歐元|欧元|"
    r"千元|百萬元|百万元|單位|单位|%|百分比)$",
    re.I,
)
CURRENCY = re.compile(r"人民幣|人民币|港幣|港币|港元|美元|歐元|欧元|日圓|日元", re.I)
UNIT = re.compile(r"百萬|百万|萬元|万元|千(?:元|港元|美元|歐元|欧元|日元)|元", re.I)


def _number(value):
    text = str(value or "").strip()
    if not NUMBER.fullmatch(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    amount = float(text.strip("() ").replace(",", ""))
    return -amount if negative else amount


def _year(value):
    match = YEAR.search(str(value or ""))
    return int(match.group()) if match else None


def _period(year, context):
    fiscal = tuple(context.get("prior_fiscal_month_day") or ())
    month, day = fiscal if len(fiscal) == 2 else (12, 31)
    day = min(day, calendar.monthrange(year, month)[1])
    end = datetime.date(year, month, day)
    start = datetime.date(year - 1, month, day) + datetime.timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _measurement(table, rows):
    text = " ".join(
        [str(table.get("section_title") or ""), str(table.get("context") or "")]
        + [str(cell or "") for row in rows[:4] for cell in row]
    )
    currency = CURRENCY.search(text)
    unit = UNIT.search(text)
    return (currency.group() if currency else ""), (unit.group() if unit else "")


def _header_text(rows, width, column, end):
    values = []
    for row_index in range(end + 1):
        row = rows[row_index]
        offset = max(0, width - len(row))
        source_column = column - offset
        if 0 <= source_column < len(row):
            values.append(str(row[source_column] or ""))
    return " ".join(values)


def _row_layout(rows):
    width = max((len(row) for row in rows), default=0)
    if width < 2:
        return None
    header_end = min(4, len(rows) - 1)
    year_columns = []
    for column in range(width):
        header = _header_text(rows, width, column, header_end)
        year = _year(header)
        if year:
            year_columns.append((column, year, header))
    if not year_columns:
        return None
    identity_scores = []
    for column in range(width):
        labels = [
            str(row[column] or "").strip() for row in rows[header_end + 1:]
            if column < len(row) and str(row[column] or "").strip()
        ]
        score = sum(_number(label) is None and not NOISE.fullmatch(label) for label in labels)
        identity_scores.append((score, -column, column))
    identity_column = max(identity_scores)[2]
    revenue_columns = [
        (column, year, header) for column, year, header in year_columns
        if not COST.search(header) and not GROSS_PROFIT.search(header)
    ]
    return header_end, identity_column, revenue_columns or year_columns


def _extract_rows(table, rows, context):
    layout = _row_layout(rows)
    if not layout:
        return []
    header_end, identity_column, columns = layout
    currency, unit = _measurement(table, rows)
    facts = []
    for row_index, row in enumerate(rows[header_end + 1:], start=header_end + 1):
        if identity_column >= len(row):
            continue
        name = str(row[identity_column] or "").strip()
        if not name or NOISE.fullmatch(name):
            continue
        name = "合计" if TOTAL.fullmatch(name) else name
        for column, year, header in columns:
            if column >= len(row):
                continue
            amount = _number(row[column])
            if amount is None:
                continue
            start, end = _period(year, context)
            facts.append({
                "table_id": table["id"], "metric": "MBREVENUE", "product_name": name,
                "amount": amount, "start_date": start, "end_date": end,
                "currency": currency, "unit": unit, "row_index": row_index,
                "column_index": column, "header": header,
            })
    return facts


def _extract_columns(table, rows, context):
    width = max((len(row) for row in rows), default=0)
    currency, unit = _measurement(table, rows)
    for metric_index, row in enumerate(rows):
        row_text = " ".join(str(cell or "") for cell in row)
        if not REVENUE.search(row_text) or COST.search(row_text) or GROSS_PROFIT.search(row_text):
            continue
        label_index = next((index for index in range(metric_index - 1, -1, -1)
                            if sum(_number(cell) is None and bool(str(cell or "").strip())
                                   for cell in rows[index][1:]) >= 2), None)
        if label_index is None:
            continue
        year = _year(" ".join(str(cell or "") for header in rows[:metric_index + 1] for cell in header))
        if not year:
            continue
        start, end = _period(year, context)
        facts = []
        for column in range(1, min(width, len(row), len(rows[label_index]))):
            name = str(rows[label_index][column] or "").strip()
            amount = _number(row[column])
            if not name or amount is None or NOISE.fullmatch(name):
                continue
            facts.append({
                "table_id": table["id"], "metric": "MBREVENUE", "product_name": name,
                "amount": amount, "start_date": start, "end_date": end,
                "currency": currency, "unit": unit, "row_index": metric_index,
                "column_index": column, "header": row_text,
            })
        if facts:
            return facts
    return []


def extract_main_table(table, context):
    """先分类，再按分类结果返回主表收入事实和可直接定位问题的 debug。"""
    if not table:
        return {"facts": [], "structure": "", "debug": {"stage": "no_main_table"}}
    rows = [list(row) for row in table.get("rows", []) if isinstance(row, (list, tuple))]
    row_layout = _row_layout(rows)
    classification = "products_in_rows" if row_layout else "products_in_columns"
    facts = _extract_rows(table, rows, context) if row_layout else []
    if classification == "products_in_columns":
        facts = _extract_columns(table, rows, context)
    if not facts:
        classification = "unsupported"
    unique = {}
    for fact in facts:
        key = (identity_key(fact["product_name"]), fact["start_date"], fact["end_date"])
        unique.setdefault(key, fact)
    facts = list(unique.values())
    return {
        "facts": facts,
        "classification": classification,
        "debug": {
            "stage": "main_table_extracted" if facts else "main_table_extraction_failed",
            "main_table_id": table.get("id", ""),
            "section_title": table.get("section_title", ""),
            "classification": classification,
            "row_count": len(rows),
            "fact_count": len(facts),
            "first_rows": rows[:8],
        },
    }
