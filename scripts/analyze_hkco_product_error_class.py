#!/usr/bin/env python3
"""Rank structural fingerprints inside one document error class.

Class membership comes from ``coarse_table_classes.json`` and is fixed before
this script reads backtest output.  Ground truth and extracted records are
copied into the dossier for diagnosis only; they never enter production code.
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
from custom.service.HKCO_FN_PRODUCT_identity import identity_matches


ERROR_FIELDS = ("missing", "extra", "value_diff")
NUMBER = re.compile(r"^\s*\(?-?[\d,]+(?:\.\d+)?\)?\s*$")


def _error_count(row):
    stats = row.get("stats") or {}
    return sum(int(stats.get(field) or 0) for field in ERROR_FIELDS)


def _table_signature(document):
    eligible = [table for table in document["tables"] if table["supported_as_primary_revenue"]]
    counts = Counter(
        (table["semantic_axis"], table["geometry"], table["role"])
        for table in eligible
    )
    return ";".join(
        f"{axis}/{geometry}/{role}={count}"
        for (axis, geometry, role), count in sorted(counts.items())
    ) or "no_eligible_table"


def _selected_signature(row):
    pipeline = row.get("pipeline") or {}
    return "/".join((
        str(pipeline.get("semantic_axis") or "none"),
        str(pipeline.get("selected_kind") or "none"),
        "supplement" if pipeline.get("supplemental_tables") else "single",
        str(pipeline.get("stage") or pipeline.get("stage_label") or "unknown"),
    ))


def _fingerprint(document, row):
    return " | ".join((_table_signature(document), _selected_signature(row)))


def _item_names(items):
    return [str(item.get("PRODUCTNAME") or "") for item in items or []]


def _raw_tables(pdf_json_dir, code):
    sources = get_all_source_tables(_load_document_lines(pdf_json_dir / code))
    return {
        f"p{source.get('page_number') if source.get('page_number') is not None else 'x'}:{index}":
        source.get("target_table") or []
        for index, source in enumerate(sources)
    }


def _identity_candidates(rows):
    """Amount-free row/column labels for analysis-only GT identity comparison."""
    names = []
    for row in rows:
        numeric = [index for index, cell in enumerate(row) if NUMBER.match(str(cell or ""))]
        boundary = min(numeric) if numeric else len(row)
        names.extend(
            str(cell or "").strip() for cell in row[:boundary]
            if str(cell or "").strip() and not NUMBER.match(str(cell or ""))
        )
    for row in rows[:8]:
        names.extend(
            str(cell or "").strip() for cell in row[1:]
            if str(cell or "").strip() and not NUMBER.match(str(cell or ""))
        )
    return list(dict.fromkeys(names))


def _analysis_target(document, row, raw):
    """Post-classification GT analysis only; never called by production."""
    gt_names = [name for name in _item_names(row.get("gt_items"))
                if name not in {"", "合计", "合計"}]
    scores = []
    for table in document["tables"]:
        if not table["supported_as_primary_revenue"]:
            continue
        hits = identity_matches(gt_names, _identity_candidates(raw.get(table["table_id"], [])))
        scores.append((hits, table["semantic_axis"], table["geometry"], table["table_id"]))
    best = max((score[0] for score in scores), default=0)
    best_axes = sorted({score[1] for score in scores if score[0] == best and best > 0})
    return {
        "best_gt_identity_hits_analysis_only": best,
        "best_axes_analysis_only": best_axes or ["unmatched"],
        "candidate_scores_analysis_only": [
            {"hits": hits, "axis": axis, "geometry": geometry, "table_id": table_id}
            for hits, axis, geometry, table_id in sorted(scores, reverse=True)
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("classification", type=Path)
    parser.add_argument("per_doc", type=Path)
    parser.add_argument("--class-rank", type=int, default=1)
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--pdf-json", type=Path, default=Path("pdf_json"))
    parser.add_argument("--out", type=Path,
                        default=Path("analysis/HKCO_FN_PRODUCT/focus_error_class_1_50.json"))
    args = parser.parse_args()

    classification = json.loads(args.classification.read_text(encoding="utf-8"))
    per_doc = json.loads(args.per_doc.read_text(encoding="utf-8"))
    rows = {row["infocode"]: row for row in per_doc.get("rows", [])}
    ranked_classes = sorted(
        classification["document_classes"],
        key=lambda item: (-item["total_errors"], -item["doc_count"], item["class"]),
    )
    selected_class = ranked_classes[args.class_rank - 1]
    documents = [
        document for document in classification["documents"]
        if document["document_class"] == selected_class["class"]
        and document["infocode"] in rows
    ]

    groups = defaultdict(list)
    analysis_targets = {}
    for document in documents:
        row = rows[document["infocode"]]
        groups[_fingerprint(document, row)].append((document, row))
        raw = _raw_tables(args.pdf_json, document["infocode"])
        analysis_targets[document["infocode"]] = _analysis_target(document, row, raw)

    axis_groups = defaultdict(list)
    for document in documents:
        row = rows[document["infocode"]]
        pipeline = row.get("pipeline") or {}
        target = analysis_targets[document["infocode"]]
        key = " | ".join((
            f"selected={pipeline.get('semantic_axis') or 'none'}/{pipeline.get('selected_kind') or 'none'}",
            f"gt_identity_axis={'+'.join(target['best_axes_analysis_only'])}",
            f"gt_hits={target['best_gt_identity_hits_analysis_only']}",
        ))
        axis_groups[key].append((document, row))

    axis_error_groups = []
    for key, members in axis_groups.items():
        errors = Counter()
        for _, row in members:
            stats = row.get("stats") or {}
            errors.update({field: int(stats.get(field) or 0) for field in ERROR_FIELDS})
        axis_error_groups.append({
            "group": key,
            "doc_count": len(members),
            "missing": errors["missing"], "extra": errors["extra"],
            "value_diff": errors["value_diff"], "total_errors": sum(errors.values()),
        })
    axis_error_groups.sort(key=lambda item: (-item["total_errors"], -item["doc_count"], item["group"]))

    fingerprints = []
    for fingerprint, members in groups.items():
        errors = Counter()
        statuses = Counter()
        for _, row in members:
            stats = row.get("stats") or {}
            errors.update({field: int(stats.get(field) or 0) for field in ERROR_FIELDS})
            statuses[str(row.get("status") or "")] += 1
        fingerprints.append({
            "fingerprint": fingerprint,
            "doc_count": len(members),
            "missing": errors["missing"],
            "extra": errors["extra"],
            "value_diff": errors["value_diff"],
            "total_errors": sum(errors.values()),
            "statuses": dict(statuses),
        })
    fingerprints.sort(key=lambda item: (-item["total_errors"], -item["doc_count"], item["fingerprint"]))

    # Round-robin over structural fingerprints, prioritising error mass within
    # each group.  This prevents 50 near-identical announcements dominating.
    queues = []
    for profile in fingerprints:
        members = sorted(groups[profile["fingerprint"]],
                         key=lambda pair: (-_error_count(pair[1]), pair[0]["infocode"]))
        queues.append(members)
    sample = []
    while queues and len(sample) < args.sample_size:
        remaining = []
        for queue in queues:
            if len(sample) >= args.sample_size:
                break
            sample.append(queue.pop(0))
            if queue:
                remaining.append(queue)
        queues = remaining

    dossier = []
    for document, row in sample:
        pipeline = row.get("pipeline") or {}
        stats = row.get("stats") or {}
        dossier.append({
            "infocode": document["infocode"],
            "fingerprint": _fingerprint(document, row),
            "status": row.get("status"),
            "errors": {field: int(stats.get(field) or 0) for field in ERROR_FIELDS},
            "selected": {
                "table": pipeline.get("selected_table"),
                "kind": pipeline.get("selected_kind"),
                "axis": pipeline.get("semantic_axis"),
                "planned_tables": pipeline.get("planned_tables") or [],
                "supplemental_tables": pipeline.get("supplemental_tables") or [],
                "rejected_hypotheses": pipeline.get("rejected_hypotheses") or [],
            },
            "gt_identity_alignment_analysis_only": analysis_targets[document["infocode"]],
            "gt_names_analysis_only": _item_names(row.get("gt_items")),
            "extract_names_analysis_only": _item_names(row.get("extract_items")),
            "tables": [
                {
                    key: table[key] for key in (
                        "table_id", "page", "title", "role", "geometry",
                        "semantic_axis", "supported_as_primary_revenue",
                        "classification_reasons", "layout_signals", "axis_signals",
                        "prior_alignment", "row_count", "column_count",
                    )
                }
                for table in document["tables"]
                if table["role"] in {
                    "primary_revenue_detail", "segment_revenue", "generic_revenue",
                    "revenue_with_metrics", "product_service_breakdown",
                }
            ],
        })

    output = {
        "classification_source": str(args.classification),
        "per_doc_analysis_only": str(args.per_doc),
        "document_class": selected_class,
        "axis_error_groups_analysis_only": axis_error_groups,
        "document_axis_index_analysis_only": [
            {
                "infocode": document["infocode"],
                "selected_axis": (rows[document["infocode"]].get("pipeline") or {}).get("semantic_axis"),
                "selected_kind": (rows[document["infocode"]].get("pipeline") or {}).get("selected_kind"),
                "selected_table": (rows[document["infocode"]].get("pipeline") or {}).get("selected_table"),
                "target": analysis_targets[document["infocode"]],
                "errors": {
                    field: int((rows[document["infocode"]].get("stats") or {}).get(field) or 0)
                    for field in ERROR_FIELDS
                },
            }
            for document in documents
        ],
        "fingerprints": fingerprints,
        "sample_count": len(dossier),
        "documents": dossier,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
