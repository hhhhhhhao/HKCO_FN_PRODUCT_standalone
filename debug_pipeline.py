# -*- coding: utf-8 -*-
"""逐阶段打印单篇 HKCO_FN_PRODUCT 处理结果，供人工和 AI 定位问题。"""
import argparse
import json
import re
from pathlib import Path

from custom.service.EAPS_HKCO_FN_PRODUCT import parse_mineru_result_to_lines, _prior_context
from custom.service.EAPS_HKCO_FN_PRODUCT_format_data import format_records
from custom.service.HKCO_FN_PRODUCT_classifier import classify_main_inner
from custom.service.HKCO_FN_PRODUCT_document import get_document_period_text, split_into_sections
from custom.service.HKCO_FN_PRODUCT_extraction import extract_main_table
from custom.service.HKCO_FN_PRODUCT_metric_enrichment import enrich_metrics
from custom.service.HKCO_FN_PRODUCT_selector import select_main_table


ROOT = Path(__file__).resolve().parent


def _page(path):
    match = re.search(r"_(\d+)\.json$", path.name)
    return int(match.group(1)) if match else 10**9


def _lines(info_code):
    result = []
    for path in sorted((ROOT / "pdf_json" / info_code).glob("*.json"), key=_page):
        if "over" in path.name.lower():
            continue
        result.extend(parse_mineru_result_to_lines(
            json.loads(path.read_text(encoding="utf-8-sig")), _page(path)
        ))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("info_code")
    args = parser.parse_args()
    lines = _lines(args.info_code)
    prior_path = ROOT / "tasks" / "HKCO_FN_PRODUCT" / "last_data.json"
    prior = json.loads(prior_path.read_text(encoding="utf-8")).get(args.info_code, [])
    sections = split_into_sections(lines)
    context = _prior_context(prior, get_document_period_text(lines))
    main_table, selection = select_main_table(sections, context["prior_product_names"])
    classify_main_inner(main_table, context["prior_product_names"])
    extraction = extract_main_table(main_table, context)
    metric_facts, metric_debug = enrich_metrics(
        sections, main_table, extraction["facts"], context["required_metrics"]
    )
    output = {
        "document": {
            "line_count": len(lines),
            "section_count": len(sections),
            "table_count": sum(len(section["tables"]) for section in sections),
        },
        "sections": [
            {"index": section["index"], "title": section["title"],
             "table_ids": [table["id"] for table in section["tables"]]}
            for section in sections
        ],
        "selection": [
            {key: value for key, value in item.items() if key != "table"}
            | {"table_id": item["table"]["id"], "section_title": item["table"]["section_title"]}
            for item in selection
        ],
        "selected_table": main_table,
        "extraction": extraction,
        "metric_debug": metric_debug,
        "records": format_records(extraction["facts"], metric_facts),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
