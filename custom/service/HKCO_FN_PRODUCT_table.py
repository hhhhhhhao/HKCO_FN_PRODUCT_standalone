# -*- coding: utf-8 -*-
"""通用表格组装：把选中的章节行还原成物理表二维数组。"""
from __future__ import annotations

import re

from custom.extend.pdfplumber_extend_object import ExtendPlumber
from custom.service.HKCO_FN_PRODUCT_utils import fullwidth_to_halfwidth, is_number


_ROW_TOLERANCE = 4
_ANCHOR_TOLERANCE = 8
_BLOCK_GAP = 45
_NARRATIVE_LEN = 40
_SENTENCE_PUNCT = re.compile(r"[。；：，]")


def assemble_tables(pdf_path, inner_lines):
    """把 inner_lines 所在的章节组装成物理表对象列表。"""
    if not inner_lines:
        return []

    tables = []
    with ExtendPlumber.open(pdf_path) as pdf:
        lines_by_page = {}
        for line in inner_lines:
            page_number = line.get("page_number")
            if page_number is None:
                continue
            lines_by_page.setdefault(page_number, []).append(line)

        for page_number in sorted(lines_by_page):
            if page_number < 1 or page_number > len(pdf.pages):
                continue
            page_lines = lines_by_page[page_number]
            page = pdf.pages[page_number - 1]
            blocks = _split_page_blocks(page, page_lines)

            for block in blocks:
                grid = _assemble_grid(block["rows"])
                table_text = " ".join(
                    str(line.get("text") or "") for line in page_lines
                )
                numeric_word_count = sum(
                    is_number(word["text"])
                    for row in block["rows"]
                    for word in row
                )
                tables.append({
                    "id": f"p{page_number}:{len(tables)}",
                    "is_table": True,
                    "table": grid,
                    "page_number": page_number,
                    "lines": page_lines,
                    "words": [word for row in block["rows"] for word in row],
                    "bbox": block["bbox"],
                    "text": table_text,
                    "assembly_debug": {
                        "block_row_count": len(block["rows"]),
                        "word_count": sum(len(row) for row in block["rows"]),
                        "numeric_word_count": numeric_word_count,
                        "grid_shape": [
                            len(grid),
                            max((len(row) for row in grid), default=0),
                        ],
                        "bbox": [round(value, 2) for value in block["bbox"]],
                    },
                })
    return tables


def _split_page_blocks(page, page_lines):
    """按数字行密度把一页里的章节文字切成候选表块。"""
    bbox = (
        min(line["x0"] for line in page_lines),
        min(line["top"] for line in page_lines),
        max(line["x1"] for line in page_lines),
        max(line["bottom"] for line in page_lines),
    )
    words = []
    for word in page.extract_words():
        if (
            word["x0"] >= bbox[0] - 1
            and word["x1"] <= bbox[2] + 1
            and word["top"] >= bbox[1] - 1
            and word["bottom"] <= bbox[3] + 1
        ):
            normalized = dict(word)
            normalized["text"] = fullwidth_to_halfwidth(normalized.get("text", ""))
            words.append(normalized)
    if not words:
        return []

    rows = [
        row for row in _group_words_to_rows(words)
        if not _is_page_number_row(row)
    ]
    blocks = []
    current = []
    pending = []
    has_numeric = False

    for row in rows:
        numeric = any(is_number(word["text"]) for word in row)
        if numeric:
            if not current and pending:
                usable = []
                for candidate in reversed(pending):
                    if _looks_narrative(candidate):
                        break
                    if usable and _row_gap(candidate, usable[0]) > _BLOCK_GAP:
                        break
                    usable.insert(0, candidate)
                current = usable
            current.append(row)
            has_numeric = True
            pending = []
        else:
            if has_numeric:
                if (
                    _row_gap(current[-1], row) <= _BLOCK_GAP
                    and not _looks_narrative(row)
                ):
                    current.append(row)
                else:
                    if current:
                        blocks.append(current)
                    current = []
                    has_numeric = False
                    pending = [row]
            else:
                pending.append(row)
                if len(pending) > 6:
                    pending.pop(0)

    if current:
        blocks.append(current)

    return [
        {
            "rows": block_rows,
            "bbox": bbox,
        }
        for block_rows in blocks
        if any(is_number(word["text"]) for row in block_rows for word in row)
    ]


def _group_words_to_rows(words):
    words = sorted(words, key=lambda word: (word["top"], word["x0"]))
    rows = []
    current = []
    current_top = None

    for word in words:
        if current_top is None or abs(word["top"] - current_top) <= _ROW_TOLERANCE:
            current.append(word)
            current_top = word["top"]
        else:
            rows.append(sorted(current, key=lambda item: item["x0"]))
            current = [word]
            current_top = word["top"]
    if current:
        rows.append(sorted(current, key=lambda item: item["x0"]))
    return rows


def _row_gap(left, right):
    if not left or not right:
        return 0
    return max(0.0, right[0]["top"] - left[-1]["bottom"])


def _looks_narrative(row):
    text = "".join(word["text"] for word in row)
    if len(text) > _NARRATIVE_LEN:
        return True
    return len(text) > 12 and bool(_SENTENCE_PUNCT.search(text))


def _is_page_number_row(row):
    text = "".join(word["text"] for word in row)
    return bool(re.fullmatch(r"[–\-—\s]*\d+[–\-—\s]*", text))


def _assemble_grid(rows):
    if not rows:
        return []

    number_x1 = []
    text_x0 = []
    for row in rows:
        for word in row:
            if is_number(word["text"]):
                number_x1.append(word["x1"])
            elif re.search(r'[一-鿿]', word["text"]) and word["x0"] > 0:
                text_x0.append(word["x0"])

    anchors = _cluster_x1(number_x1)
    if len(anchors) < 2 and text_x0:
        text_anchors = _cluster_text_x0(text_x0)
        anchors = sorted(set(anchors + text_anchors))
    if not anchors:
        return [_split_row_by_gaps(row) for row in rows]

    grid = []
    width = len(anchors) + 1
    for row in rows:
        cells = ["" for _ in range(width)]
        for word in sorted(row, key=lambda item: item["x0"]):
            column = _word_column(word, anchors)
            if column is None or column >= width:
                continue
            if cells[column]:
                cells[column] += " "
            cells[column] += word["text"]
        cells = _split_concatenated_cells(cells)
        grid.append([re.sub(r"\s+", " ", cell).strip() for cell in cells])
    return grid


def _cluster_text_x0(values):
    """用 CJK 文本词左边界聚类，找非首列（产品名列）的列起始位置。"""
    values = sorted(set(v for v in values if v > 80))
    if len(values) <= 1:
        return []
    # 找间隔 > 40 的跳跃点作为列边界
    boundaries = []
    for i in range(1, len(values)):
        if values[i] - values[i - 1] > 40:
            boundaries.append(values[i])
    return boundaries


def _split_concatenated_cells(cells):
    """拆分文字数字粘连及数字间粘连。"""
    result = []
    for cell in cells:
        text = str(cell or "")
        # 尝试拆分 CJK文字+数字：開發與授權105.59.2%185.9 → 開發與授權, 105.5, 9.2%, 185.9
        m = re.match(r'^([一-鿿][一-鿿\s\-–—]+?)([\d,.\-\(\)%\s]+)$', text)
        if m:
            result.append(m.group(1).strip())
            nums = m.group(2).strip()
            result.extend(_split_number_sequence(nums))
        else:
            # 纯数字+百分比粘连：105.59.2% → 105.5, 9.2%
            expanded = _split_number_sequence(text)
            result.extend(expanded if len(expanded) > 1 else [text])
    return result


def _split_number_sequence(text):
    """拆分数字序列：105.5 9.2% 185.9 20.4% → [105.5, 9.2%, 185.9, 20.4%]"""
    text = str(text or "").strip()
    if not text:
        return [text]
    # 先按空白分
    parts = re.split(r'\s+', text)
    result = []
    for p in parts:
        # 尝试拆百分比粘连：105.59.2% → 105.5, 9.2%
        # 匹配模式：数字(可含小数点)后紧跟百分比或另一个数字
        sub = re.findall(r'\(?-?[\d,]+(?:\.\d+)?[%％]?\)?', p)
        if len(sub) > 1:
            result.extend(s for s in sub if s)
        else:
            result.append(p)
    return result


def _cluster_x1(values):
    values = sorted(values)
    clusters = []
    for value in values:
        if clusters and value - clusters[-1] <= _ANCHOR_TOLERANCE:
            clusters[-1] = (clusters[-1] + value) / 2.0
        else:
            clusters.append(value)
    return clusters


def _word_column(word, anchors):
    for index, anchor in enumerate(anchors):
        if abs(word["x1"] - anchor) <= _ANCHOR_TOLERANCE:
            return index + 1
    if is_number(word["text"]):
        nearest = min(range(len(anchors)), key=lambda index: abs(word["x1"] - anchors[index]))
        return nearest + 1

    middle = (word["x0"] + word["x1"]) / 2.0
    if middle < anchors[0] - 8:
        return 0
    for index in range(len(anchors) - 1):
        if middle <= (anchors[index] + anchors[index + 1]) / 2.0:
            return index + 1
    return len(anchors)


def _split_row_by_gaps(row):
    gap = 8
    cells = []
    current = [row[0]]
    for index in range(1, len(row)):
        if row[index]["x0"] - row[index - 1]["x1"] > gap:
            cells.append("".join(word["text"] for word in current))
            current = [row[index]]
        else:
            current.append(row[index])
    cells.append("".join(word["text"] for word in current))
    return cells
