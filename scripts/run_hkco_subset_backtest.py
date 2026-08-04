#!/usr/bin/env python3
"""Re-run the document membership of an existing batch.

This is analysis orchestration only: it chooses document IDs from ``per_doc``
and delegates extraction/scoring unchanged to ``run_backtest``.  Keeping the
subset outside production code makes repeated structure-class validation
possible without introducing announcement lists into the pipeline.
"""
import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_backtest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("per_doc", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    rows = json.loads(args.per_doc.read_text(encoding="utf-8"))["rows"]
    infocodes = [row["infocode"] for row in rows]
    run_backtest.select_infocodes = lambda ground_truth, infocode="": infocodes
    run_backtest.run_backtest("HKCO_FN_PRODUCT", workers=max(1, args.workers))


if __name__ == "__main__":
    main()
