# -*- coding: utf-8 -*-
"""阶段 1：把 OCR lines 按标题拆成章节，并定位章节内表格。

这里只恢复文档结构，不选择主表、不判断表型、不抽取金额。标题只来自本文件
的正则切章；每张原始物理表直接归入所属章节。
"""
import re
import string

from custom.service.HKCO_FN_PRODUCT_utils import contains_chinese, is_number


def match_patterns(s, patterns):
    for pattern, group in patterns:
        match = re.search(pattern, s)
        if match and match.group(group):
            return match.group(group)
    return None


def is_title_line(line, title_regex, exclude_regex, sure_regex):
    """
    判断一行是否是标题行。

    参数:
        line (dict): 包含文本和位置信息的行字典
        title_regex (list): 标题匹配正则模式列表
        exclude_regex (list): 排除匹配正则模式列表
        sure_regex (list): 强制匹配正则模式列表

    返回:
        bool: 如果是标题行返回 True，否则返回 False
    """

    text = line.get("text", "").strip()

    if text == '下表載列二零二六財年的收益明細,連同二零二五財年的比較業績:':
        print

    text = line["text"]
    if not text:
        return False
    
    if sum(1 for ch in text if ch.isdigit()) > 5:
        return False

    if is_number(text):
        return False

    if not contains_chinese(text):
        return False

    # if line.get("is_table", False):
    #     return False

    # 强制匹配：如果匹配 sure_regex，直接认为是标题
    if match_patterns(text, sure_regex):
        return True

    # 排除条件：匹配 exclude_regex 的不是标题
    if match_patterns(text, exclude_regex):
        return False

    # if line['source_type'] == 'title' or line['source_type'] == 'table_caption':
    #     return True

    # 以[一二三四五六七八九十]开头后面跟中文的
    if re.search(r"^[一二三四五六七八九十][\u4e00-\u9fff]+", text) and len(text) > 5 and all(ch not in string.punctuation for ch in text[:5]):
        return False


    # 排除逗号或句号过多的行（超过3个）
    if text.count(",") >= 3 or text.count("、") >= 3 or text.count(".") >= 3 or (text.count(".") >= 2 and sum(1 for c in text if c.isdigit()) >= 5):
        return False


    # 最终匹配 title_regex
    return match_patterns(text, title_regex) is not None


def get_lines_grouped(lines):
    title_regex = [
        (r"^[\(（]*[、\)）.．。]*[一二三四五六七八九十①②③④⑤⑥⑦⑧⑨A-Da-di]+[、\)）.．。]*", 0),
        (r"^[\(（]*[、\)）.．。]*[1234567890]+[、\)）.．。]*(表|收入|收益|附註|)", 0),
        (r"(表|附註|各項:|如下:|劃分)$", 0), # not 收入: 收益: 包括: 淨額
        (r"按.*(劃分|分)", 0),
        (r"^(地域資料|分部.*業績|有關.*資料|可呈報.*對賬|管理層.*分析|分部資料|下表.*業績:)$", 0),
    ]
    exclude_regex = [
        (r"^(一般)", 0),
        (r"^[一二三四五六七八九十0123456789]+(室|期|级|类|个|家|致)", 0),
        (r"^[一二三四五六七八九十0123456789]+個月$", 0),
        (r"^(\d{4}年|\d{2}月|202\d)", 0),
        (r"^\d+(个月|年)", 0),
        (r"^\(\d+(个月|年)", 0),
        (r"^\d+\-\d+(个月|年)", 0),
        (r"[。%;；《》]", 0),
        (r"^(四川|十堰|三峡|三一|一大|九江|五矿)", 0),
        (r"(元|位于|注册资本为|100|合計|小計|準則)", 0),
        (r"^(披露)$", 0),
        (r"^(截).*(月|年度)$", 0),
        (r"^(於|于)\s*\d{4}年\d{1,2}月\d{1,2}日", 0),
        (r"^[一二三四五六七八九十零]{4}年$", 0),
    ]
    sure_regex = [
        (r"^(近[一二三四五六七八九])年.*(表|如下:|所示:)$", 0),
        (r"^(近[一二三四五六七八九])年", 0),
        (r"(按.*(地區|地区|地域|區域|区域|所在地).*劃分)", 0),
        (r"按.*(收入|收益|業績)", 0),
        (r"^(其他分部資料:｜綜合全面收益表)$", 0),
        (r"(分部.*如下:)$", 0),
    ]

    # 找到标题段落所在索引
    lines_group_index =[0] + [
        index for index, line in enumerate(lines)
        if is_title_line(line, title_regex, exclude_regex, sure_regex)
    ]
    # 按照标题段落索引分组
    lines_grouped = []
    for i, index in enumerate(lines_group_index):
        # 如果是最后一个索引，则取到列表末尾
        if i == len(lines_group_index) - 1:
            group = lines[index:]
        else:
            # 取当前索引到下一个索引之前的部分
            next_index = lines_group_index[i + 1]
            group = lines[index:next_index]
        if group:
            lines_grouped.append(group)

    # 孤立的“收入”标题并入下一章节，只保留一份，避免复制行。
    merged = []
    for i, inner_lines in enumerate(lines_grouped):
        if not inner_lines:
            continue
        first_line = inner_lines[0]["text"]
        is_lone_heading = (
            len(inner_lines) == 1
            and not inner_lines[0].get("is_table")
            and '收入' in first_line
        )
        if is_lone_heading and i + 1 < len(lines_grouped):
            lines_grouped[i + 1].insert(0, dict(inner_lines[0]))
        else:
            merged.append(inner_lines)

    return merged
