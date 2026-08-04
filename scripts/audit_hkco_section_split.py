# -*- coding: utf-8 -*-
"""审计 HKCO 第一阶段：OCR lines 的章节切割。

数据范围和目标物理表来自 GT 事实解析结果 ``gt_target_tables.json``。脚本读取每份
公告的全部 MinerU 页面，但重点检查解析出的所有有效目标页：

1. 切割前后的 line 和 table 数量必须完全一致；
2. table line 不能成为章节边界；
3. 每张表必须且只能属于一个章节；
4. GT 目标页必须存在表格，且目标表格必须获得非空章节标题；
5. 记录目标页表格所在章节；同一章节可包含续表或同主题的多张物理表。

运行：``python scripts/audit_hkco_section_split.py``
输出：``analysis/HKCO_FN_PRODUCT/section_split_audit.json``
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom.service.EAPS_HKCO_FN_PRODUCT import parse_mineru_result_to_lines
from custom.service.HKCO_FN_PRODUCT_document import get_lines_grouped, is_title_line


def _page_number(path: Path) -> int:
    match = re.search(r"_(\d+)\.json$", path.name)
    return int(match.group(1)) if match else 10 ** 9


def _load_lines(document_dir: Path):
    lines = []
    for path in sorted(document_dir.glob("*.json"), key=_page_number):
        if "over" in path.name.lower():
            continue
        page = _page_number(path)
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        lines.extend(parse_mineru_result_to_lines(payload, page))
    return lines


def _section_title(group):
    for line in group:
        if line.get("is_table"):
            break
        if not line.get("is_table"):
            text = str(line.get("text") or "").strip()
            if text:
                return text
    return ""


def audit_document(info_code, target_pages, pdf_json_root):
    document_dir = pdf_json_root / info_code
    if not document_dir.is_dir():
        return {"infocode": info_code, "status": "missing_json_dir", "target_pages": target_pages}

    lines = _load_lines(document_dir)
    groups = get_lines_grouped(lines)
    flat = [line for group in groups for line in group]
    source_tables = [line for line in lines if line.get("is_table")]
    grouped_tables = [line for line in flat if line.get("is_table")]
    table_memberships = Counter(id(line) for group in groups for line in group if line.get("is_table"))

    target_assignments = []
    target_multi_table_groups = []
    for group_index, group in enumerate(groups):
        title = _section_title(group)
        tables = [line for line in group if line.get("is_table")]
        if len(tables) > 1 and any(line.get("page_number") in target_pages for line in tables):
            target_multi_table_groups.append({
                "group_index": group_index,
                "section_title": title,
                "table_pages": [line.get("page_number") for line in tables],
                "table_count": len(tables),
            })
        boundary_kind = (
            "title" if group and is_title_line(group[0]) else
            "table" if group and group[0].get("is_table") else
            "preamble"
        )
        for table_index, line in enumerate(tables):
            if line.get("page_number") in target_pages:
                line_index = next(index for index, item in enumerate(group) if item is line)
                target_assignments.append({
                    "page": line.get("page_number"),
                    "group_index": group_index,
                    "table_index_in_group": table_index,
                    "section_title": title,
                    "section_boundary_kind": boundary_kind,
                    "section_page": group[0].get("page_number") if group else None,
                    "lines_from_section_start": line_index,
                    "group_table_count": len(tables),
                    "nearby_text_before_table": [
                        str(item.get("text") or "").strip()[:300]
                        for item in group[max(0, line_index - 8):line_index]
                        if not item.get("is_table") and str(item.get("text") or "").strip()
                    ],
                    "first_row": (line.get("table") or [[]])[0][:8],
                })

    target_pages_with_table = sorted({item["page"] for item in target_assignments})
    problems = []
    if len(flat) != len(lines) or any(left is not right for left, right in zip(lines, flat)):
        problems.append("line_partition_changed")
    if len(source_tables) != len(grouped_tables):
        problems.append("table_count_changed")
    if any(count != 1 for count in table_memberships.values()):
        problems.append("table_membership_not_one")
    if any(is_title_line(line) for line in source_tables):
        problems.append("table_used_as_title_boundary")
    missing_target_pages = sorted(set(target_pages) - set(target_pages_with_table))
    if missing_target_pages:
        problems.append("target_page_without_table")
    if any(item["section_boundary_kind"] == "preamble" for item in target_assignments):
        problems.append("target_table_in_unbounded_preamble")

    return {
        "infocode": info_code,
        "status": "ok" if not problems else "problem",
        "target_pages": target_pages,
        "missing_target_pages": missing_target_pages,
        "line_count": len(lines),
        "group_count": len(groups),
        "table_count": len(source_tables),
        "target_table_count": len(target_assignments),
        "untitled_target_table_count": sum(
            not item["section_title"] for item in target_assignments
        ),
        "target_multi_table_group_count": len(target_multi_table_groups),
        "problems": problems,
        "target_assignments": target_assignments,
        "target_multi_table_groups": target_multi_table_groups,
    }


def main():
    parser = argparse.ArgumentParser(description="Audit HKCO OCR section splitting")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--output",
        default=str(ROOT / "analysis" / "HKCO_FN_PRODUCT" / "section_split_audit.json"),
    )
    args = parser.parse_args()

    gt_path = ROOT / "analysis" / "HKCO_FN_PRODUCT" / "gt_target_tables.json"
    pdf_json_root = ROOT / "pdf_json"
    resolved = json.loads(gt_path.read_text(encoding="utf-8"))["documents"]
    items = [
        (item["infocode"], item)
        for item in resolved
        if item.get("status") == "resolved"
    ][:args.limit or None]
    results = []
    for code, meta in items:
        target_pages = [int(page) for page in meta.get("target_pages", [])]
        results.append(audit_document(code, target_pages, pdf_json_root))
    problem_counts = Counter(problem for item in results for problem in item.get("problems", []))
    summary = {
        "documents": len(results),
        "ok_documents": sum(item.get("status") == "ok" for item in results),
        "problem_documents": sum(item.get("status") == "problem" for item in results),
        "missing_json_dirs": sum(item.get("status") == "missing_json_dir" for item in results),
        "tables": sum(item.get("table_count", 0) for item in results),
        "target_tables": sum(item.get("target_table_count", 0) for item in results),
        "untitled_target_tables": sum(
            item.get("untitled_target_table_count", 0) for item in results
        ),
        "target_multi_table_groups": sum(
            item.get("target_multi_table_group_count", 0) for item in results
        ),
        "problem_counts": dict(sorted(problem_counts.items())),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"summary": summary, "documents": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(output)


if __name__ == "__main__":
    main()
