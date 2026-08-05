# -*- coding: utf-8 -*-
"""公告级抽取入口：按表格 classification 分发到对应分类的抽取函数。"""
from __future__ import annotations

from custom.service.HKCO_FN_PRODUCT_extraction.common import (
    COST,
    GROSS_PROFIT,
    _number,
    _year,
)
from custom.service.HKCO_FN_PRODUCT_extraction import (
    product_in_columns,
    product_in_rows,
    profit_loss,
)


EXTRACTORS = {
    "product_in_rows": product_in_rows.extract,
    "product_in_columns": product_in_columns.extract,
    "profit_loss": profit_loss.extract,
}


def extract_main_table(main_inner_lines, context):
    measurement = " ".join(line.get("text", "") for line in main_inner_lines or ())
    for inner_lines in main_inner_lines or ():
        if not inner_lines.get("is_table") or not inner_lines.get("table"):
            continue
        extractor = EXTRACTORS.get(inner_lines.get("classification"))
        if extractor is None:
            continue
        inner_lines.setdefault("period_text", measurement)
        return extractor(inner_lines, context)
    return []
