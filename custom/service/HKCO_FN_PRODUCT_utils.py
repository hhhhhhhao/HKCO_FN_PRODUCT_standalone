# -*- coding: utf-8 -*-
"""HKCO 公共工具：全角转半角、上期产品名匹配。"""
import re
import unicodedata


_GT_VALUE_FIELDS = ("MBREVENUE",)


def _to_number(value):
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.replace(",", "").strip()
        if not text or text in ("-", "--"):
            return None
        try:
            return float(text)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number_format_variants(value):
    """Return plain and comma-separated text forms for a numeric GT value."""
    negative = value < 0
    magnitude = abs(value)
    if magnitude == int(magnitude):
        plain = str(int(magnitude))
        comma = f"{int(magnitude):,}"
    else:
        plain = format(magnitude, ".10f").rstrip("0").rstrip(".")
        comma = format(magnitude, ",.10f").rstrip("0").rstrip(".")
    variants = {plain, comma}
    if negative:
        variants.update({f"-{plain}", f"-{comma}", f"({plain})", f"({comma})"})
    return variants


def missing_gt_values_in_selected_lines(gt_records, selected_lines):
    """Return GT numeric values absent from the selected main lines' text."""
    text = " ".join(
        str(line.get("text") or "") if isinstance(line, dict) else str(line or "")
        for line in (selected_lines or ())
    )
    missing = []
    for row in (gt_records or ()):
        for field in _GT_VALUE_FIELDS:
            value = _to_number(row.get(field))
            if value is None:
                continue
            if not any(variant and variant in text for variant in _number_format_variants(value)):
                missing.append(value)
    return missing


def locate_ok_by_ratio(gt_records, missing_values):
    """GT 数值超过 4 个时，70% 及以上命中即视为定位成功。"""
    total = sum(
        1
        for row in (gt_records or ())
        for field in _GT_VALUE_FIELDS
        if _to_number(row.get(field)) is not None
    )
    if total <= 4:
        return False
    found = total - len(missing_values or ())
    return found / total >= 0.7


def fullwidth_to_halfwidth(s):
    s = s.replace('戶', '户')
    return "".join(
        unicodedata.normalize("NFKC", char)
        if unicodedata.east_asian_width(char) in ["F", "W"]
        else char
        for char in s
    )

def flatten_arr(lst, depth=-1):
    """
    打平多维数组到指定的深度。

    参数:
    lst (list): 要打平的列表。
    depth (int): 打平的深度，默认值为-1，表示完全打平。

    返回:
    list: 打平后的列表。
    """
    flat_list = []
    for item in lst:
        if isinstance(item, list) and depth != 0:
            flat_list.extend(flatten_arr(item, depth - 1))
        else:
            flat_list.append(item)
    return flat_list

def is_number(text):
    # 检验字符串是否是数字（含会计格式括号表示负数，如 (1,102,311)）
    text = str(text).strip().replace(' ', '').replace('(', '').replace(')', '')
    num_part = r"(\d{1,3}(,\d{3})*|\d+)(\.\d+)?"
    return bool(re.fullmatch(r"^([+-]?" + num_part + r"|\(" + num_part + r"\))$", text))

def contains_chinese(text):
    """Check if the text contains any Chinese characters."""
    return bool(re.search(r"[\u4e00-\u9fff]", text))

def last_name_matches(name, text):
    left_key = str(name or "").strip().replace("之", "")
    orig_left_key = left_key
    text = text.replace("之", "")
    if '-' in left_key:
        left_key = left_key.split('-')[-1]
    if ':' in left_key:
        left_key = left_key.split(':')[0]
    if not left_key or not text:
        return False
    if left_key in text:
        return True
    if len(orig_left_key) > 10 and '-' in orig_left_key:
        if all(char in text for char in orig_left_key):
            return True
    return False

def historical_product_last_name_matches(prior_names, inner_words_flatten):
    """返回 prior_names 中有多少个名字能命中 tables 表格文本。"""
    inner_words_text = [word['text'] for word in inner_words_flatten]
    table_text = str(inner_words_text).replace(" ", "").replace("\\n", "").replace("\n", "")

    matched_count = 0
    for left in prior_names:
        if last_name_matches(left, table_text):
            matched_count += 1

    matched_count_arr = 0
    for left in prior_names:
        left_key = left.strip()
        orig_left_key = left_key
        if '-' in left_key:
            left_key = left_key.split('-')[-1]
        if ':' in left_key:
            left_key = left_key.split(':')[0]
        if not left_key or not table_text:
            continue
        if left_key in inner_words_text:
            matched_count_arr += 1

    # 逐字命中：只判断名字的每个字是否都在章节文本里出现，不改动上面的精确匹配。
    continuous_text = "".join(inner_words_text)
    blur_matched_count = 0
    for left in prior_names:
        normalized = fullwidth_to_halfwidth(left).strip().replace(" ", "")
        if normalized and all(char in continuous_text for char in normalized):
            blur_matched_count += 1

    return matched_count, matched_count_arr, blur_matched_count
