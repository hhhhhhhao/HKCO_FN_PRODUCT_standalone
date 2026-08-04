#!/usr/bin/env python3
"""Cluster HKCO_FN_PRODUCT backtest documents by disclosure structure.

Ground truth is used only through the already-computed error fields in
``per_doc.json``.  Cluster membership depends exclusively on pipeline evidence
and decisions, so this report cannot feed answer knowledge back into extraction.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re


ERROR_FIELDS = ("missing", "extra", "value_diff")


def _title_class(title):
    text = str(title or "")
    patterns = (
        ("other_income", r"其他(?:收入|收益)|other income"),
        ("cost_profit", r"成本|毛利|溢利|利潤|虧損|亏损|cost|profit|loss"),
        ("financial_statement", r"損益表|收益表|全面收益|income statement|comprehensive income"),
        ("segment", r"分部|segment"),
        ("geography", r"地區|地区|地理|geograph"),
        ("primary_revenue", r"營業額|营业额|銷售額|销售额|收入|收益|revenue|turnover|sales"),
    )
    return next((name for name, pattern in patterns if re.search(pattern, text, re.I)), "unknown")


def _selected_evidence(pipeline):
    selected = str(pipeline.get("selected_hypothesis") or "")
    table_id = selected.split(":revenue:", 1)[0] if ":revenue:" in selected else ""
    evidence = pipeline.get("evidence_summary") or []
    return next((item for item in evidence if item.get("table_id") == table_id), {})


def _dominant(values, default="none"):
    values = [str(value) for value in values if value]
    return Counter(values).most_common(1)[0][0] if values else default


def _fact_band(value):
    value = int(value or 0)
    if value == 0:
        return "facts_0"
    if value == 1:
        return "facts_1"
    if value <= 5:
        return "facts_2_5"
    if value <= 15:
        return "facts_6_15"
    return "facts_16_plus"


def structural_signature(row):
    """Return a coarse, GT-independent structural family key."""
    pipeline = row.get("pipeline") or {}
    if pipeline.get("selected_hypothesis"):
        evidence = _selected_evidence(pipeline)
        axes = "+".join(sorted(evidence.get("axes") or [])) or pipeline.get("semantic_axis") or "unknown"
        sections = evidence.get("sections") or []
        return "|".join((
            "native",
            str(pipeline.get("selected_kind") or "unknown"),
            str(pipeline.get("semantic_axis") or "unknown"),
            str(pipeline.get("revenue_basis") or "unknown"),
            "sections" if sections else "whole_table",
            "multi_axis" if "+" in axes else axes,
            _title_class(evidence.get("title")),
        ))

    rejected = pipeline.get("rejected_hypotheses") or []
    evidence = pipeline.get("evidence_summary") or []
    lead = max(rejected, key=lambda item: (
        bool(item.get("fact_count")),
        -len(item.get("rejection_reasons") or []),
        int(item.get("fact_count") or 0),
    ), default={})
    lead_table_id = str(lead.get("hypothesis_id") or "").split(":revenue:", 1)[0]
    lead_evidence = next((item for item in evidence if item.get("table_id") == lead_table_id), {})
    structures = set(lead_evidence.get("structures") or [])
    axes = set(lead_evidence.get("axes") or [])
    shape = next((name for name, signal in (
        ("matrix", "explicit_total_column"),
        ("multi_sections", "multi_semantic_sections"),
        ("hierarchy", "hierarchy_markers"),
        ("column_identity", "identity_labels_in_header"),
        ("row_identity", "product_labels_in_rows"),
    ) if signal in structures), "unresolved")
    return "|".join((
        "native_unresolved",
        str(pipeline.get("failure_stage") or "unknown"),
        str(lead.get("kind") or "none"),
        _dominant(lead.get("rejection_reasons") or []),
        shape,
        "+".join(sorted(axes)) or "unknown",
        _title_class(lead_evidence.get("title")),
        _fact_band(lead.get("fact_count")),
    ))


def _error_stats(row):
    stats = row.get("stats") or {}
    return {field: int(stats.get(field) or 0) for field in ERROR_FIELDS}


def build_clusters(rows, sample_size):
    groups = defaultdict(list)
    for row in rows:
        groups[structural_signature(row)].append(row)
    clusters = []
    for signature, members in groups.items():
        errors = Counter()
        for member in members:
            errors.update(_error_stats(member))
        ranked = sorted(
            members,
            key=lambda item: (-sum(_error_stats(item).values()), item.get("infocode") or ""),
        )
        perfect = sum(member.get("status") == "完全匹配" for member in members)
        clusters.append({
            "signature": signature,
            "doc_count": len(members),
            "perfect_docs": perfect,
            "problem_docs": len(members) - perfect,
            "missing": errors["missing"],
            "extra": errors["extra"],
            "value_diff": errors["value_diff"],
            "total_errors": sum(errors.values()),
            "errors_per_doc": round(sum(errors.values()) / len(members), 3),
            "sample_infocodes": [item.get("infocode") for item in ranked[:sample_size]],
        })
    return sorted(clusters, key=lambda item: (-item["total_errors"], -item["doc_count"], item["signature"]))


def _markdown(clusters, run_dir, min_docs):
    lines = [
        "# HKCO_FN_PRODUCT structural clusters",
        "",
        f"Source: `{run_dir}`",
        "",
        "Cluster membership uses pipeline evidence only; errors are analysis-only aggregates.",
        "",
        "| Rank | Docs | Perfect | Missing | Extra | Value diff | Total errors | Signature |",
        "|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    shown = [item for item in clusters if item["doc_count"] >= min_docs]
    for index, item in enumerate(shown, 1):
        signature = item["signature"].replace("|", " / ")
        lines.append(
            f"| {index} | {item['doc_count']} | {item['perfect_docs']} | {item['missing']} | "
            f"{item['extra']} | {item['value_diff']} | {item['total_errors']} | {signature} |"
        )
    lines.extend(("", "## Samples", ""))
    for index, item in enumerate(shown[:20], 1):
        lines.append(f"### {index}. {item['signature']}")
        lines.append("")
        lines.append(", ".join(item["sample_infocodes"]))
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path, help="batch run directory containing metrics/per_doc.json")
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--min-docs", type=int, default=2)
    parser.add_argument("--out", type=Path, help="output prefix; defaults to <run_dir>/metrics/structural_clusters")
    args = parser.parse_args()
    per_doc = args.run_dir / "metrics" / "per_doc.json"
    rows = json.loads(per_doc.read_text(encoding="utf-8"))["rows"]
    clusters = build_clusters(rows, max(1, args.sample_size))
    output = args.out or args.run_dir / "metrics" / "structural_clusters"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(
        json.dumps({"run_dir": str(args.run_dir), "clusters": clusters}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    output.with_suffix(".md").write_text(_markdown(clusters, args.run_dir, args.min_docs), encoding="utf-8")
    print(output.with_suffix(".json"))
    print(output.with_suffix(".md"))


if __name__ == "__main__":
    main()
