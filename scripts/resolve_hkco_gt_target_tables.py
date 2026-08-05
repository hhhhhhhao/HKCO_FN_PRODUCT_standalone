# -*- coding: utf-8 -*-
"""使用 ground truth 产品—收入事实直接定位公告 JSON 中的目标物理表。

这里直接对公告中的每张二维表计算三个独立证据：

1. GT 产品名在表格单元格中的命中数；
2. GT MBREVENUE 在数值单元格中的命中数；
3. 产品名与对应收入位于同一行或同一列的配对命中数。

排序以配对命中优先，其次产品名、金额。并列最高的物理表全部保留为有效目标，
不强制生成唯一目标页。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom.service.HKCO_FN_PRODUCT_document import get_lines_grouped
from custom.service.HKCO_FN_PRODUCT_selector import select_main_table
from custom.service.HKCO_FN_PRODUCT_utils import historical_product_last_name_matches
from custom.service.EAPS_HKCO_FN_PRODUCT import parse_mineru_result_to_lines


TOTAL_KEYS = {"合计", "合計", "总计", "總計", "总额", "總額", "total"}
NUMBER = re.compile(r"^\s*([（(])?\s*([+-]?[\d,]+(?:\.\d+)?)\s*[）)]?\s*$")
TABLE_NAME_SEMANTICS = re.compile(
    r"收入|收益|營業額|营业额|營收|营收|銷售|销售|"
    r"分部|分類|分类|分拆|分列|細分|细分|明細|明细|"
    r"產品|产品|服務|服务|業務|业务|經營|经营|營運|营运|"
    r"revenue|turnover|sales|segment|product|service",
    re.IGNORECASE,
)


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


def _table_titles(lines):
    """返回表前短窗口内最近的业务语义标题，跳过日期、单位等元数据。"""
    titles = {}
    recent_text = deque(maxlen=8)
    for line in lines:
        if line.get("is_table") and line.get("table"):
            semantic_title = next(
                (
                    item["text"]
                    for item in reversed(recent_text)
                    if TABLE_NAME_SEMANTICS.search(item["text"])
                ),
                "",
            )
            source_title = next(
                (
                    item["text"]
                    for item in reversed(recent_text)
                    if item["source_type"] == "title"
                ),
                "",
            )
            titles[id(line)] = semantic_title or source_title
            continue
        text = str(line.get("text") or "").strip()
        if text:
            recent_text.append({"text": text, "source_type": line.get("source_type")})
    return titles


def _product_names(rows):
    names = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("PRODUCTNAME") or "").strip()
        key = name.lower()
        if name and key and key not in TOTAL_KEYS:
            names.append(name)
    return list(dict.fromkeys(names))


def _number(value):
    text = str(value or "").strip().replace("−", "-").replace("—", "-")
    match = NUMBER.fullmatch(text)
    if not match:
        return None
    try:
        amount = float(match.group(2).replace(",", ""))
    except ValueError:
        return None
    if match.group(1) and amount > 0:
        amount = -amount
    return amount


def _same_amount(left, right):
    if left is None or right is None:
        return False
    return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-6)


def _gt_facts(rows):
    facts = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("PRODUCTNAME") or "").strip()
        name_key = name.lower()
        amount = _number(row.get("MBREVENUE"))
        if not name or not name_key or name_key in TOTAL_KEYS or amount is None:
            continue
        facts.append({"name": name, "amount": amount})
    return facts


def _positions(rows):
    text_cells = []
    number_cells = []
    for row_index, row in enumerate(rows):
        if not isinstance(row, (list, tuple)):
            continue
        for column_index, cell in enumerate(row):
            key = str(cell or "").strip().lower()
            if key:
                text_cells.append((row_index, column_index, key))
            amount = _number(cell)
            if amount is not None:
                number_cells.append((row_index, column_index, amount))
    return text_cells, number_cells


def score_table(table, facts):
    rows = [list(row) for row in table.get("table", []) if isinstance(row, (list, tuple))]
    text_cells, number_cells = _positions(rows)
    name_hits = amount_hits = pair_hits = 0
    matched_facts = []
    for fact in facts:
        name_positions = []
        if historical_product_last_name_matches([fact["name"]], [table]):
            name_positions = [
                (row, column)
                for row, column, value in text_cells
                if historical_product_last_name_matches([fact["name"]], [{"table": [[value]]}])
            ]
        amount_positions = [
            (row, column)
            for row, column, value in number_cells
            if _same_amount(fact["amount"], value)
        ]
        name_hit = bool(name_positions)
        amount_hit = bool(amount_positions)
        pair_hit = any(
            name_row == amount_row or name_column == amount_column
            for name_row, name_column in name_positions
            for amount_row, amount_column in amount_positions
        )
        name_hits += name_hit
        amount_hits += amount_hit
        pair_hits += pair_hit
        if name_hit or amount_hit:
            matched_facts.append({
                "name": fact["name"],
                "amount": fact["amount"],
                "name_hit": name_hit,
                "amount_hit": amount_hit,
                "pair_hit": pair_hit,
            })
    return {
        "table": table,
        "score": (pair_hits, name_hits, amount_hits),
        "pair_hits": pair_hits,
        "name_hits": name_hits,
        "amount_hits": amount_hits,
        "fact_count": len(facts),
        "matched_facts": matched_facts,
    }


def resolve_document(info_code, pdf_json_root, current_rows, prior_rows):
    document_dir = pdf_json_root / info_code
    facts = _gt_facts(current_rows)
    if not document_dir.is_dir():
        return {"infocode": info_code, "status": "missing_json_dir"}
    lines = _load_lines(document_dir)
    lines_grouped = get_lines_grouped(lines)
    table_titles = _table_titles(lines)
    tables = [line for group in lines_grouped for line in group if line.get("is_table") and line.get("table")]
    section_titles = {
        id(line): group[0]["text"]
        for group in lines_grouped
        for line in group
        if line.get("is_table") and line.get("table")
    }
    for physical_index, table in enumerate(tables):
        table["id"] = f"p{table.get('page_number', 'x')}:{physical_index}"
        table["page"] = table.get("page_number")
    scored = [score_table(table, facts) for table in tables]
    best_score = max((item["score"] for item in scored), default=(0, 0, 0))
    resolved = bool(
        facts
        and (
            best_score[0] > 0
            or best_score[1] == len(facts)
            or (len(facts) >= 2 and best_score[2] == len(facts))
            or (best_score[1] > 0 and best_score[2] > 0)
        )
    )
    targets = [item for item in scored if resolved and item["score"] == best_score]

    selected_inner_lines, _related_inner_lines, _ = select_main_table(lines_grouped, _product_names(prior_rows))
    selected = next(
        (
            line for line in selected_inner_lines or ()
            if line.get("is_table") and line.get("table")
        ),
        None,
    )
    selected_is_target = bool(
        selected and any(item["table"]["id"] == selected["id"] for item in targets)
    )
    selected_score = next(
        (item["score"] for item in scored if selected and item["table"]["id"] == selected["id"]),
        (0, 0, 0),
    )
    return {
        "infocode": info_code,
        "status": "resolved" if resolved else "unresolved",
        "fact_count": len(facts),
        "table_count": len(tables),
        "best_score": list(best_score),
        "target_table_ids": [item["table"]["id"] for item in targets],
        "target_pages": sorted({item["table"]["page"] for item in targets}),
        "selected_table_id": selected["id"] if selected else "",
        "selected_page": selected["page"] if selected else None,
        "selected_score": list(selected_score),
        "selected_is_target": selected_is_target,
        "targets": [
            {
                "table_id": item["table"]["id"],
                "page": item["table"]["page"],
                "section_title": section_titles[id(item["table"])],
                "title": table_titles[id(item["table"])],
                "score": list(item["score"]),
                "matched_facts": item["matched_facts"],
            }
            for item in targets
        ],
    }


def _resolve_task(task):
    return resolve_document(*task)


def main():
    parser = argparse.ArgumentParser(description="Resolve HKCO target tables from GT facts")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--workers",
        type=int,
        default=min(8, os.cpu_count() or 4),
        help="并发进程数，默认最多 8；设为 1 可串行运行",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "analysis" / "HKCO_FN_PRODUCT" / "gt_target_tables.json"),
    )
    parser.add_argument(
        "--names-output",
        default=str(ROOT / "analysis" / "HKCO_FN_PRODUCT" / "gt_target_table_names.csv"),
        help="GT 目标物理表的近邻表名清单",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    task_dir = ROOT / "tasks" / "HKCO_FN_PRODUCT"
    current = json.loads((task_dir / "ground_truth.json").read_text(encoding="utf-8"))
    prior = json.loads((task_dir / "last_data.json").read_text(encoding="utf-8"))
    local_codes = sorted(path.name for path in (ROOT / "pdf_json").iterdir() if path.is_dir())
    items = [(code, current[code]) for code in local_codes if code in current][:args.limit or None]
    tasks = [
        (code, ROOT / "pdf_json", rows, prior.get(code, []))
        for code, rows in items
    ]
    if args.workers == 1:
        documents = [_resolve_task(task) for task in tasks]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            documents = list(executor.map(_resolve_task, tasks, chunksize=8))
    statuses = Counter(item["status"] for item in documents)
    resolved = [item for item in documents if item["status"] == "resolved"]
    summary = {
        "documents": len(documents),
        "statuses": dict(statuses),
        "resolved_documents": len(resolved),
        "selected_target_table": sum(item["selected_is_target"] for item in resolved),
        "selected_target_table_rate": round(
            sum(item["selected_is_target"] for item in resolved) / len(resolved), 4
        ) if resolved else 0.0,
        "multiple_valid_targets": sum(len(item["target_table_ids"]) > 1 for item in resolved),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps({"summary": summary, "documents": documents}, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    title_counts = Counter(
        target["title"].strip()
        for document in resolved
        for target in document["targets"]
        if target["title"].strip()
    )
    section_title_counts = Counter(
        target["section_title"].strip()
        for document in resolved
        for target in document["targets"]
        if target["section_title"].strip()
    )
    names_output = Path(args.names_output)
    names_output.parent.mkdir(parents=True, exist_ok=True)
    with names_output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["name_type", "table_name", "target_count"])
        for name, count in sorted(title_counts.items(), key=lambda item: (-item[1], item[0])):
            writer.writerow(["nearest_title", name, count])
        for name, count in sorted(section_title_counts.items(), key=lambda item: (-item[1], item[0])):
            writer.writerow(["section_title", name, count])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(output)
    print(names_output)


if __name__ == "__main__":
    main()
