# -*- coding: utf-8 -*-
"""AI 兜底：只做忠实信息提取，合计/过滤等业务逻辑留在后处理里。"""
from __future__ import annotations

import ast
import json
import re

from custom.service.HKCO_FN_PRODUCT_extraction.common import (
    _label_kind,
    _matches,
    _number,
    _year,
    get_currency,
    get_end_date,
    get_start_date,
    get_unit,
)
from custom.utils.call_gpt_util import call_gpt_service


def extract_ai_tables(table_blocks, pdf_path, info_code, context):
    """处理 ai_table 表块，返回事实列表和 debug 信息。"""
    return [], {
                "ai_table_count": 0,
                "ai_called_count": 0,
                "ai_failed_count": 0,
                "ai_failed_ids": [],
                "ai_fact_count": 0,
                "table_debug": [],
            }
    if not table_blocks:
        return [], {
            "ai_table_count": 0,
            "ai_called_count": 0,
            "ai_failed_count": 0,
            "ai_failed_ids": [],
            "ai_fact_count": 0,
            "table_debug": [],
        }

    facts = []
    ai_time = 0
    failed = []
    table_debug = []
    for block in table_blocks:
        table_text = _table_to_text(block)
        rows = block.get("table") or []
        entry = {
            "table_id": block.get("id", ""),
            "page_number": block.get("page_number"),
            "classification": block.get("classification"),
            "table_shape": [
                len(rows),
                max((len(row) for row in rows), default=0),
            ],
            "called": False,
            "raw_returned": "",
            "parse_ok": False,
            "parsed_row_count": 0,
            "fact_count": 0,
            "skipped": [],
            "error": "",
        }
        try:
            raw = _call_ai(table_text, info_code, block.get("id", ""))
            entry["called"] = True
            if raw is None:
                entry["error"] = "ai_returned_none"
                failed.append(block.get("id", ""))
            else:
                ai_time += 1
                entry["raw_returned"] = _summarize(raw)
                parsed = _parse_ai_result(raw)
                entry["parse_ok"] = bool(parsed)
                entry["parsed_row_count"] = len(parsed)
                block_facts, skipped = _post_process(block, parsed, context)
                entry["fact_count"] = len(block_facts)
                entry["skipped"] = skipped
                facts.extend(block_facts)
        except Exception as exc:
            entry["error"] = str(exc)
            failed.append(block.get("id", ""))
        table_debug.append(entry)

    debug = {
        "ai_table_count": len(table_blocks),
        "ai_called_count": ai_time,
        "ai_failed_count": len(failed),
        "ai_failed_ids": failed,
        "ai_fact_count": len(facts),
        "table_debug": table_debug,
    }
    return facts, debug


def _table_to_text(block):
    rows = block.get("table") or []
    if rows:
        return json.dumps(rows, ensure_ascii=False)
    lines = block.get("lines") or []
    return "\n".join(str(line.get("text") or "") for line in lines)


def _call_ai(text, info_code, table_id):
    system_info = """
你是一个金融表格信息提取器，只负责把输入表格中的内容原样提取出来。
禁止计算合计、禁止推断、禁止过滤、禁止改名、禁止补全。
输入是二维数组，可能含多行表头。
输出 JSON 数组，每个元素包含：
product_name: 项目名称，保持原文；没有则空字符串
period: 该金额对应的期间或年份表头，保持原文；没有则空字符串
amount: 金额字符串，保持原文，例如 1,499 或 (563)；没有则空字符串
unit: 单位，保持原文；没有则空字符串
currency: 币种，保持原文；没有则空字符串
只输出 JSON。
""".strip()
    payload = call_gpt_service(
        info_code,
        text,
        "HKCO_FN_PRODUCT",
        [],
        system_info,
        [],
        engine="gpt-5.1",
    )
    if not payload or str(payload.get("Code")) != "0":
        return None
    return payload.get("Data")


def _parse_ai_result(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return raw.get("Data") or raw
    text = str(raw or "")
    text = re.sub(r"```(?:json)?", "", text, flags=re.I).strip()
    start = text.find("[")
    if start < 0:
        return []
    depth = 0
    end = None
    for index in range(start, len(text)):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        return []
    snippet = text[start:end]
    try:
        parsed = json.loads(snippet)
    except Exception:
        try:
            parsed = ast.literal_eval(snippet)
        except Exception:
            return []
    return parsed if isinstance(parsed, list) else []


def _summarize(raw):
    if isinstance(raw, (list, dict)):
        text = json.dumps(raw, ensure_ascii=False)
    else:
        text = str(raw)
    return text[:500]


def _post_process(block, rows, context):
    facts = []
    skipped = []
    prior_names = context.get("prior_product_names") or ()
    measurement = block.get("text") or " ".join(
        str(line.get("text") or "") for line in (block.get("lines") or [])
    )
    default_currency = get_currency([measurement])
    default_unit = get_unit([measurement])
    year = _year(measurement)

    for row in rows or ():
        if not isinstance(row, dict):
            skipped.append({"row": str(row)[:80], "reason": "not_object"})
            continue
        amount = _number(row.get("amount"))
        if amount is None:
            skipped.append({
                "product_name": str(row.get("product_name") or "")[:60],
                "reason": "amount_invalid",
            })
            continue

        name = str(row.get("product_name") or "").strip()
        if not name:
            skipped.append({"row": str(row)[:80], "reason": "empty_product_name"})
            continue

        kind = _label_kind(name)
        if kind in ("final", "subtotal"):
            name = "合计"
        elif not any(
            key in name.replace(" ", "") or name.replace(" ", "") in key
            for key in prior_names
        ):
            if _matches("metric", name) or _matches("revenue_label", name):
                skipped.append({
                    "product_name": name[:60],
                    "reason": "metric_or_revenue_label",
                })
                continue

        period = str(row.get("period") or "").strip() or measurement
        end = get_end_date(period) or get_end_date(measurement)
        if end is None and year is not None:
            end = f"{year}-12-31"
        if end is None:
            skipped.append({
                "product_name": name[:60],
                "reason": "no_report_date",
            })
            continue
        period_year = _year(period) or year
        start = get_start_date(None, end, f"{period_year}年", measurement) if period_year else None
        if start is None:
            skipped.append({
                "product_name": name[:60],
                "reason": "no_start_date",
            })
            continue

        facts.append({
            "table_id": block.get("id", ""),
            "metric": "MBREVENUE",
            "product_name": name,
            "amount": amount,
            "start_date": start,
            "end_date": end,
            "currency": str(row.get("currency") or default_currency or "").strip(),
            "unit": str(row.get("unit") or default_unit or "").strip(),
        })

    unique = {}
    for fact in facts:
        key = (
            fact["product_name"].replace(" ", "").lower(),
            fact["start_date"],
            fact["end_date"],
            fact["metric"],
        )
        unique.setdefault(key, fact)
    return list(unique.values()), skipped
