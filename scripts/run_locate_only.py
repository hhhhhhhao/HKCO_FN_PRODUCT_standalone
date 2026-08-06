#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""只跑定位并写 debug，跳过提取/AI/格式化/report。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom.service.EAPS_HKCO_FN_PRODUCT import get_lines
from custom.service.HKCO_FN_PRODUCT_document import get_lines_grouped
from custom.service.HKCO_FN_PRODUCT_selector import select_main_table
from custom.service.HKCO_FN_PRODUCT_utils import contains_chinese


def locate_one(task):
    code, pdf_path, prior_names, debug_dir = task
    debug_path = debug_dir / f"{code}_debug.txt"
    try:
        lines = get_lines(pdf_path)
        if not any(contains_chinese(str(line.get("text") or "")) for line in lines):
            debug_path.write_text(
                f"infocode={code}\nlines_count={len(lines)}\nreason_arr=无法识别（无中文）\n",
                encoding="utf-8",
            )
            return {"code": code, "status": "unrecognized"}
        groups = get_lines_grouped(lines)
        selected, related, from_full_history = select_main_table(
            pdf_path,
            groups,
            prior_names,
        )
        selected = selected or []
        payload = [
            {
                "page_number": line.get("page_number"),
                "text": str(line.get("text") or ""),
            }
            for line in selected
        ]
        lines_out = [
            f"infocode={code}",
            f"lines_count={len(lines)}",
            "",
            "=" * 72,
            "sections",
            "=" * 72,
            f"groups={len(groups)}",
            "",
            "=" * 72,
            "main_table_selection",
            "=" * 72,
            f"related_inner_lines={len(related)} from_full_history={from_full_history}",
            f"selected_page_numbers={sorted({line.get('page_number') for line in selected})}",
            f"selected_line_count={len(selected)}",
            "main_inner_lines=" + json.dumps(payload, ensure_ascii=False),
            "",
        ]
        debug_path.write_text("\n".join(lines_out), encoding="utf-8")
        return {"code": code, "status": "ok", "pages": sorted({line.get("page_number") for line in selected})}
    except Exception as exc:
        debug_path.write_text(
            f"infocode={code}\nerror={exc}\n",
            encoding="utf-8",
        )
        return {"code": code, "status": "error", "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--out-dir",
        default=str(ROOT / "batch_runs" / "HKCO_FN_PRODUCT" / "locate_only"),
    )
    args = parser.parse_args()

    gt = json.loads((ROOT / "tasks" / "HKCO_FN_PRODUCT" / "ground_truth.json").read_text(encoding="utf-8"))
    last_data = json.loads((ROOT / "tasks" / "HKCO_FN_PRODUCT" / "last_data.json").read_text(encoding="utf-8"))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_dir = Path(args.out_dir) / stamp / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    for code in sorted(gt):
        pdf_path = str(ROOT / "pdf_json" / f"{code}.pdf")
        prior = last_data.get(code, [])
        prior_names = [
            str(item.get("PRODUCTNAME") or "").strip()
            for item in prior
            if isinstance(item, dict) and str(item.get("PRODUCTNAME") or "").strip()
        ]
        tasks.append((code, pdf_path, prior_names, debug_dir))

    if args.workers <= 1 or len(tasks) <= 1:
        results = [locate_one(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            results = list(executor.map(locate_one, tasks, chunksize=4))

    from collections import Counter

    print(Counter(result["status"] for result in results))
    print(debug_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
