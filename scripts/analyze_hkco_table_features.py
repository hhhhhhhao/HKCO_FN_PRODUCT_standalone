# -*- coding: utf-8 -*-
"""Analyze HKCO main-table features against GT hits.

For every local document the script runs the production selector, classifies
the returned ``main_inner_lines`` with title + table geometry, then compares
all GT revenue amounts against the amounts inside ``main_inner_lines``.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom.service.HKCO_FN_PRODUCT_classifier import classify_main_inner
from custom.service.HKCO_FN_PRODUCT_document import get_lines_grouped
from custom.service.HKCO_FN_PRODUCT_selector import select_main_table
import scripts.eval_table_selection as ev


def _title_family(title: str) -> str:
    return re.sub(r"[\s:：,，。;；()（）\[\]【】\d.、\-–—]+", "", title or "").strip()


def _supported_classification(classification: str) -> bool:
    return classification in {"product_in_rows", "product_in_columns"}


def _main_inner_hit(main_inner_lines, gt_amounts):
    numbers = []
    for line in main_inner_lines or []:
        if not (line.get("is_table") and line.get("table")):
            continue
        for row in line["table"]:
            for cell in row:
                value = ev._parse_number(cell)
                if value is not None:
                    numbers.append(value)
    missing = [
        amount for amount in gt_amounts
        if not any(abs(amount - value) < 1e-6 for value in numbers)
    ]
    return not missing, missing


def analyze_one(info_code, pdf_json_root, gt_rows, prior_rows):
    document_dir = pdf_json_root / info_code
    if not document_dir.is_dir():
        return {"infocode": info_code, "status": "missing_json_dir"}

    gt_names, gt_amounts = ev._gt_facts(gt_rows)
    if not gt_names:
        return {"infocode": info_code, "status": "no_gt_facts"}

    prior_names = [
        str(row.get("PRODUCTNAME") or "").strip()
        for row in (prior_rows or [])
        if isinstance(row, dict) and str(row.get("PRODUCTNAME") or "").strip()
    ]
    lines = ev._load_lines(document_dir)
    lines_grouped = get_lines_grouped(lines)
    main_inner_lines, _, _ = select_main_table(lines_grouped, prior_names)
    classify_main_inner(main_inner_lines, prior_names)
    hit, missing = _main_inner_hit(main_inner_lines, gt_amounts)

    tables = [
        line for line in (main_inner_lines or [])
        if line.get("is_table") and line.get("table")
    ]
    classifications = [
        line.get("classification", "")
        for line in tables
    ]
    main_classification = next(
        (classification for classification in classifications if _supported_classification(classification)),
        classifications[0] if classifications else "",
    )
    name = main_classification
    table_type = ""

    row_count = column_count = 0
    signals = []
    if tables:
        table_rows = [
            row for row in tables[0]["table"]
            if isinstance(row, (list, tuple))
        ]
        row_count = len(table_rows)
        column_count = max((len(row) for row in table_rows), default=0)
        signals = []

    return {
        "infocode": info_code,
        "status": "evaluated",
        "hit": hit,
        "missing": missing,
        "gt_product_count": len(gt_names),
        "gt_products": gt_names,
        "section_title": main_inner_lines[0]["text"] if main_inner_lines else "",
        "table_count_in_main": len(tables),
        "name": name,
        "table_type": table_type,
        "revenue_basis": "reported",
        "supported": _supported_classification(main_classification),
        "reasons": [],
        "row_count": row_count,
        "column_count": column_count,
        "signals": signals,
    }


def _accumulate_class(agg, row):
    key = (
        f"{row['name'] or 'none'}/"
        f"{row['table_type'] or 'none'}/"
        f"{row['revenue_basis'] or 'none'}"
    )
    item = agg[key]
    item["count"] += 1
    item["hit"] += int(row["hit"])
    item["row_count_sum"] += row["row_count"]
    item["column_count_sum"] += row["column_count"]
    item["title_families"][_title_family(row["section_title"])] += 1
    if len(item["examples"]) < 3:
        item["examples"].append({
            "infocode": row["infocode"],
            "title": row["section_title"],
            "hit": row["hit"],
            "missing": row["missing"],
            "rows": row["row_count"],
            "cols": row["column_count"],
        })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--output",
        default=str(ROOT / "analysis" / "HKCO_FN_PRODUCT" / "table_feature_analysis.json"),
    )
    args = parser.parse_args()

    task_dir = ROOT / "tasks" / "HKCO_FN_PRODUCT"
    gt = json.loads((task_dir / "ground_truth.json").read_text(encoding="utf-8"))
    prior = json.loads((task_dir / "last_data.json").read_text(encoding="utf-8"))
    pdf_json_root = ROOT / "pdf_json"

    items = [
        (code, gt[code])
        for code in sorted(path.name for path in pdf_json_root.iterdir() if path.is_dir())
        if code in gt
    ][: args.limit or None]

    rows = []
    for code, gt_rows in items:
        rows.append(analyze_one(code, pdf_json_root, gt_rows, prior.get(code, [])))

    evaluated = [row for row in rows if row["status"] == "evaluated"]
    total = len(evaluated)
    hit_total = sum(int(row["hit"]) for row in evaluated)

    class_agg = defaultdict(lambda: {
        "count": 0,
        "hit": 0,
        "row_count_sum": 0,
        "column_count_sum": 0,
        "title_families": Counter(),
        "examples": [],
    })
    for row in evaluated:
        _accumulate_class(class_agg, row)

    class_stats = []
    for key, item in sorted(
        class_agg.items(),
        key=lambda pair: pair[1]["count"],
        reverse=True,
    ):
        class_stats.append({
            "class": key,
            "count": item["count"],
            "hit": item["hit"],
            "accuracy": round(item["hit"] / item["count"], 4) if item["count"] else 0,
            "avg_rows": round(item["row_count_sum"] / item["count"], 2) if item["count"] else 0,
            "avg_cols": round(item["column_count_sum"] / item["count"], 2) if item["count"] else 0,
            "top_titles": item["title_families"].most_common(8),
            "examples": item["examples"],
        })

    signal_agg = defaultdict(lambda: {"present": 0, "present_hit": 0, "absent": 0, "absent_hit": 0})
    for row in evaluated:
        for signal in row["signals"]:
            signal_agg[signal]["present"] += 1
            signal_agg[signal]["present_hit"] += int(row["hit"])
        for signal in signal_agg:
            if signal not in row["signals"]:
                signal_agg[signal]["absent"] += 1
                signal_agg[signal]["absent_hit"] += int(row["hit"])

    signal_stats = [
        {
            "signal": signal,
            "present": item["present"],
            "present_hit": item["present_hit"],
            "present_accuracy": round(item["present_hit"] / item["present"], 4) if item["present"] else 0,
            "absent": item["absent"],
            "absent_hit": item["absent_hit"],
            "absent_accuracy": round(item["absent_hit"] / item["absent"], 4) if item["absent"] else 0,
        }
        for signal, item in sorted(signal_agg.items(), key=lambda pair: -pair[1]["present"])
    ]

    by_type = Counter((row["table_type"] or "none", row["supported"]) for row in evaluated)
    by_name = Counter((row["name"] or "none", row["supported"]) for row in evaluated)

    summary = {
        "total_documents": total,
        "hit": hit_total,
        "accuracy": round(hit_total / total, 4) if total else 0,
        "by_table_type": [
            {"table_type": key[0], "supported": key[1], "count": value}
            for key, value in sorted(by_type.items(), key=lambda pair: -pair[1])
        ],
        "by_name": [
            {"name": key[0], "supported": key[1], "count": value}
            for key, value in sorted(by_name.items(), key=lambda pair: -pair[1])
        ],
    }

    payload = {
        "summary": summary,
        "classes": class_stats,
        "signals": signal_stats,
        "documents": evaluated,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nOutput: {output}")


if __name__ == "__main__":
    main()
