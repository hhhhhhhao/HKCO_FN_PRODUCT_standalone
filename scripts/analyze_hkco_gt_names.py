# -*- coding: utf-8 -*-
"""分析 gt_target_table_names.csv 里的目标表名称属于哪个 name 分类。"""
from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom.service.HKCO_FN_PRODUCT_classifier import _title_classification

CSV_PATH = ROOT / "analysis" / "HKCO_FN_PRODUCT" / "gt_target_table_names.csv"


def _name_category(table_name: str) -> str:
    return _title_classification(table_name)


def main():
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))

    type_counts = Counter(row["name_type"] for row in rows)
    category_counts = Counter()
    category_names = defaultdict(list)
    for row in rows:
        category = _name_category(row["table_name"])
        category_counts[category] += 1
        category_names[category].append((int(row["target_count"]), row["table_name"]))

    print("总行数:", len(rows))
    print("name_type:", dict(type_counts))
    print("名称分类:", dict(category_counts))
    print()
    for category in ("product_service", "business", "geography", "sales_channel", "customer", "recognition_time", "unknown"):
        names = sorted(category_names.get(category, []), key=lambda item: -item[0])
        print(f"[{category}] {len(names)}")
        for count, name in names[:10]:
            print(f"  {count:3}  {name}")
        print()


if __name__ == "__main__":
    main()
