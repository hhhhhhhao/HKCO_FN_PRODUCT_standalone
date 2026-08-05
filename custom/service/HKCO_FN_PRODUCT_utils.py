# -*- coding: utf-8 -*-
"""HKCO 公共工具：全角转半角、上期产品名匹配。"""
import unicodedata


def fullwidth_to_halfwidth(s):
    s = s.replace('戶', '户')
    return "".join(
        unicodedata.normalize("NFKC", char)
        if unicodedata.east_asian_width(char) in ["F", "W"]
        else char
        for char in s
    )


def last_name_matches(name, text):
    left_key = str(name or "").strip().lower()
    orig_left_key = left_key
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


def historical_product_last_name_matches(prior_names, tables):
    """返回 prior_names 中有多少个名字能命中 tables 表格文本。"""
    table_rows = []
    for item in (tables or []):
        table = item.get("table") if isinstance(item, dict) and "table" in item else item
        if isinstance(table, (list, tuple)):
            table_rows.extend(table)
    tables_flatten = [
        str(cell).replace(" ", "").replace("\\n", "").replace("\n", "")
        for row in table_rows
        for cell in row
    ]
    table_text = str(tables_flatten).strip().lower()

    matched_count = 0
    for left in prior_names:
        if last_name_matches(left, table_text):
            matched_count += 1

    matched_count_arr = 0
    for left in prior_names:
        left_key = left.strip().lower()
        orig_left_key = left_key
        if '-' in left_key:
            left_key = left_key.split('-')[-1]
        if ':' in left_key:
            left_key = left_key.split(':')[0]
        if not left_key or not table_text:
            continue
        if left_key in tables_flatten:
            matched_count_arr += 1

    return matched_count, matched_count_arr


