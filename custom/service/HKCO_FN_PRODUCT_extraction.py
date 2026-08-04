# -*- coding: utf-8 -*-
"""对唯一主表分类，再按分类结果抽取产品和收入。"""
import calendar
import datetime
import re

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


def extract_main_table(main_inner_lines, context):
    """从唯一主章节中的物理表抽取产品和收入。"""
    if not main_inner_lines:
        return {
            "facts": [],
            "classification": "unsupported",
            "debug": {"stage": "no_main_table"},
        }

    table = next(
        (
            line for line in main_inner_lines
            if line.get("is_table") and line.get("table")
        ),
        None,
    )
    if not table:
        return {
            "facts": [],
            "classification": "unsupported",
            "debug": {"stage": "no_main_table"},
        }

    rows = [list(row) for row in table["table"] if isinstance(row, (list, tuple))]
    width = max((len(row) for row in rows), default=0)
    measurement = " ".join(line["text"] for line in main_inner_lines)
    currency_match = CURRENCY.search(measurement)
    unit_match = UNIT.search(measurement)
    currency = currency_match.group() if currency_match else ""
    unit = unit_match.group() if unit_match else ""
    table_id = table.get("id", "")

    # 产品在行、年份在列。
    year_columns = []
    header_end = min(4, len(rows) - 1)
    if width >= 2:
        for column in range(width):
            values = []
            for row_index in range(header_end + 1):
                row = rows[row_index]
                source_column = column - max(0, width - len(row))
                if 0 <= source_column < len(row):
                    values.append(str(row[source_column] or ""))
            header = " ".join(values)
            year = _year(header)
            if year:
                year_columns.append((column, year, header))

    facts = []
    if year_columns:
        identity_columns = []
        for column in range(width):
            labels = [
                str(row[column] or "").strip()
                for row in rows[header_end + 1:]
                if column < len(row) and str(row[column] or "").strip()
            ]
            text_count = sum(_number(label) is None and not NOISE.fullmatch(label) for label in labels)
            identity_columns.append((text_count, -column, column))
        identity_column = max(identity_columns)[2]
        revenue_columns = [
            column for column in year_columns
            if not COST.search(column[2]) and not GROSS_PROFIT.search(column[2])
        ] or year_columns

        for row_index, row in enumerate(rows[header_end + 1:], start=header_end + 1):
            if identity_column >= len(row):
                continue
            name = str(row[identity_column] or "").strip()
            if not name or NOISE.fullmatch(name):
                continue
            name = "合计" if TOTAL.fullmatch(name) else name
            for column, year, header in revenue_columns:
                amount = _number(row[column]) if column < len(row) else None
                if amount is None:
                    continue
                start, end = _period(year, context)
                facts.append({
                    "table_id": table_id,
                    "metric": "MBREVENUE",
                    "product_name": name,
                    "amount": amount,
                    "start_date": start,
                    "end_date": end,
                    "currency": currency,
                    "unit": unit,
                    "row_index": row_index,
                    "column_index": column,
                    "header": header,
                })
        classification = "products_in_rows"

    # 产品在列、收入在行。
    else:
        classification = "products_in_columns"
        for metric_index, row in enumerate(rows):
            row_text = " ".join(str(cell or "") for cell in row)
            if not REVENUE.search(row_text) or COST.search(row_text) or GROSS_PROFIT.search(row_text):
                continue
            label_index = next(
                (
                    index for index in range(metric_index - 1, -1, -1)
                    if sum(
                        _number(cell) is None and bool(str(cell or "").strip())
                        for cell in rows[index][1:]
                    ) >= 2
                ),
                None,
            )
            if label_index is None:
                continue
            year = _year(" ".join(str(cell or "") for header in rows[:metric_index + 1] for cell in header))
            if not year:
                continue
            start, end = _period(year, context)
            for column in range(1, min(width, len(row), len(rows[label_index]))):
                name = str(rows[label_index][column] or "").strip()
                amount = _number(row[column])
                if name and amount is not None and not NOISE.fullmatch(name):
                    facts.append({
                        "table_id": table_id,
                        "metric": "MBREVENUE",
                        "product_name": name,
                        "amount": amount,
                        "start_date": start,
                        "end_date": end,
                        "currency": currency,
                        "unit": unit,
                        "row_index": metric_index,
                        "column_index": column,
                        "header": row_text,
                    })
            if facts:
                break

    if not facts:
        classification = "unsupported"

    unique = {}
    for fact in facts:
        key = (str(fact["product_name"]).strip().lower(), fact["start_date"], fact["end_date"])
        unique.setdefault(key, fact)
    facts = list(unique.values())
    return {
        "facts": facts,
        "classification": classification,
        "debug": {
            "stage": "main_table_extracted" if facts else "main_table_extraction_failed",
            "main_table_id": table.get("id", ""),
            "section_title": main_inner_lines[0]["text"],
            "classification": classification,
            "row_count": len(rows),
            "fact_count": len(facts),
        },
    }
