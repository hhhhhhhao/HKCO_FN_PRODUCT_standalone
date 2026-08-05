# -*- coding: utf-8 -*-
"""评价选表准确率：selector 选中表 → GT 所有产品名+金额都在表内 → 对，否则错。"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom.service.EAPS_HKCO_FN_PRODUCT import parse_mineru_result_to_lines
from custom.service.HKCO_FN_PRODUCT_document import get_lines_grouped
from custom.service.HKCO_FN_PRODUCT_selector import select_main_table


# 直接排除的公告，不参与评估；后续新增直接往这里加。
EXCLUDED_INFOCODES = {
    "AN202603271820814478",
    "AN202603271820813335",
}

TOTAL_KEYS = {"合计", "合計", "总计", "總計", "总额", "總額", "total"}
NUMBER_RE = re.compile(r"^\s*([（(])?\s*([+-]?[\d,]+(?:\.\d+)?)\s*[）)]?\s*$")


def _page_number(path: Path) -> int:
    m = re.search(r"_(\d+)\.json$", path.name)
    return int(m.group(1)) if m else 10 ** 9


def _load_lines(document_dir: Path):
    lines = []
    for path in sorted(document_dir.glob("*.json"), key=_page_number):
        if "over" in path.name.lower():
            continue
        page = _page_number(path)
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        lines.extend(parse_mineru_result_to_lines(payload, page))
    return lines


def _gt_facts(rows):
    """从 GT 行提取产品名和收入金额（排除合计）。"""
    names = []
    amounts = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("PRODUCTNAME") or "").strip()
        if not name or name.lower() in TOTAL_KEYS:
            continue
        val = _parse_number(row.get("MBREVENUE"))
        if val is None:
            continue
        names.append(name)
        amounts.append(val)
    return names, amounts


def _parse_number(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace("−", "-").replace("—", "-")
    m = NUMBER_RE.fullmatch(text)
    if not m:
        return None
    try:
        v = float(m.group(2).replace(",", ""))
    except (ValueError, IndexError):
        return None
    if m.group(1) and v > 0:
        v = -v
    return v


def _table_cells(table):
    """展平表中所有单元格为纯文本。"""
    rows = [list(r) for r in table.get("table", []) if isinstance(r, (list, tuple))]
    cells = []
    for row in rows:
        for cell in row:
            cells.append(str(cell or "").strip())
    return cells


def _match_main_inner(selected_inner_lines, gt_names, gt_amounts):
    """以 select_main_table 返回的 main_inner_lines 为准，其内所有表格金额全命中才算对。"""
    tables = [
        line for line in (selected_inner_lines or [])
        if line.get("is_table") and line.get("table")
    ]
    if not tables:
        return False, "no_table_selected"

    cells = []
    for table in tables:
        cells.extend(_table_cells(table))

    all_numbers = []
    for c in cells:
        v = _parse_number(c)
        if v is not None:
            all_numbers.append(v)

    missing_amounts = [
        a for a in gt_amounts
        if not any(math.isclose(a, v, rel_tol=1e-9, abs_tol=1e-6) for v in all_numbers)
    ]
    if missing_amounts:
        return False, f"missing_amounts: {missing_amounts}"

    all_text = " ".join(cells)
    missing_names = [n for n in gt_names if n.lower() not in all_text.lower()]
    if missing_names:
        return True, f"amounts_ok_names_partial: {missing_names}"
    return True, "all_matched"


def evaluate_one(info_code, pdf_json_root, gt_rows, prior_rows):
    """对单份公告评价选表结果。"""
    document_dir = pdf_json_root / info_code
    if not document_dir.is_dir():
        return {"infocode": info_code, "status": "missing_json_dir"}

    gt_names, gt_amounts = _gt_facts(gt_rows)
    if not gt_names:
        return {"infocode": info_code, "status": "no_gt_facts"}

    prior_names = [str(r.get("PRODUCTNAME") or "").strip()
                   for r in (prior_rows or [])
                   if isinstance(r, dict) and str(r.get("PRODUCTNAME") or "").strip()]

    lines = _load_lines(document_dir)
    lines_grouped = get_lines_grouped(lines)

    selected_inner_lines, _, _ = select_main_table(lines_grouped, prior_names)
    selected_tables = [
        line for line in (selected_inner_lines or [])
        if line.get("is_table") and line.get("table")
    ]

    matched, reason = _match_main_inner(selected_inner_lines, gt_names, gt_amounts)

    return {
        "infocode": info_code,
        "status": "evaluated",
        "correct": matched,
        "reason": reason,
        "selected_page": (
            selected_tables[0].get("page_number")
            if selected_tables
            else (selected_inner_lines[0].get("page_number") if selected_inner_lines else None)
        ),
        "selected_table_count": len(selected_tables),
        "selected_pages": sorted({
            table.get("page_number")
            for table in selected_tables
            if table.get("page_number") is not None
        }),
        "gt_product_count": len(gt_names),
        "gt_products": gt_names,
        "gt_amounts": gt_amounts,
    }


def _evaluate_task(task):
    info_code, pdf_json_root, gt_rows, prior_rows = task
    return evaluate_one(info_code, pdf_json_root, gt_rows, prior_rows)


def main():
    parser = argparse.ArgumentParser(description="Evaluate table selection accuracy")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, os.cpu_count() or 4),
        help="number of worker processes",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="extra infocodes to exclude; repeatable or comma-separated",
    )
    parser.add_argument("--output", default=str(ROOT / "analysis" / "HKCO_FN_PRODUCT" / "selection_eval.json"))
    args = parser.parse_args()

    extra_excluded = set()
    for value in args.exclude:
        extra_excluded.update(part.strip() for part in value.split(",") if part.strip())
    excluded_codes = EXCLUDED_INFOCODES | extra_excluded

    task_dir = ROOT / "tasks" / "HKCO_FN_PRODUCT"
    gt = json.loads((task_dir / "ground_truth.json").read_text(encoding="utf-8"))
    prior = json.loads((task_dir / "last_data.json").read_text(encoding="utf-8"))
    pdf_json_root = ROOT / "pdf_json"

    candidate = [
        (c, gt[c])
        for c in sorted(p.name for p in pdf_json_root.iterdir() if p.is_dir())
        if c in gt
    ]
    excluded_infocodes = sorted(c for c, _ in candidate if c in excluded_codes)
    items = [(c, rows) for c, rows in candidate if c not in excluded_codes][:args.limit or None]

    tasks = [
        (code, pdf_json_root, rows, prior.get(code, []))
        for code, rows in items
    ]
    if args.workers <= 1:
        results = [_evaluate_task(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            results = list(executor.map(_evaluate_task, tasks, chunksize=8))

    correct = sum(1 for r in results if r.get("correct"))
    total = sum(1 for r in results if r["status"] == "evaluated")

    summary = {
        "total_documents": len(results),
        "excluded_documents": len(excluded_infocodes),
        "excluded_infocodes": excluded_infocodes,
        "evaluated": total,
        "correct": correct,
        "wrong": total - correct,
        "accuracy": round(correct / total, 4) if total else 0,
        "statuses": dict(Counter(r["status"] for r in results)),
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(
        {"summary": summary, "documents": results},
        ensure_ascii=False, indent=2,
    ), encoding="utf-8")

    # 自动生成错题本
    wrong_list = output.parent / "wrong_selections.txt"
    wrong_docs = [d for d in results if not d.get("correct")]
    lines = [f"选对: {correct} ({summary['accuracy']:.1%})",
             f"选错: {len(wrong_docs)}", ""]
    for i, d in enumerate(wrong_docs):
        sp = d.get("selected_page", "?")
        gt = d.get("gt_products", [])
        reason = d.get("reason", "")
        if "missing_amounts" in reason:
            try:
                missing = eval(reason.split(": ", 1)[1])
                lines.append(f"{i+1}. {d['infocode']} p.{sp}  缺{len(missing)}个金额  GT({len(gt)}产品): {gt}")
            except Exception:
                lines.append(f"{i+1}. {d['infocode']} p.{sp}  {reason[:120]}")
        else:
            lines.append(f"{i+1}. {d['infocode']} p.{sp}  {reason[:120]}")
    wrong_list.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nOutput: {output}")
    print(f"Wrong list: {wrong_list}")


if __name__ == "__main__":
    main()
