# -*- coding: utf-8 -*-
"""诊断 HKCO_FN_PRODUCT 错误选表附近的标题切章证据。

GT 只用于跑后确定目标物理表；本脚本不会参与正式选表，也不会修改业务规则。
"""
from __future__ import annotations

import argparse
import json
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


_SEMANTIC_LOOKAHEAD = (
    r"(?=.*(?:收入|收益|營業額|营业额|銷售額|销售额|產品|产品|商品|貨品|货品|"
    r"服務|服务|業務|业务|分部|地區|地区|成本|毛利|利潤|利润|損益|损益|"
    r"財務|财务|業績|业绩|revenue|turnover|sales|product|service|segment|cost|profit))"
)
CANDIDATE_TITLE_PATTERNS = {
    "semantic_explicit_table_ending": re.compile(
        rf"^{_SEMANTIC_LOOKAHEAD}.{{1,500}}(?:如下|如下所示|呈列如下|列示如下|"
        r"載列如下|载列如下|載於下表|载于下表|於下表|于下表)\s*[:：]?$",
        re.I,
    ),
    "semantic_table_lead": re.compile(
        rf"^{_SEMANTIC_LOOKAHEAD}(?:下表|以下|下文|上表).{{1,500}}[:：]?\s*$",
        re.I,
    ),
    "short_semantic_colon": re.compile(
        rf"^{_SEMANTIC_LOOKAHEAD}.{{1,80}}[:：]\s*$",
        re.I,
    ),
}
SEMANTIC_TEXT = re.compile(
    r"收入|收益|營業額|营业额|銷售額|销售额|產品|产品|商品|貨品|货品|"
    r"服務|服务|業務|业务|分部|地區|地区|成本|毛利|利潤|利润|損益|损益|"
    r"財務|财务|業績|业绩|revenue|turnover|sales|product|service|segment|cost|profit",
    re.I,
)
MEASUREMENT_TEXT = re.compile(r"截至|止年度|止期间|20\d{2}|人民币|港元|美元|欧元|日元|百万元|千元|单位", re.I)


def _page_number(path: Path) -> int:
    match = re.search(r"_(\d+)\.json$", path.name)
    return int(match.group(1)) if match else 10**9


def _load_lines(info_code: str):
    lines = []
    for path in sorted((ROOT / "pdf_json" / info_code).glob("*.json"), key=_page_number):
        if "over" in path.name.lower():
            continue
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        lines.extend(parse_mineru_result_to_lines(payload, _page_number(path)))
    return lines


def _line_profile(line, title_line_ids):
    text = str(line.get("text") or "").strip()
    source_type = str(line.get("source_type") or "").lower()
    return {
        "page": line.get("page_number"),
        "source_type": source_type,
        "text": text[:500],
        "is_title_line": id(line) in title_line_ids,
        "semantic_title": bool(SEMANTIC_TEXT.search(text)),
        "measurement_text": bool(MEASUREMENT_TEXT.search(text)),
        "ends_with_colon": text.endswith((":", "：")),
        "length": len(text),
    }


def _table_index(table_id: str):
    match = re.fullmatch(r"p(?:\d+|x):(\d+)", str(table_id or ""))
    return int(match.group(1)) if match else None


def _table_contexts(lines, wanted_ids):
    wanted_indexes = {
        index for table_id in wanted_ids if (index := _table_index(table_id)) is not None
    }
    contexts = {}
    title_line_ids = {id(group[0]) for group in get_lines_grouped(lines) if group}
    physical_index = -1
    for line_index, line in enumerate(lines):
        if not isinstance(line, dict) or not line.get("is_table"):
            continue
        physical_index += 1
        if physical_index not in wanted_indexes:
            continue
        page = line.get("page_number")
        preceding = []
        for previous in reversed(lines[max(0, line_index - 20):line_index]):
            if not isinstance(previous, dict) or previous.get("is_table"):
                continue
            if previous.get("page_number") != page:
                continue
            text = str(previous.get("text") or "").strip()
            if text:
                preceding.append(_line_profile(previous, title_line_ids))
            if len(preceding) >= 12:
                break
        table_id = next(
            table_id for table_id in wanted_ids if _table_index(table_id) == physical_index
        )
        contexts[table_id] = {
            "page": page,
            "preceding_lines_nearest_first": preceding,
        }
    return contexts


def _analyze_document(document):
    info_code = document["infocode"]
    wanted_ids = list(dict.fromkeys(
        [document.get("selected_table_id", "")] + list(document.get("target_table_ids") or [])
    ))
    contexts = _table_contexts(_load_lines(info_code), wanted_ids)
    return {
        "infocode": info_code,
        "selected_table_id": document.get("selected_table_id", ""),
        "target_table_ids": document.get("target_table_ids") or [],
        "tables": contexts,
    }


def _candidate_kind(line):
    if line["is_title_line"]:
        return "recognized_title"
    if line["source_type"] == "table_caption":
        return "unrecognized_table_caption"
    if line["semantic_title"] and line["ends_with_colon"]:
        return "unrecognized_semantic_colon"
    if line["semantic_title"] and line["length"] <= 80:
        return "unrecognized_short_semantic"
    if line["ends_with_colon"] and line["length"] <= 120:
        return "unrecognized_short_colon"
    return "other_text"


def main():
    parser = argparse.ArgumentParser(description="Analyze HKCO title split errors")
    parser.add_argument(
        "--evaluation",
        default=str(ROOT / ".codex_tmp" / "gt_target_tables_after_caption_revert.json"),
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / ".codex_tmp" / "hkco_title_split_error_analysis.json"),
    )
    parser.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 4))
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    evaluation = json.loads(Path(args.evaluation).read_text(encoding="utf-8"))
    misses = [
        document
        for document in evaluation.get("documents", [])
        if document.get("status") == "resolved" and not document.get("selected_is_target")
    ]
    if args.workers == 1:
        documents = [_analyze_document(document) for document in misses]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            documents = list(executor.map(_analyze_document, misses, chunksize=8))

    selected_kinds = Counter()
    target_kinds = Counter()
    selected_candidate_patterns = Counter()
    target_candidate_patterns = Counter()
    target_examples = []
    for document in documents:
        selected_id = document["selected_table_id"]
        for table_id, table in document["tables"].items():
            nearest = table["preceding_lines_nearest_first"][:1]
            kind = _candidate_kind(nearest[0]) if nearest else "no_same_page_text"
            if table_id == selected_id:
                selected_kinds[kind] += 1
                if nearest and not nearest[0]["is_title_line"]:
                    for name, pattern in CANDIDATE_TITLE_PATTERNS.items():
                        if pattern.fullmatch(nearest[0]["text"]):
                            selected_candidate_patterns[name] += 1
            if table_id in document["target_table_ids"]:
                target_kinds[kind] += 1
                if nearest and not nearest[0]["is_title_line"]:
                    for name, pattern in CANDIDATE_TITLE_PATTERNS.items():
                        if pattern.fullmatch(nearest[0]["text"]):
                            target_candidate_patterns[name] += 1
                if kind.startswith("unrecognized_") and len(target_examples) < 100:
                    target_examples.append({
                        "infocode": document["infocode"],
                        "table_id": table_id,
                        "kind": kind,
                        "nearest_line": nearest[0],
                    })

    result = {
        "summary": {
            "misselected_documents": len(documents),
            "selected_nearest_line_kinds": dict(selected_kinds),
            "target_nearest_line_kinds": dict(target_kinds),
            "selected_candidate_pattern_hits": dict(selected_candidate_patterns),
            "target_candidate_pattern_hits": dict(target_candidate_patterns),
            "unrecognized_target_examples": len(target_examples),
        },
        "unrecognized_target_examples": target_examples,
        "documents": documents,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(output)


if __name__ == "__main__":
    main()
