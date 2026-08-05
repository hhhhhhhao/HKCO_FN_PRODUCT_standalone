# -*- coding: utf-8 -*-
"""按 GT 目标表结构抽 50 份核对，不写分类实现，只做验证。"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TARGET_STRUCTURES = [
    "row_period",
    "segment_matrix_period",
    "row_metric_period",
    "row_identity_total_period",
    "mixed_hierarchy",
    "multi_section_row",
    "row_measurement_period",
    "unsupported",
]


def main():
    target_data = json.loads(
        (ROOT / "analysis" / "HKCO_FN_PRODUCT" / "gt_target_tables_after_history_override.json")
        .read_text(encoding="utf-8")
    )
    two = json.loads(
        (ROOT / "analysis" / "HKCO_FN_PRODUCT" / "two_stage_table_classes.json")
        .read_text(encoding="utf-8")
    )
    two_docs = {doc["infocode"]: doc for doc in two["documents"]}

    groups = defaultdict(list)
    for item in target_data["documents"]:
        code = item["infocode"]
        target_ids = item.get("target_table_ids") or []
        if not target_ids:
            continue
        doc = two_docs.get(code)
        if not doc:
            continue
        table = next(
            (t for t in doc.get("tables", []) if t.get("table_id") == target_ids[0]),
            None,
        )
        if not table:
            continue
        geometry = table.get("geometry") or "unsupported"
        target = next(iter(item.get("targets") or []), {})
        matched_facts = target.get("matched_facts") or []
        amount_ok = bool(matched_facts) and all(
            fact.get("amount_hit") for fact in matched_facts
        )
        groups[geometry].append({
            "infocode": code,
            "page": target.get("page") or table.get("page"),
            "table_id": target_ids[0],
            "amount_ok": amount_ok,
            "fact_count": len(matched_facts),
            "title": (table.get("title") or "")[:70],
        })

    lines = ["# GT 目标表结构 50 份核对\n"]
    summary = []
    for geometry in TARGET_STRUCTURES:
        items = groups.get(geometry, [])
        sampled = items[:50]
        ok = sum(1 for item in sampled if item["amount_ok"])
        summary.append({
            "geometry": geometry,
            "available": len(items),
            "sampled": len(sampled),
            "amount_ok": ok,
            "accuracy": round(ok / len(sampled), 4) if sampled else 0,
        })
        lines.append(f"\n## {geometry}\n")
        lines.append(
            f"可用 {len(items)}，抽样 {len(sampled)}，GT 金额全部命中 {ok}"
        )
        lines.append("\n| 公告 | 页 | 金额命中 | 事实数 | 标题 |")
        lines.append("|---|---|---:|---:|---|")
        for item in sampled:
            lines.append(
                f"| {item['infocode']} | p.{item['page']} | {item['amount_ok']} "
                f"| {item['fact_count']} | {item['title']} |"
            )

    output = ROOT / "analysis" / "HKCO_FN_PRODUCT" / "target_structure_50_review.md"
    output.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(output)


if __name__ == "__main__":
    main()
