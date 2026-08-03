#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
HKCO_FN_PRODUCT standalone runner — single document extraction entry point.

Usage:
  python main.py --pdf <path_to_pdf> --infocode <AN...>
  python main.py --mineru-dir <json_dir> --infocode <AN...>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from custom.service.EAPS_HKCO_FN_PRODUCT import extract_init


def main():
    parser = argparse.ArgumentParser(description="HKCO_FN_PRODUCT standalone extraction")
    parser.add_argument("--pdf", default="", help="Path to PDF file")
    parser.add_argument("--mineru-dir", default="", help="Directory containing per-page MinerU JSON files (pdf_path can be omitted)")
    parser.add_argument("--infocode", required=True, help="InfoCode (e.g. AN202502261643530572)")
    parser.add_argument("--notice-date", default="", help="Notice date (optional)")
    parser.add_argument("--output", default="", help="Output JSON path (default: stdout)")
    args = parser.parse_args()

    pdf_path = args.pdf
    infocode = args.infocode

    configs = {
        "pipeline_debug": True,
        "debug_dir": str(PROJECT_ROOT / "debug"),
    }
    if args.mineru_dir:
        # In standalone mode, if no PDF but MinerU JSON is available,
        # we pass the JSON dir as pdf_path and the get_all_paths will handle it
        if not pdf_path:
            pdf_path = args.mineru_dir
        configs["mineru_json_base_dir"] = str(Path(args.mineru_dir).parent)

    result = extract_init(pdf_path, infocode, "backtest", configs=configs, task_info_list=None)

    output = json.dumps(result, ensure_ascii=False, indent=2, default=str)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"Result written to: {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
