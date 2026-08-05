# -*- coding: utf-8 -*-
"""分类后按类别抽取主营收入。

分类 -> 抽取：
- segment_matrix_period：产品在列，收入在行
- row_period / row_metric_period / row_identity_total_period：产品在行，按年份列提取
- unsupported：不抽取
"""
from __future__ import annotations

import calendar
import datetime
import re
from typing import Any, Dict, List, Optional, Sequence

from custom.service.HKCO_FN_PRODUCT_classifier import classify_main_inner


NUMBER = re.compile(r"^\s*\(?-?[\d,]+(?:\.\d+)?\)?\s*$")
YEAR = re.compile(r"20\d{2}|二零[〇零一二三四五六七八九]{2}|二〇[〇零一二三四五六七八九]{2}", re.I)
REVENUE = re.compile(r"收入|收益|營業額|营业额|銷售額|销售额|revenue|turnover|sales", re.I)
COST = re.compile(r"成本|cost", re.I)
GROSS_PROFIT = re.compile(r"毛利|毛損|毛损|gross profit|gross loss", re.I)
PROFIT = re.compile(r"溢利|利潤|利润|虧損|亏损|損益|损益|profit|loss", re.I)
TOTAL = re.compile(r"^(?:合計|合计|小計|小计|總計|总计|總額|总额|總收入|总收入|總收益|总收益|subtotal|total)$", re.I)
NOISE = re.compile(
    r"^(?:截至|年度|期間|期间|人民幣|人民币|港元|美元|歐元|欧元|日圓|日元|"
    r"千元|百萬|百万|萬元|万元|單位|单位|%|百分比|-|–|—)$",
    re.I,
)
CURRENCY = re.compile(r"人民幣|人民币|港幣|港币|港元|美元|歐元|欧元|日圓|日元", re.I)
UNIT = re.compile(r"百萬|百万|萬|万|千|元|million|thousand", re.I)


def _number(value: Any) -> Optional[float]:
    text = str(value or "").strip()
    if not NUMBER.fullmatch(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    amount = float(text.strip("() ").replace(",", ""))
    return -amount if negative else amount


def _year(value: Any) -> Optional[int]:
    match = YEAR.search(str(value or ""))
    if not match:
        return None
    token = match.group(0)
    if token.isdigit():
        return int(token)
    cn = {"〇": "0", "零": "0", "一": "1", "二": "2", "三": "3", "四": "4",
          "五": "5", "六": "6", "七": "7", "八": "8", "九": "9"}
    return 2000 + int("".join(cn.get(ch, "") for ch in token[2:]))


def _period(year: int, context: Dict[str, Any]):
    fiscal = tuple(context.get("prior_fiscal_month_day") or ())
    month, day = fiscal if len(fiscal) == 2 else (12, 31)
    day = min(day, calendar.monthrange(year, month)[1])
    end = datetime.date(year, month, day)
    start = datetime.date(year - 1, month, day) + datetime.timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _rows(table: Dict[str, Any]) -> List[List[Any]]:
    return [list(row) for row in table.get("table", []) if isinstance(row, (list, tuple))]


def _clean_name(value: Any) -> str:
    name = re.sub(r"^(?:其中[:：]?\s*|[-–—·•]{1,3}\s*)", "", str(value or "").strip())
    return name


def _is_text_label(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and text not in {"-", "–", "—"} and _number(text) is None


def _clean_header_name(value: Any) -> str:
    text = str(value or "").replace("\n", " ")
    text = YEAR.sub("", text)
    text = CURRENCY.sub("", text)
    text = UNIT.sub("", text)
    return re.sub(r"[()（）\s/]+", "", text)


def _revenue_row_index(rows: List[List[Any]]):
    candidates = []
    for index, row in enumerate(rows):
        row_text = " ".join(str(cell or "") for cell in row)
        if (
            REVENUE.search(row_text)
            and not COST.search(row_text)
            and not GROSS_PROFIT.search(row_text)
            and not PROFIT.search(row_text)
        ):
            priority = 0 if re.search(r"外部|對外|对外|external", row_text, re.I) else 1
            candidates.append((priority, index, row_text))
    if not candidates:
        return None, ""
    priority, index, row_text = min(candidates)
    return index, row_text


def _year_columns(rows: List[List[Any]], width: int):
    header_end = min(4, len(rows) - 1)
    columns = []
    for column in range(width):
        values = []
        for row in rows[:header_end + 1]:
            source_column = column - max(0, width - len(row))
            if 0 <= source_column < len(row):
                values.append(str(row[source_column] or ""))
        header = " ".join(values)
        year = _year(header)
        if year and not re.search(r"%|百分比", header) and any(
            column < len(row) and _number(row[column]) is not None
            for row in rows[header_end + 1:]
        ):
            columns.append((column, year, header))
    return columns


def _identity_column(rows: List[List[Any]], header_end: int, width: int) -> int:
    scores = []
    for column in range(width):
        text_count = sum(
            1
            for row in rows[header_end + 1:]
            if column < len(row)
            and str(row[column] or "").strip()
            and _number(row[column]) is None
            and not NOISE.fullmatch(str(row[column] or "").strip())
        )
        scores.append((text_count, -column, column))
    return max(scores)[2]


def _extract_products_in_rows(
    rows: List[List[Any]],
    context: Dict[str, Any],
    table_id: str,
    currency: str,
    unit: str,
    width: int,
) -> List[Dict[str, Any]]:
    year_columns = _year_columns(rows, width)
    if not year_columns:
        return []
    body_start = 0
    for index, row in enumerate(rows):
        first = _clean_name(row[0]) if row else ""
        if (
            any(_number(cell) is not None for cell in row)
            and first
            and _year(first) is None
            and not TOTAL.search(first)
        ):
            body_start = index
            break
    header_end = body_start - 1
    identity_column = _identity_column(rows, header_end, width)
    revenue_columns = [
        (column, year, header)
        for column, year, header in year_columns
        if not COST.search(header) and not GROSS_PROFIT.search(header)
    ] or year_columns

    facts = []
    for row_index, row in enumerate(rows[header_end + 1:], start=header_end + 1):
        if identity_column >= len(row):
            continue
        name = _clean_name(row[identity_column])
        if not name or NOISE.fullmatch(name) or TOTAL.fullmatch(name):
            continue
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
    return facts


def _extract_products_in_columns(
    rows: List[List[Any]],
    context: Dict[str, Any],
    table_id: str,
    currency: str,
    unit: str,
    width: int,
    fallback_year: Optional[int] = None,
) -> List[Dict[str, Any]]:
    metric_index, row_text = _revenue_row_index(rows)
    if metric_index is None:
        return []
    row = rows[metric_index]
    label_index = next(
        (
            index
            for index in range(metric_index - 1, -1, -1)
            if sum(
                _is_text_label(cell)
                for cell in rows[index]
            ) >= 2
        ),
        None,
    )
    if label_index is None:
        return []
    year = _year(" ".join(
        str(cell or "")
        for header in rows[:metric_index + 1]
        for cell in header
    )) or fallback_year
    if not year:
        return []
    start, end = _period(year, context)
    label_row = rows[label_index]
    header_offset = 1 if _clean_header_name(label_row[0]) else 0
    facts = []
    for column in range(header_offset, min(width, len(row))):
        name_index = column - header_offset
        if name_index >= len(label_row):
            continue
        name = _clean_header_name(label_row[name_index])
        amount = _number(row[column])
        if name and amount is not None and not NOISE.fullmatch(name) and not TOTAL.fullmatch(name):
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
    return facts


def _classification_supported(classification):
    return classification in {"product_in_rows", "product_in_columns"}


def extract_main_table(main_inner_lines, context):
    if not main_inner_lines:
        return {
            "facts": [],
            "classification": "unsupported",
            "debug": {"stage": "no_main_table"},
        }

    tables = [
        line for line in main_inner_lines
        if line.get("is_table") and line.get("table")
    ]
    supported_indices = [
        index
        for index, table in enumerate(tables)
        if _classification_supported(table.get("classification", ""))
    ]
    main_index = supported_indices[0] if supported_indices else 0
    table = tables[main_index] if tables else None
    if not table:
        return {
            "facts": [],
            "classification": "unsupported",
            "debug": {"stage": "no_main_table"},
        }

    rows = _rows(table)
    width = max((len(row) for row in rows), default=0)
    measurement = " ".join(line.get("text", "") for line in main_inner_lines)
    currency_match = CURRENCY.search(measurement)
    unit_match = UNIT.search(measurement)
    currency = currency_match.group() if currency_match else ""
    unit = unit_match.group() if unit_match else ""
    table_id = table.get("id", "")
    classification_str = table.get("classification", "/")
    supported = _classification_supported(classification_str)

    # 产品在列：找外部客户收入行
    facts = []
    if supported and classification_str == "product_in_columns":
        fallback_year = _year(measurement)
        facts = _extract_products_in_columns(
            rows, context, table_id, currency, unit, width, fallback_year,
        )
        classification = classification_str
    # 产品在行：按年份列提取，跳过小计/合计/百分比列
    elif supported and classification_str == "product_in_rows":
        facts = _extract_products_in_rows(
            rows, context, table_id, currency, unit, width,
        )
        classification = classification_str
    else:
        classification = "unsupported"

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
            "main_table_id": table_id,
            "section_title": main_inner_lines[0]["text"],
            "classification": classification,
            "classifier": {
                "classification": classification_str,
                "supported": supported,
            },
            "row_count": len(rows),
            "fact_count": len(facts),
        },
    }
