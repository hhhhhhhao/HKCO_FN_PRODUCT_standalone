# -*- coding: utf-8 -*-
"""HKCO 公共工具：全角转半角、上期产品名匹配。"""
import unicodedata
import re


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
