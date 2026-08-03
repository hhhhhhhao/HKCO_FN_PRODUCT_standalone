#!/usr/bin/env python
"""
Convert flat export JSON into backtest ground_truth.json:

  { "RELINFOCODE1": [ {...}, ... ], "RELINFOCODE2": [ ... ] }

Example:
  from convert_gt_json import convert_gt_json

  convert_gt_json(
      "export.json",
      "tasks/xxx/ground_truth.json",
      fields=["STARTDATE", "REPORTDATE", "CURRENCY", "PRODUCTNAME", "MBREVENUE", "MBCOST", "GROSS_PROFIT", "UNIT"],
  )
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Union

PathLike = Union[str, Path]

DEFAULT_GROUP_FIELD = "INFOCODE"


def load_rows(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and payload and all(isinstance(v, list) for v in payload.values()):
        # 导出常见形态：{sql_or_key: [row, ...]}
        rows = []
        for key, items in payload.items():
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError(f"row 必须是对象: {path}")
                row = dict(item)
                row.setdefault(DEFAULT_GROUP_FIELD, key)
                rows.append(row)
    else:
        raise ValueError(f"Unsupported JSON structure: {path}")

    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("All rows must be objects")
    return [dict(row) for row in rows]


def project_row(row: Mapping[str, Any], fields: Sequence[str]) -> Dict[str, Any]:
    return {field: row.get(field) for field in fields}


def group_by_relinfocode(
    rows: Iterable[Mapping[str, Any]],
    group_field: str = DEFAULT_GROUP_FIELD,
    fields: Sequence[str] = (),
) -> tuple[Dict[str, List[Dict[str, Any]]], int]:
    if not fields:
        raise ValueError("fields 不能为空")
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    skipped = 0
    for row in rows:
        key = str(row.get(group_field) or "").strip()
        if not key:
            skipped += 1
            continue
        buckets[key].append(project_row(row, fields))

    grouped = {key: buckets[key] for key in sorted(buckets.keys())}
    return grouped, skipped


def convert_gt_json(
    input_path: PathLike,
    output_path: PathLike,
    fields: Sequence[str],
    *,
    group_by: str = DEFAULT_GROUP_FIELD,
    indent: int = 2,
) -> Dict[str, Any]:
    input_file = Path(input_path)
    if not input_file.exists():
        raise FileNotFoundError(input_file)

    if not fields:
        raise ValueError("fields 不能为空")

    output_file = Path(output_path)
    rows = load_rows(input_file)
    grouped, skipped = group_by_relinfocode(rows, group_field=group_by, fields=fields)

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(grouped, ensure_ascii=False, indent=indent) + "\n",
        encoding="utf-8",
    )

    return {
        "input": str(input_file),
        "output": str(output_file),
        "group_by": group_by,
        "pdf_count": len(grouped),
        "record_count": sum(len(v) for v in grouped.values()),
        "fields": list(fields),
        "skipped": skipped,
    }


if __name__ == "__main__":
    result = convert_gt_json(
        r"C:\Users\Administrator\Desktop\1.json",
        r"C:\Users\Administrator\Desktop\2.json",
        fields=["STARTDATE", "REPORTDATE", "CURRENCY", "PRODUCTNAME", "MBREVENUE", "MBCOST", "GROSS_PROFIT", "UNIT"],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
