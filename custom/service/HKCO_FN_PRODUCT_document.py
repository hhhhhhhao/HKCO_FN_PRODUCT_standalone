# -*- coding: utf-8 -*-
"""阶段 1：把 OCR lines 按标题拆成章节，并定位章节内表格。

这里只恢复文档结构，不选择主表、不判断表型、不抽取金额。标题只来自本文件
的正则切章；每张原始物理表直接归入所属章节。
"""
import re
import string


TITLE_PATTERNS = (
    re.compile(
        r"^\s*(?!(?:截至|於截至|于截至|截至於|截至于|20\d{2}))"
        r"[\(（]?"
        r"(?:[一二三四五六七八九十百]+|\d+(?:\.\d+)*|[①②③④⑤⑥⑦⑧⑨⑩]|[A-Da-d]|[ivxIVX]+)"
        r"[\)）、.．。]+\s*"
    ),
)
SURE_TITLE_PATTERNS = (
    re.compile(
        r"^(?:主营业务|营业收入|收入|收益|营业额|销售额|成本|营业成本|销售成本|"
        r"毛利|毛利润|分部资料|分部信息|经营分部|业务分部|产品|服务|业务)"
        r"(?:情况|分析|明细|构成|结构|分布)?[:：]?$",
        re.I,
    ),
    re.compile(r"(收入|收益|营业额|销售额|余额)(构成|结构|分析|分拆|明细)"),
    re.compile(r"^(表|图表).*投资资产"),
    re.compile(r"^(表|图表|报告期内).*(情况|明细|表|结构|所示[:：]?)$"),
    re.compile(r"^(?:最近|情况).*(?:表|如下[:：]?|所示[:：]?)$"),
    re.compile(r"^近[一二三四五六七八九]年.*(表|如下[:：]?|所示[:：]?)?$"),
    re.compile(r"分布情况[:：]?$"),
    re.compile(
        r"^(?=.*(?:收入|收益|營業額|营业额|銷售額|销售额|產品|产品|商品|貨品|货品|"
        r"服務|服务|業務|业务|分部|地區|地区|成本|毛利|利潤|利润|損益|损益|"
        r"財務|财务|業績|业绩|revenue|turnover|sales|product|service|segment|cost|profit))"
        r".{1,500}(?:如下|如下所示|呈列如下|列示如下|載列如下|载列如下|"
        r"載於下表|载于下表|於下表|于下表)\s*[:：]?$",
        re.I,
    ),
)
EXCLUDE_TITLE_PATTERNS = (
    # 期间、币种和单位只是表格测量坐标，不能开启新的业务章节。
    re.compile(
        r"^(?:截至|於截至|于截至|截至於|截至于)"
        r"(?!.*(?:收入|收益|营业额|營業額|销售额|銷售額|产品|產品|商品|服务|服務|"
        r"业务|業務|分部|成本|毛利|利润|利潤|损益|損益|业绩|業績))"
        r".*(?:年度|期间|期間|个月|個月|月|日)",
        re.I,
    ),
    re.compile(
        r"^(?:单位[:：]?\s*)?(?:人民币|人民幣|港元|港币|港幣|美元|欧元|歐元|日元)"
        r"(?:百万元|百萬港元|百萬美元|万元|萬元|千元|元)?\s*$",
        re.I,
    ),
    re.compile(r"^(?:续|續|续表|續表|continued)\s*$", re.I),
    re.compile(r"^一般"),
    re.compile(r"^[一二三四五六七八九十0123456789]+(室|期|级|类|个|家|致)"),
    re.compile(r"^(\d{4}年|\d{2}月|202\d)"),
    re.compile(r"^\(?\d+(?:-\d+)?(个月|年)"),
    re.compile(r"[。%;；《》]"),
    re.compile(r"^(四川|十堰|三峡|三一|一大|九江|五矿)"),
    re.compile(r"(位于|注册资本为|100)"),
)
SEMANTIC_TITLE = re.compile(
    r"收入|收益|营业额|销售额|产品|商品|货品|服务|业务|分部|地区|"
    r"成本|毛利|利润|损益|财务|业绩|revenue|turnover|sales|product|"
    r"service|segment|cost|profit",
    re.I,
)
MEASUREMENT_TEXT = re.compile(
    r"截至|止年度|止期间|20\d{2}|人民币|港元|美元|欧元|日元|"
    r"百万元|千元|单位|expressed in|year ended",
    re.I,
)
PERIOD_TEXT = re.compile(r"财务年度|年度业绩|financial\s+year\s+ended|year\s+ended", re.I)


def _matches(text, patterns):
    return any(pattern.search(text) for pattern in patterns)


def is_title_line(line):
    """判断一条非表格 line 是否开启新章节。

    所有非表格文本统一经过正则、排除词、标点密度和左边距判断。表格 line 在
    入口直接排除，表格内容永远不会被切开。
    """
    if not isinstance(line, dict) or line.get("is_table"):
        return False
    text = str(line.get("text") or "").strip()
    if not text:
        return False
    # 排除规则必须先于强标题规则和 MinerU title 类型，否则“截至某日止年度”
    # 这类期间标题会把上下连续的表格错误切成两个章节。
    if _matches(text, EXCLUDE_TITLE_PATTERNS):
        return False
    if MEASUREMENT_TEXT.search(text) and not SEMANTIC_TITLE.search(text):
        return False
    if _matches(text, SURE_TITLE_PATTERNS):
        return True
    if (re.search(r"^[一二三四五六七八九十][\u4e00-\u9fff]+", text)
            and len(text) > 5
            and all(ch not in string.punctuation for ch in text[:5])):
        return False
    if text.count(",") >= 3 or text.count("、") >= 2 or text.count(".") >= 3:
        return False
    if (text.count(".") >= 2
            and sum(character.isdigit() for character in text) >= 5):
        return False
    x0 = line.get("x0")
    if isinstance(x0, (int, float)):
        # MinerU bbox 通常是 0～1；旧 OCR line 也可能使用页面像素坐标。
        left_limit = 0.32 if 0 <= x0 <= 1 else 190
        if x0 >= left_limit:
            return False
    return _matches(text, TITLE_PATTERNS)


def get_lines_grouped(lines):
    """按标题切章节，并阻止新出现的纯表格继承很久以前的无关标题。

    第一轮只使用通用标题规则。第二轮处理一种常见的拼接文档：标题章节已经结束，
    中间至少隔了一整页没有表格，后面突然从纯表格开始。此时在该 table line 前
    建立无标题章节；后续相邻页表格仍留在同一章节，作为可能的续表。
    """
    document_lines = list(lines or [])
    title_indexes = [index for index, line in enumerate(document_lines) if is_title_line(line)]
    if not title_indexes:
        title_indexes = []
    boundaries = (([0] if title_indexes[0] else []) + title_indexes
                  if title_indexes else [])
    if not boundaries and document_lines:
        boundaries = [0]
    boundaries = list(dict.fromkeys(boundaries))
    title_groups = [document_lines[start:end]
                    for start, end in zip(boundaries, boundaries[1:] + [len(document_lines)])
                    if start < end]

    groups = []
    for group in title_groups:
        split_indexes = [0]
        last_table_page = None
        section_page = group[0].get("page_number") if group else None
        for index, line in enumerate(group):
            if not isinstance(line, dict) or not line.get("is_table"):
                continue
            page = line.get("page_number")
            if (index > 0 and isinstance(page, int)
                    and ((last_table_page is None and isinstance(section_page, int)
                          and page - section_page > 1)
                         or (isinstance(last_table_page, int) and page - last_table_page > 1))):
                split_indexes.append(index)
            last_table_page = page
        split_indexes = list(dict.fromkeys(split_indexes))
        groups.extend(
            group[start:end]
            for start, end in zip(split_indexes, split_indexes[1:] + [len(group)])
            if start < end
        )
    return groups


def _group_title(group):
    for line in group:
        # 标题必须位于本章节第一张表之前；无标题表不能借用表后的正文。
        if isinstance(line, dict) and line.get("is_table"):
            break
        if isinstance(line, dict) and not line.get("is_table"):
            text = str(line.get("text") or "").strip()
            if text:
                return text[:500]
    return ""


def split_into_sections(lines):
    """按标题正则切章；每张原始物理表原样归入所属章节。"""
    sections = []
    physical_index = 0
    for group_index, group in enumerate(get_lines_grouped(lines)):
        section_title = _group_title(group)
        section = {"title": section_title, "index": group_index, "tables": []}
        for line_index, line in enumerate(group):
            table = line.get("table") if isinstance(line, dict) else None
            if not table:
                continue
            context_lines = []
            for nearby in group[max(0, line_index - 12):line_index]:
                if isinstance(nearby, dict) and not nearby.get("is_table"):
                    text = str(nearby.get("text") or "").strip()
                    if text and MEASUREMENT_TEXT.search(text):
                        context_lines.append(text)
            page = line.get("page_number")
            section["tables"].append({
                "id": f"p{page if page is not None else 'x'}:{physical_index}",
                "page": page,
                "section_title": section_title,
                "rows": table,
                "context": " ".join(context_lines[-8:]),
                "section_index": group_index,
            })
            physical_index += 1
        sections.append(section)
    return sections


def get_document_period_text(lines):
    snippets = []
    for line in lines or []:
        if not isinstance(line, dict) or line.get("is_table"):
            continue
        text = str(line.get("text") or "").strip()
        if text and PERIOD_TEXT.search(text):
            snippets.append(text[:500])
    return " ".join(snippets[:120])
