# -*- coding: utf-8 -*-
"""从 run_backtest 的 debug 文件读 main_inner_lines，判断 GT 所有金额是否都在其中。"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


EXCLUDED_INFOCODES = {
    "AN202603271820814478",
    "AN202603271820813335",
}

NUMBER_TOKEN = re.compile(r"[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[+-]?\d+(?:\.\d+)?")


def _parse_amount(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace("−", "-").replace("—", "-")
    match = NUMBER_TOKEN.fullmatch(text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def _gt_amounts(rows):
    """GT 里所有非空 MBREVENUE 数值。"""
    amounts = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        value = _parse_amount(row.get("MBREVENUE"))
        if value is not None:
            amounts.add(value)
    return amounts


def _numbers_in_text(text):
    """从一行文本里提取数值，括号负数转负值。"""
    numbers = []
    for match in NUMBER_TOKEN.finditer(str(text or "")):
        token = match.group(0)
        try:
            value = float(token.replace(",", ""))
        except ValueError:
            continue
        start, end = match.start(), match.end()
        if token[0] not in "+-" and start > 0 and text[start - 1] in "（(" and end < len(text) and text[end] in "）)":
            value = -value
        numbers.append(value)
    return numbers


def _match_main_inner(selected_inner_lines, gt_amounts):
    """main_inner_lines 的文本里包含所有 GT 金额才算找到。"""
    if not selected_inner_lines:
        return False, "no_selection"

    all_numbers = []
    all_text = []
    for line in selected_inner_lines:
        if not isinstance(line, dict):
            continue
        text = str(line.get("text") or "")
        all_numbers.extend(_numbers_in_text(text))
        all_text.append(text)
        table = line.get("table")
        if isinstance(table, list):
            for row in table:
                if not isinstance(row, (list, tuple)):
                    continue
                for cell in row:
                    cell_text = str(cell or "")
                    all_numbers.extend(_numbers_in_text(cell_text))
                    all_text.append(cell_text)
    all_text = "\n".join(all_text)

    def amount_in_text(amount):
        if amount.is_integer():
            integer = int(amount)
            forms = {str(integer), f"{integer:,}"}
            if integer < 0:
                forms.update({
                    f"-{integer:,}",
                    f"({abs(integer):,})",
                    f"({abs(integer)})",
                })
            return any(form in all_text for form in forms)
        return f"{amount:.2f}" in all_text or f"{amount:g}" in all_text

    missing = [
        amount for amount in sorted(gt_amounts)
        if (
            not any(math.isclose(amount, value, rel_tol=1e-9, abs_tol=1e-6) for value in all_numbers)
            and not amount_in_text(amount)
        )
    ]
    if missing:
        return False, f"missing_amounts: {missing}"
    return True, "all_matched"


def _main_inner_lines_from_debug(debug_path):
    """从 run_backtest 的 debug 文本里解析 main_inner_lines=... 这一行。"""
    text = debug_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("main_inner_lines="):
            return json.loads(line[len("main_inner_lines="):])
    return None


def evaluate_one(info_code, debug_dir, gt_rows):
    debug_path = debug_dir / f"{info_code}_debug.txt"
    if not debug_path.is_file():
        return {"infocode": info_code, "status": "missing_debug"}

    selected_inner_lines = _main_inner_lines_from_debug(debug_path)
    if selected_inner_lines is None:
        return {"infocode": info_code, "status": "no_main_inner_lines"}

    gt_amounts = _gt_amounts(gt_rows)
    if not gt_amounts:
        return {"infocode": info_code, "status": "no_gt_amounts"}

    matched, reason = _match_main_inner(selected_inner_lines, gt_amounts)
    return {
        "infocode": info_code,
        "status": "evaluated",
        "correct": matched,
        "reason": reason,
        "gt_amount_count": len(gt_amounts),
        "gt_amounts": sorted(gt_amounts),
        "selected_line_count": len(selected_inner_lines or []),
        "selected_pages": sorted({
            line.get("page_number")
            for line in (selected_inner_lines or [])
            if line.get("page_number") is not None
        }),
    }


def _latest_debug_dir():
    batch_root = ROOT / "batch_runs" / "HKCO_FN_PRODUCT"
    if not batch_root.is_dir():
        return None
    runs = sorted(
        (path / "debug" for path in batch_root.iterdir() if path.is_dir()),
        key=lambda path: path.parent.name,
        reverse=True,
    )
    return next((path for path in runs if path.is_dir()), None)


def main():
    parser = argparse.ArgumentParser(description="Evaluate table selection from run_backtest debug files")
    parser.add_argument("--run-dir", default="", help="run_backtest 批次目录；默认取最近一次")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="兼容保留；debug 模式只读文件，不需要多进程",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="extra infocodes to exclude; repeatable or comma-separated",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "analysis" / "HKCO_FN_PRODUCT" / "selection_eval.json"),
    )
    args = parser.parse_args()

    extra_excluded = set()
    for value in args.exclude:
        extra_excluded.update(part.strip() for part in value.split(",") if part.strip())
    excluded_codes = EXCLUDED_INFOCODES | extra_excluded

    if args.run_dir:
        debug_dir = Path(args.run_dir) / "debug"
    else:
        debug_dir = _latest_debug_dir()
    if not debug_dir or not debug_dir.is_dir():
        print(f"No debug dir found: {debug_dir}")
        return 1

    gt = json.loads((ROOT / "tasks" / "HKCO_FN_PRODUCT" / "ground_truth.json").read_text(encoding="utf-8"))
    items = []
    for debug_path in sorted(debug_dir.glob("*_debug.txt")):
        info_code = debug_path.name[: -len("_debug.txt")]
        if info_code in gt and info_code not in excluded_codes:
            items.append((info_code, gt[info_code]))

    results = [evaluate_one(info_code, debug_dir, rows) for info_code, rows in items]
    correct = sum(1 for result in results if result.get("correct"))
    total = sum(1 for result in results if result["status"] == "evaluated")
    summary = {
        "total_documents": len(results),
        "evaluated": total,
        "correct": correct,
        "wrong": total - correct,
        "accuracy": round(correct / total, 4) if total else 0,
        "statuses": dict(Counter(result["status"] for result in results)),
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"summary": summary, "documents": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
