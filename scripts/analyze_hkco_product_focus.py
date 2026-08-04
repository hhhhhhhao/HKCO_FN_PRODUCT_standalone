#!/usr/bin/env python3
"""Build a 50-document analysis dossier for a coarse structural family.

Family membership is derived only from pipeline evidence. Ground truth and
backtest differences are copied into the dossier strictly after membership is
fixed, for offline error analysis; this script is not imported by extraction.
"""
import argparse
import json
from pathlib import Path
import re

from analyze_hkco_product_clusters import structural_signature


NUMBER = re.compile(r"^\s*\(?-?[\d,]+(?:\.\d+)?\)?\s*$")
YEAR = re.compile(r"20\d{2}|二零[〇零一二三四五六七八九]{2}")
TOTAL = re.compile(r"合計|合计|總計|总计|總額|总额|total", re.I)


def _coarse(signature):
    parts = signature.split("|")
    if parts and parts[0] == "native_unresolved" and parts[-1].startswith("facts_"):
        parts[-1] = "facts_many" if parts[-1] != "facts_0" else parts[-1]
    return "|".join(parts)


def _table_profile(item):
    rows = item.get("target_table") or []
    width = max((len(row) for row in rows), default=0)
    data_rows = [row for row in rows[1:] if row]
    first_col_text = sum(bool(str(row[0] or "").strip()) and not NUMBER.match(str(row[0] or ""))
                         for row in data_rows)
    numeric_by_col = [sum(col < len(row) and bool(NUMBER.match(str(row[col] or "")))
                          for row in data_rows) for col in range(width)]
    header = " ".join(str(cell or "") for row in rows[:3] for cell in row)
    last = rows[-1] if rows else []
    last_label = str(last[0] or "").strip() if last else ""
    paired_identity_rows = sum(
        len(row) >= 3
        and bool(str(row[0] or "").strip()) and bool(str(row[1] or "").strip())
        and not NUMBER.match(str(row[0] or "")) and not NUMBER.match(str(row[1] or ""))
        and any(NUMBER.match(str(cell or "")) for cell in row[2:])
        for row in data_rows
    )
    header_rows = rows[:3]
    year_counts = {}
    for row in header_rows:
        for cell in row[1:]:
            for token in YEAR.findall(str(cell or "")):
                year_counts[token] = year_counts.get(token, 0) + 1
    section_markers = sum(bool(re.search(
        r"服務類型|服务类型|產品類型|产品类型|地區市場|地区市场|"
        r"收益確認時間|收益确认时间|revenue recognition|geographical market|type of services",
        " ".join(str(cell or "") for cell in row[:2]), re.I,
    )) for row in rows)
    return {
        "rows": len(rows),
        "columns": width,
        "first_column_text_rows": first_col_text,
        "numeric_cells_by_column": numeric_by_col,
        "year_in_header": bool(YEAR.search(header)),
        "blank_top_left": bool(rows and rows[0] and not str(rows[0][0] or "").strip()),
        "terminal_total": bool(last and (not last_label or TOTAL.search(last_label))
                               and any(NUMBER.match(str(cell or "")) for cell in last[1:])),
        "bilingual_identity_geometry": paired_identity_rows >= 2,
        "repeated_year_columns": any(count >= 2 for count in year_counts.values()),
        "semantic_section_markers": section_markers,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--family-rank", type=int, default=1)
    args = parser.parse_args()

    rows = json.loads((args.run_dir / "metrics" / "per_doc.json").read_text(encoding="utf-8"))["rows"]
    groups = {}
    for row in rows:
        key = _coarse(structural_signature(row))
        groups.setdefault(key, []).append(row)
    ranked = sorted(groups.items(), key=lambda pair: (
        -sum(sum(int((row.get("stats") or {}).get(field) or 0)
                 for field in ("missing", "extra", "value_diff")) for row in pair[1]),
        -len(pair[1]), pair[0],
    ))
    family, members = ranked[args.family_rank - 1]
    members = sorted(members, key=lambda row: -sum(
        int((row.get("stats") or {}).get(field) or 0) for field in ("missing", "extra", "value_diff")
    ))[:args.sample_size]

    dossier = []
    profile_counts = {
        "row_period_geometry": 0, "terminal_total": 0, "year_in_header": 0,
        "bilingual_identity_geometry": 0, "repeated_year_columns": 0,
        "multi_semantic_sections": 0,
    }
    for row in members:
        code = row["infocode"]
        target_path = args.run_dir / "debug" / f"{code}_target_item.json"
        target = json.loads(target_path.read_text(encoding="utf-8")) if target_path.exists() else {}
        profile = _table_profile(target)
        row_geometry = bool(
            profile["columns"] >= 2
            and profile["first_column_text_rows"] >= 2
            and sum(count >= 2 for count in profile["numeric_cells_by_column"][1:]) >= 1
            and profile["year_in_header"]
        )
        profile_counts["row_period_geometry"] += row_geometry
        profile_counts["terminal_total"] += profile["terminal_total"]
        profile_counts["year_in_header"] += profile["year_in_header"]
        profile_counts["bilingual_identity_geometry"] += profile["bilingual_identity_geometry"]
        profile_counts["repeated_year_columns"] += profile["repeated_year_columns"]
        profile_counts["multi_semantic_sections"] += profile["semantic_section_markers"] >= 2
        pipeline = row.get("pipeline") or {}
        matching_evidence = [item for item in pipeline.get("evidence_summary") or []
                             if item.get("page") == target.get("page_number")
                             and item.get("title") == target.get("title")]
        matching_ids = {item.get("table_id") for item in matching_evidence}
        matching_rejected = [item for item in pipeline.get("rejected_hypotheses") or []
                             if str(item.get("hypothesis_id") or "").split(":revenue:", 1)[0] in matching_ids]
        dossier.append({
            "infocode": code,
            "errors": {field: int((row.get("stats") or {}).get(field) or 0)
                       for field in ("missing", "extra", "value_diff")},
            "target_candidate": {**target, "profile": profile, "row_period_geometry": row_geometry},
            "matching_pipeline_evidence": matching_evidence,
            "matching_rejected_hypotheses": matching_rejected,
            "gt_analysis_only": row.get("gt_items") or [],
            "extract_analysis_only": row.get("extract_items") or [],
        })

    output = {
        "source": str(args.run_dir),
        "family": family,
        "family_doc_count": len(groups[family]),
        "sample_count": len(dossier),
        "profile_counts": profile_counts,
        "documents": dossier,
    }
    out_path = args.run_dir / "metrics" / f"focus_family_{args.family_rank}_{len(dossier)}.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
