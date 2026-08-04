# -*- coding: utf-8 -*-
"""Convert OCR lines into neutral document evidence.

This module deliberately does not locate or select a revenue table.  It only
enumerates the independent tables in the current announcement and supplies
announcement-level period text.  Revenue-plan discovery belongs to the
classified pipeline.
"""
import re


SEMANTIC_TITLE = re.compile(
    r"收入|收益|營業額|营业额|銷售額|销售额|產品|产品|商品|貨品|货品|"
    r"服務|服务|業務|业务|分部|地區|地区|成本|毛利|利潤|利润|損益|损益|"
    r"財務|财务|業績|业绩|revenue|turnover|sales|product|service|segment|cost|profit",
    re.I,
)
DETAIL_TITLE = re.compile(r"分析|明細|明细|分拆|細分|细分|分類|分类|劃分|划分|構成|构成|資料|资料", re.I)
MEASUREMENT_BANNER = re.compile(
    r"截至|止年度|止期間|止期间|20\d{2}|二零|二〇|"
    r"人民幣|人民币|港元|美元|歐元|欧元|日圓|日元|"
    r"百萬|百万|千元|千港元|unit|expressed in",
    re.I,
)


def _semantic_title(candidates):
    """Prefer a nearby semantic heading over a period/unit banner."""
    if not candidates:
        return ""
    scored = []
    for distance, text in enumerate(candidates):
        semantic = bool(SEMANTIC_TITLE.search(text))
        score = (6 if semantic else 0) + (2 if DETAIL_TITLE.search(text) else 0)
        if MEASUREMENT_BANNER.search(text) and not semantic:
            score -= 5
        if re.fullmatch(r"[\d\s年月日截至止期間期间度()（）:：,，.\-–—]+", text):
            score -= 5
        if len(text) > 200:
            score -= 4
        if len(text) > 400:
            score -= 4
        score -= min(distance, 12) * 0.1
        scored.append((score, -distance, text))
    best = max(scored)
    return best[2] if best[0] > 0 else candidates[0]


def get_all_source_tables(lines):
    """Return every distinct OCR table without granting it a semantic role."""
    items = []
    seen = set()
    all_lines = lines or []
    for index, line in enumerate(all_lines):
        table = line.get("table") if isinstance(line, dict) else None
        if not table:
            continue
        page = line.get("page_number")
        candidates = []
        for previous in reversed(all_lines[max(0, index - 16):index]):
            if not isinstance(previous, dict) or previous.get("page_number") != page:
                continue
            if previous.get("is_table"):
                continue
            text = str(previous.get("text") or "").strip()
            if text and not re.fullmatch(r"\d+", text):
                candidates.append(text[:500])
        title = _semantic_title(candidates)
        measurement_context = " ".join(
            text for text in candidates[:8] if MEASUREMENT_BANNER.search(text)
        )
        signature = repr(table)
        if signature in seen:
            continue
        seen.add(signature)
        items.append({
            "title": title,
            "measurement_context": measurement_context,
            "page_number": page,
            "target_table": table,
            "page_lines": [line],
            "source_role": "document_table",
        })
    return items


def get_document_period_text(lines):
    """Collect annual-period statements for tables whose headers omit dates."""
    snippets = []
    for line in lines or []:
        if not isinstance(line, dict) or line.get("is_table"):
            continue
        text = str(line.get("text") or "").strip()
        if text and re.search(
            r"止(?:財政|财政)?年度|年度(?:業績|业绩)|"
            r"financial\s+year\s+ended|year\s+ended",
            text,
            re.I,
        ):
            snippets.append(text[:500])
    return " ".join(snippets[:120])
