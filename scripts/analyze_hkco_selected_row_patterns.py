#!/usr/bin/env python3
"""Coarsely group selected row-period tables before changing materializers.

The pattern is fixed from raw current-table structure.  Backtest errors and GT
names are joined afterwards for prioritisation/diagnosis only.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_hkco_product_clusters import _load_document_lines
from custom.service.HKCO_FN_PRODUCT_document import get_all_source_tables


NUMBER = re.compile(r"^\s*\(?-?[\d,]+(?:\.\d+)?%?\)?\s*$")
TOTAL = re.compile(r"合計|合计|合共|總計|总计|總額|总额|total", re.I)
SUBTOTAL = re.compile(r"小計|小计|subtotal", re.I)
MAIN_OTHER = re.compile(r"主營業務|主营业务|其他業務|其他业务|主要業務|主要业务", re.I)
DIMENSION = re.compile(
    r"地區市場|地区市场|地理市場|地理市场|收入確認時間|收入确认时间|"
    r"收益確認時間|收益确认时间|銷售渠道|销售渠道|客户類型|客户类型|"
    r"geograph|recognition time|sales channel", re.I,
)
NON_REVENUE_METRIC = re.compile(
    r"成本|毛利|業績|业绩|溢利|利潤|利润|虧損|亏损|資產|资产|負債|负债|"
    r"開支|开支|費用|费用|EBITDA|cost|profit|result|asset|liabilit|expense", re.I,
)
REVENUE_METRIC = re.compile(r"收入|收益|營業額|营业额|銷售額|销售额|revenue|sales", re.I)


def _raw_tables(pdf_json, code):
    sources = get_all_source_tables(_load_document_lines(pdf_json / code))
    return {
        f"p{source.get('page_number') if source.get('page_number') is not None else 'x'}:{index}":
        source.get("target_table") or []
        for index, source in enumerate(sources)
    }


def _pattern(rows):
    numeric_indexes = [
        index for index, row in enumerate(rows)
        if any(NUMBER.match(str(cell or "")) for cell in row[1:])
    ]
    first = min(numeric_indexes, default=len(rows))
    labels = [str(row[0] or "").strip() if row else "" for row in rows]
    internal_headers = [
        label for index, label in enumerate(labels)
        if index > first and label and index not in numeric_indexes
    ]
    numeric_labels = [labels[index] for index in numeric_indexes]
    header_text = " ".join(str(cell or "") for row in rows[:first] for cell in row[1:])
    total_count = sum(bool(TOTAL.search(label)) or not label for label in numeric_labels)
    subtotal_count = sum(bool(SUBTOTAL.search(label)) for label in numeric_labels)
    metric_rows = sum(bool(NON_REVENUE_METRIC.search(label)) for label in numeric_labels)
    multi_metric_header = bool(REVENUE_METRIC.search(header_text) and NON_REVENUE_METRIC.search(header_text))
    if multi_metric_header:
        name = "multi_metric_columns"
    elif any(MAIN_OTHER.search(label) for label in internal_headers + numeric_labels):
        name = "main_other_business_sections"
    elif any(DIMENSION.search(label) for label in internal_headers + numeric_labels):
        name = "multiple_disclosure_dimensions"
    elif metric_rows >= 2:
        name = "metric_ledger_rows"
    elif total_count + subtotal_count >= 2:
        name = "multiple_subtotals"
    elif len(internal_headers) >= 2:
        name = "multiple_untyped_sections"
    else:
        name = "simple_row_period"
    return {
        "pattern": name, "row_count": len(rows), "column_count": max(map(len, rows), default=0),
        "total_count": total_count, "subtotal_count": subtotal_count,
        "metric_row_count": metric_rows, "internal_headers": internal_headers[:12],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("classification", type=Path)
    parser.add_argument("per_doc", type=Path)
    parser.add_argument("--document-class", default="multi_axis|business+product_service")
    parser.add_argument("--pdf-json", type=Path, default=Path("pdf_json"))
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--out", type=Path,
                        default=Path("analysis/HKCO_FN_PRODUCT/focus_selected_row_patterns.json"))
    args = parser.parse_args()

    classification = json.loads(args.classification.read_text(encoding="utf-8"))
    allowed = {document["infocode"] for document in classification["documents"]
               if document["document_class"] == args.document_class}
    rows = [row for row in json.loads(args.per_doc.read_text(encoding="utf-8")).get("rows", [])
            if row.get("infocode") in allowed]
    groups, members = defaultdict(Counter), []
    for row in rows:
        pipeline = row.get("pipeline") or {}
        if pipeline.get("selected_kind") != "row_period":
            continue
        selected = pipeline.get("selected_table")
        evidence = next((item for item in pipeline.get("evidence_summary", [])
                         if item.get("table_id") == selected), {})
        if "revenue" not in evidence.get("title_signals", []):
            continue
        raw = _raw_tables(args.pdf_json, row["infocode"]).get(selected, [])
        profile = _pattern(raw)
        stats = row.get("stats") or {}
        errors = {key: int(stats.get(key) or 0) for key in ("missing", "extra", "value_diff")}
        groups[profile["pattern"]].update({"docs": 1, **errors, "errors": sum(errors.values())})
        members.append({
            "infocode": row["infocode"], "selected_table": selected,
            "title": evidence.get("title", ""), "axis": pipeline.get("semantic_axis"),
            "profile": profile, "errors": errors,
            "gt_names_analysis_only": [item.get("PRODUCTNAME") for item in row.get("gt_items", [])],
            "extract_names_analysis_only": [item.get("PRODUCTNAME") for item in row.get("extract_items", [])],
            "raw_table": raw,
        })
    summaries = [dict(pattern=pattern, **counts) for pattern, counts in groups.items()]
    summaries.sort(key=lambda item: (-item["errors"], -item["docs"], item["pattern"]))
    selected_samples = []
    for summary in summaries:
        candidates = sorted((item for item in members if item["profile"]["pattern"] == summary["pattern"]),
                            key=lambda item: (-sum(item["errors"].values()), item["infocode"]))
        selected_samples.extend(candidates[:max(1, args.sample_size // max(1, len(summaries)))])
    output = {"document_class": args.document_class, "summaries": summaries,
              "member_count": len(members), "samples": selected_samples[:args.sample_size]}
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
