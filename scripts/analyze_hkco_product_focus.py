#!/usr/bin/env python3
"""Build a raw-table dossier for one coarse class, without GT or extraction."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.analyze_hkco_product_clusters import REVENUE_ROLES, _load_document_lines
from custom.service.HKCO_FN_PRODUCT_document import get_all_source_tables


def _ranked_classes(payload, scope, eligibility):
    key = "revenue_table_classes" if scope == "revenue" else "table_classes"
    classes = list(payload[key])
    if scope == "revenue" and eligibility != "all":
        classes = [item for item in classes if item["class"].endswith("|" + eligibility)]
    return classes


def _raw_tables(pdf_json_dir, code):
    sources = get_all_source_tables(_load_document_lines(pdf_json_dir / code))
    return {
        f"p{source.get('page_number') if source.get('page_number') is not None else 'x'}:{index}": source
        for index, source in enumerate(sources)
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("classification", type=Path)
    parser.add_argument("--pdf-json", type=Path, default=Path("pdf_json"))
    parser.add_argument("--scope", choices=("revenue", "all"), default="revenue")
    parser.add_argument("--eligibility", choices=("eligible", "rejected", "all"), default="eligible")
    parser.add_argument("--class-rank", type=int, default=1)
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.classification.read_text(encoding="utf-8"))
    classes = _ranked_classes(payload, args.scope, args.eligibility)
    selected_class = classes[args.class_rank - 1]
    class_name = selected_class["class"]
    profile_key = "revenue_structure_class" if args.scope == "revenue" else "coarse_class"

    members = []
    for document in payload["documents"]:
        for table in document["tables"]:
            if table[profile_key] == class_name:
                members.append((document, table))
    members.sort(key=lambda pair: (
        pair[1]["title_family"], pair[1]["period_layout"], pair[0]["infocode"],
        pair[1]["table_id"],
    ))

    # Spread the sample over title families instead of taking 50 near-duplicates.
    by_title = {}
    for member in members:
        by_title.setdefault(member[1]["title_family"] or "<untitled>", []).append(member)
    sample = []
    title_groups = sorted(by_title.values(), key=lambda group: (-len(group), group[0][1]["title_family"]))
    while title_groups and len(sample) < args.sample_size:
        remaining = []
        for group in title_groups:
            if len(sample) >= args.sample_size:
                break
            sample.append(group.pop(0))
            if group:
                remaining.append(group)
        title_groups = remaining

    documents = []
    raw_cache = {}
    for document, table in sample:
        code = document["infocode"]
        if code not in raw_cache:
            raw_cache[code] = _raw_tables(args.pdf_json, code)
        raw = raw_cache[code].get(table["table_id"], {})
        competing = [
            {
                "table_id": other["table_id"],
                "title": other["title"],
                "role": other["role"],
                "geometry": other["geometry"],
                "semantic_axis": other["semantic_axis"],
                "eligible": other["supported_as_primary_revenue"],
            }
            for other in document["tables"]
            if other["table_id"] != table["table_id"]
            and other["role"] in REVENUE_ROLES
        ]
        documents.append({
            "infocode": code,
            "document_class": document["document_class"],
            "table_profile": table,
            "raw_table": raw.get("target_table") or [],
            "competing_revenue_tables": competing,
        })

    output = {
        "classification_source": str(args.classification),
        "scope": args.scope,
        "eligibility": args.eligibility,
        "class_rank": args.class_rank,
        "class": class_name,
        "class_table_count": len(members),
        "class_doc_count": len({document["infocode"] for document, _ in members}),
        "sample_count": len(documents),
        "summary": {
            "title_families": dict(Counter(table["title_family"] or "<untitled>"
                                             for _, table in members).most_common(50)),
            "period_layouts": dict(Counter(table["period_layout"] for _, table in members)),
            "measurements": dict(Counter(table["measurement"] for _, table in members)),
            "prior_alignment": dict(Counter(table["prior_alignment"] for _, table in members)),
            "relations": dict(Counter(table["table_relation"] for _, table in members)),
            "document_classes": dict(Counter(document["document_class"]
                                               for document, _ in members)),
        },
        "tables": documents,
    }
    output_path = args.out or args.classification.parent / (
        f"focus_{args.scope}_{args.eligibility}_{args.class_rank}_{len(documents)}.json"
    )
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
