#!/usr/bin/env python3
"""Screen every OCR table, then classify only retained revenue shapes.

The first layer is deliberately binary: irrelevant tables are rejected and no
more taxonomy work is spent on them.  The second layer assigns structural
classes only to retained revenue/explicit-metric tables.  Ground truth is never
loaded.  Optional errors are joined only after memberships are fixed.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom.service.EAPS_HKCO_FN_PRODUCT import parse_mineru_result_to_lines
from custom.service.EAPS_HKCO_FN_PRODUCT_get_res import _prior_fiscal_month_day
from custom.service.HKCO_FN_PRODUCT_classifier import TableClassifier
from custom.service.HKCO_FN_PRODUCT_document import (
    get_all_source_tables,
    get_document_period_text,
)
from custom.service.HKCO_FN_PRODUCT_evidence import (
    TableEvidenceScanner,
    table_refs_from_sources,
)


ERROR_FIELDS = ("missing", "extra", "value_diff")
PAGE_NUMBER = re.compile(r"_(\d+)\.json$", re.I)
CONTINUATION = re.compile(r"[（(]?續[）)]?|[（(]?续[）)]?|continued", re.I)
FINANCIAL_STATEMENT = re.compile(
    r"(?:綜合|综合|合併|合并|簡明|简明).*(?:收益表|損益表|损益表)|"
    r"(?:收益表|損益表|损益表|income statement|statement of profit)", re.I,
)
NON_PRODUCT_INCOME = re.compile(
    r"營業外收入|营业外收入|利息收入|股息收入|交易(?:淨|净)?收益|"
    r"金融投資(?:淨|净)?收益|投資收益|投资收益|每股收益|每股盈利|"
    r"investment income|interest income|dividend income|earnings per share",
    re.I,
)
GEOGRAPHIC_TITLE = re.compile(
    r"地區|地区|地域|地理|省份|城市|國家|国家|"
    r"(?:客戶|客户).{0,8}(?:位置|所在地|地點|地点)|"
    r"(?:交付|送達|送达).{0,8}(?:地點|地点|位置)|geograph|customer location|place of delivery",
    re.I,
)
RECONCILIATION = re.compile(r"對賬|对账|調節|调节|reconcil", re.I)
CONTRACT_BALANCE = re.compile(r"合約負債|合同负债|履約義務|履约义务|contract liabilit", re.I)
CUSTOMER_CONCENTRATION = re.compile(
    r"(?:主要|重大|單一|单一).*(?:客戶|客户)|(?:客戶|客户).*(?:10|十)\s*[%％]|major customer",
    re.I,
)
DETAIL_TITLE = re.compile(
    r"產品|产品|商品|貨品|货品|服務|服务|業務|业务|"
    r"明細|明细|分析|分拆|細分|细分|分類|分类|劃分|划分|構成|构成",
    re.I,
)
REVENUE_ROLES = {
    "primary_revenue_detail", "segment_revenue", "generic_revenue",
    "geography_revenue", "revenue_with_metrics", "product_service_breakdown",
}
METRIC_ROLES = {"explicit_cost", "explicit_gross_profit", "revenue_with_metrics"}


def _prior_names(rows):
    return [
        str(row.get("PRODUCTNAME") or "").strip()
        for row in rows or []
        if isinstance(row, dict)
        and str(row.get("PRODUCTNAME") or "").strip() not in {"", "合计", "合計"}
    ]


def _load_document_lines(document_dir):
    lines = []
    page_files = []
    for path in document_dir.glob("*.json"):
        match = PAGE_NUMBER.search(path.name)
        if match and "over" not in path.name.lower():
            page_files.append((int(match.group(1)), path))
    for page, path in sorted(page_files):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(payload, list):
                lines.extend(parse_mineru_result_to_lines(payload, page))
        except (OSError, ValueError, TypeError):
            continue
    return lines


def _role(classification):
    title = classification.evidence.title_signals
    layout = classification.evidence.layout_signals
    raw_title = classification.evidence.table.title
    revenue = ("revenue" in title or "revenue_metric_row" in layout
               or "revenue_metric_column" in layout
               or "embedded_revenue_heading" in layout)
    metric = "cost" in title or "gross_profit" in title
    if FINANCIAL_STATEMENT.search(raw_title):
        return "financial_statement"
    if CONTRACT_BALANCE.search(raw_title):
        return "contract_balance_revenue"
    if RECONCILIATION.search(raw_title):
        return "revenue_reconciliation"
    if CUSTOMER_CONCENTRATION.search(raw_title):
        return "customer_concentration"
    if NON_PRODUCT_INCOME.search(raw_title):
        return "non_product_income_metric"
    if "other_income" in title and "primary_with_other_income" not in title:
        return "other_income"
    if revenue and metric:
        return "revenue_with_metrics"
    if "cost" in title:
        return "explicit_cost"
    if "gross_profit" in title:
        return "explicit_gross_profit"
    if revenue:
        if GEOGRAPHIC_TITLE.search(raw_title):
            return "geography_revenue"
        if re.search(r"分部|segment", raw_title, re.I):
            return "segment_revenue"
        if DETAIL_TITLE.search(raw_title):
            return "primary_revenue_detail"
        return "generic_revenue"
    if "product_service_breakdown" in title:
        return "product_service_breakdown"
    if "expense" in title:
        return "expense_disclosure"
    return "neutral"


def _screen(role, classification):
    """Binary gate; excluded tables receive no finer extractor taxonomy.

    Corpus rule: classification exists to avoid parsing every table.  Only
    current-announcement revenue shapes and explicit cost/gross-profit facts
    survive.  Prior identity continuity may rank survivors later, but cannot
    make a table survive this gate by itself.
    """
    if role in METRIC_ROLES:
        return "retain_explicit_metric"
    if role in REVENUE_ROLES:
        return ("retain_primary_candidate" if classification.supported
                else "retain_revenue_shape_rejected")
    return "exclude"


def _period_layout(evidence):
    if "repeated_period_columns" in evidence.layout_signals:
        return "repeated_period_columns"
    if len(evidence.period_tokens) > 1:
        return "multi_period"
    if evidence.period_tokens:
        return "single_period"
    return "period_unstated"


def _measurement(evidence):
    currency = bool(evidence.currency_tokens)
    unit = bool(evidence.unit_tokens)
    if currency and unit:
        return "currency_and_unit"
    if currency:
        return "currency_only"
    if unit:
        return "unit_only"
    return "measurement_unstated"


def _prior_alignment(evidence, has_prior):
    if not has_prior:
        return "no_prior"
    if not evidence.prior_identity_hits:
        return "prior_no_hit"
    coverage = evidence.prior_identity_coverage
    band = "full" if coverage >= 0.999 else "high" if coverage >= 0.75 else "partial"
    return f"prior_{evidence.prior_axis or 'mixed'}_{band}"


def _title_family(value):
    text = CONTINUATION.sub("", str(value or "").lower())
    text = re.sub(r"20\d{2}|二零[〇零一二三四五六七八九]{2}", "", text)
    return re.sub(r"[\s:：,，。;；()（）\[\]【】\d.、\-–—]+", "", text)


def _table_profile(classification, has_prior):
    evidence = classification.evidence
    role = _role(classification)
    geometry = classification.table_type
    profile = {
        "table_id": evidence.table.table_id,
        "page": evidence.table.page,
        "title": evidence.table.title,
        "title_family": _title_family(evidence.table.title),
        "role": role,
        "screen": _screen(role, classification),
        "geometry": geometry,
        "semantic_axis": classification.semantic_axis,
        "revenue_basis": classification.revenue_basis,
        "period_layout": _period_layout(evidence),
        "measurement": _measurement(evidence),
        "prior_alignment": _prior_alignment(evidence, has_prior),
        "supported_as_primary_revenue": classification.supported,
        "classification_reasons": list(classification.reasons),
        "title_signals": sorted(evidence.title_signals),
        "layout_signals": sorted(evidence.layout_signals),
        "axis_signals": sorted(evidence.axis_signals),
        "row_count": evidence.row_count,
        "column_count": evidence.column_count,
    }
    profile["coarse_class"] = ("exclude" if profile["screen"] == "exclude"
                               else "|".join((profile["screen"], role, geometry)))
    profile["revenue_structure_class"] = "|".join((
        role,
        geometry,
        classification.semantic_axis,
        "eligible" if classification.supported else "rejected",
    ))
    profile["fine_class"] = "|".join((
        profile["revenue_structure_class"], profile["period_layout"]
    ))
    return profile


def _mark_table_relations(tables):
    families = defaultdict(list)
    for table in tables:
        if table["title_family"]:
            families[(table["title_family"], table["semantic_axis"], table["revenue_basis"])].append(table)
    for table in tables:
        table["table_relation"] = "standalone"
    for members in families.values():
        if len(members) < 2:
            continue
        periods = {member["period_layout"] for member in members}
        relation = (
            "continuation_family"
            if any(CONTINUATION.search(member["title"]) for member in members)
            else "period_sibling_family"
            if len(periods) > 1 or "multi_period" in periods
            else "same_title_family"
        )
        for member in members:
            member["table_relation"] = relation


def _document_class(tables):
    shaped = [
        table for table in tables
        if table["role"] in REVENUE_ROLES
    ]
    revenue = [table for table in shaped if table["supported_as_primary_revenue"]]
    if not shaped:
        return "no_revenue_shaped_table"
    if not revenue:
        return "revenue_shaped_but_all_rejected"
    axes = sorted({table["semantic_axis"] for table in revenue if table["semantic_axis"] != "unknown"})
    if not axes:
        axes = ["unknown"]
    geometries = sorted({table["geometry"] for table in revenue})
    relations = sorted({table["table_relation"] for table in revenue
                        if table["table_relation"] != "standalone"})
    if len(axes) > 1:
        return "multi_axis|" + "+".join(axes)
    if relations:
        return relations[0] + "|" + axes[0]
    if len(revenue) == 1:
        return "single|" + geometries[0] + "|" + axes[0]
    if len(geometries) == 1:
        return "multiple_same_geometry|" + geometries[0] + "|" + axes[0]
    return "multiple_mixed_geometry|" + axes[0]


def classify_corpus(pdf_json_dir, prior_data):
    scanner, classifier = TableEvidenceScanner(), TableClassifier()
    documents = []
    for document_dir in sorted(path for path in pdf_json_dir.iterdir() if path.is_dir()):
        code = document_dir.name
        lines = _load_document_lines(document_dir)
        sources = get_all_source_tables(lines)
        prior_rows = prior_data.get(code) or []
        evidence = scanner.scan(
            table_refs_from_sources(sources),
            get_document_period_text(lines),
            _prior_names(prior_rows),
            _prior_fiscal_month_day(prior_rows),
        )
        tables = [_table_profile(item, bool(prior_rows)) for item in classifier.classify(evidence)]
        _mark_table_relations(tables)
        documents.append({
            "infocode": code,
            "document_class": _document_class(tables),
            "table_count": len(tables),
            "tables": tables,
        })
    return documents


def _load_errors(path):
    if not path:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        row["infocode"]: {
            field: int((row.get("stats") or {}).get(field) or 0) for field in ERROR_FIELDS
        }
        for row in payload.get("rows", []) if row.get("infocode")
    }


def _summaries(documents, errors, sample_size):
    table_groups, revenue_groups, document_groups = defaultdict(list), defaultdict(list), defaultdict(list)
    screen_groups = defaultdict(list)
    for document in documents:
        document_groups[document["document_class"]].append(document)
        for table in document["tables"]:
            screen_groups[table["screen"]].append((document, table))
            table_groups[table["coarse_class"]].append((document, table))
            if table["role"] in REVENUE_ROLES:
                revenue_groups[table["revenue_structure_class"]].append((document, table))

    table_classes = []
    for name, members in table_groups.items():
        table_classes.append({
            "class": name,
            "table_count": len(members),
            "doc_count": len({document["infocode"] for document, _ in members}),
            "supported_primary_count": sum(table["supported_as_primary_revenue"] for _, table in members),
            "relations": dict(Counter(table["table_relation"] for _, table in members)),
            "measurements": dict(Counter(table["measurement"] for _, table in members)),
            "prior_alignment": dict(Counter(table["prior_alignment"] for _, table in members)),
            "sample_infocodes": list(dict.fromkeys(
                document["infocode"] for document, _ in members
            ))[:sample_size],
        })
    table_classes.sort(key=lambda item: (-item["table_count"], -item["doc_count"], item["class"]))

    revenue_table_classes = []
    for name, members in revenue_groups.items():
        revenue_table_classes.append({
            "class": name,
            "table_count": len(members),
            "doc_count": len({document["infocode"] for document, _ in members}),
            "period_layouts": dict(Counter(table["period_layout"] for _, table in members)),
            "relations": dict(Counter(table["table_relation"] for _, table in members)),
            "rejection_reasons": dict(Counter(
                reason for _, table in members for reason in table["classification_reasons"]
            )),
            "sample_infocodes": list(dict.fromkeys(
                document["infocode"] for document, _ in members
            ))[:sample_size],
        })
    revenue_table_classes.sort(key=lambda item: (
        -item["table_count"], -item["doc_count"], item["class"]
    ))

    document_classes = []
    for name, members in document_groups.items():
        aggregate = Counter()
        for document in members:
            aggregate.update(errors.get(document["infocode"], {}))
        ranked = sorted(members, key=lambda document: (
            -sum(errors.get(document["infocode"], {}).values()), document["infocode"]
        ))
        document_classes.append({
            "class": name,
            "doc_count": len(members),
            "missing": aggregate["missing"],
            "extra": aggregate["extra"],
            "value_diff": aggregate["value_diff"],
            "total_errors": sum(aggregate.values()),
            "sample_infocodes": [document["infocode"] for document in ranked[:sample_size]],
        })
    document_classes.sort(key=lambda item: (
        -item["total_errors"] if errors else -item["doc_count"],
        -item["doc_count"], item["class"],
    ))
    screen_summary = []
    for name, members in screen_groups.items():
        screen_summary.append({
            "class": name,
            "table_count": len(members),
            "doc_count": len({document["infocode"] for document, _ in members}),
            "roles": dict(Counter(table["role"] for _, table in members)),
        })
    screen_summary.sort(key=lambda item: (-item["table_count"], item["class"]))
    return screen_summary, table_classes, revenue_table_classes, document_classes


def _markdown(payload):
    lines = [
        "# HKCO_FN_PRODUCT 粗分类",
        "",
        f"公告数：{payload['doc_count']}；表格数：{payload['table_count']}。",
        "",
        "分类完全基于公告表格结构；错误数据如有，仅在分类完成后聚合。",
        "",
        "## 第一层：直接排除或保留",
        "",
        "| 决策 | 表格数 | 公告数 |",
        "|---|---:|---:|",
    ]
    for item in payload["screen_summary"]:
        lines.append(f"| {item['class']} | {item['table_count']} | {item['doc_count']} |")
    lines.extend((
        "", "## 第二层：仅保留表的结构类", "",
        "",
        "| 排名 | 表格数 | 公告数 | 可作主收入候选 | 类别 |",
        "|---:|---:|---:|---:|---|",
    ))
    retained = [item for item in payload["table_classes"] if item["class"] != "exclude"]
    for index, item in enumerate(retained[:40], 1):
        lines.append(
            f"| {index} | {item['table_count']} | {item['doc_count']} | "
            f"{item['supported_primary_count']} | {item['class'].replace('|', ' / ')} |"
        )
    lines.extend((
        "", "## 收入结构类", "",
        "| 排名 | 表格数 | 公告数 | 类别 |",
        "|---:|---:|---:|---|",
    ))
    for index, item in enumerate(payload["revenue_table_classes"], 1):
        lines.append(
            f"| {index} | {item['table_count']} | {item['doc_count']} | "
            f"{item['class'].replace('|', ' / ')} |"
        )
    lines.extend((
        "", "## 公告收入计划类别", "",
        "| 排名 | 公告数 | Missing | Extra | Value diff | 总错误 | 类别 |",
        "|---:|---:|---:|---:|---:|---:|---|",
    ))
    for index, item in enumerate(payload["document_classes"], 1):
        lines.append(
            f"| {index} | {item['doc_count']} | {item['missing']} | {item['extra']} | "
            f"{item['value_diff']} | {item['total_errors']} | {item['class'].replace('|', ' / ')} |"
        )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf-json", type=Path, default=Path("pdf_json"))
    parser.add_argument("--last-data", type=Path, default=Path("tasks/HKCO_FN_PRODUCT/last_data.json"))
    parser.add_argument("--per-doc", type=Path, help="optional analysis-only metrics/per_doc.json")
    parser.add_argument("--sample-size", type=int, default=50)
    parser.add_argument("--out", type=Path, default=Path("analysis/HKCO_FN_PRODUCT/coarse_table_classes"))
    args = parser.parse_args()

    prior_data = json.loads(args.last_data.read_text(encoding="utf-8"))
    documents = classify_corpus(args.pdf_json, prior_data)
    errors = _load_errors(args.per_doc)
    screen_summary, table_classes, revenue_table_classes, document_classes = _summaries(
        documents, errors, max(1, args.sample_size)
    )
    payload = {
        "pdf_json": str(args.pdf_json),
        "per_doc_analysis_only": str(args.per_doc) if args.per_doc else "",
        "doc_count": len(documents),
        "table_count": sum(document["table_count"] for document in documents),
        "screen_summary": screen_summary,
        "table_classes": table_classes,
        "revenue_table_classes": revenue_table_classes,
        "document_classes": document_classes,
        "documents": documents,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.out.with_suffix(".md").write_text(_markdown(payload), encoding="utf-8")
    print(args.out.with_suffix(".json"))
    print(args.out.with_suffix(".md"))


if __name__ == "__main__":
    main()
