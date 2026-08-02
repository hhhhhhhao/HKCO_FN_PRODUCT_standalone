# -*- coding: utf-8 -*-
"""HKCO_FN_PRODUCT get_res"""
import re

from custom.service.EAPS_HKCO_FN_PRODUCT import format_number, fullwidth_to_halfwidth


# ═══════════════════ Chinese numeral → Arabic ═══════════════════

def replace_chinese_numerals(text):
    """
    Replace all occurrences of Chinese numerals in the text with their corresponding Arabic numerals.
    Handles numbers from 0 to 9999 and beyond, including complex structures with 千, 百, and 十.
    """
    # Replace variant zero characters with standard '0'
    text = re.sub(r'[〇○零]', '0', text)

    single_digits = {
        '0': 0, '一': 1, '二': 2, '三': 3, '四': 4,
        '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
    }

    multipliers = {'千': 1000, '百': 100, '十': 10}

    pattern = re.compile(r'[0-9一二三四五六七八九十百千]+')

    def parse_numeral(s):
        total = 0
        current_value = 0
        last_multiplier = float('inf')

        for c in s:
            if c in single_digits:
                current_value += single_digits[c]
            elif c in multipliers:
                if current_value == 0 and c in ('十', '百', '千'):
                    current_value = 1
                multiplier = multipliers[c]
                if multiplier >= last_multiplier:
                    total += current_value
                    current_value = 0
                else:
                    total += current_value * multiplier
                    current_value = 0
                    last_multiplier = multiplier
            else:
                continue
        total += current_value
        return str(total)

    def replace_match(match):
        numeral = match.group()
        if numeral.isdigit():
            return numeral
        return parse_numeral(numeral)

    return pattern.sub(replace_match, text)


def classify_table(table, title,last_period_data):
    """返回 '收入_行产品' 或 None"""
    if '收入' in title and is_row_product(table, last_period_data):
        return "收入_行产品"
    return ""

def is_row_product(table, last_period_data=None):
    """用上期产品名匹配：命中行头多 → 行产品，命中列头多 → 列产品"""
    names = [str(r.get("PRODUCTNAME", "")).strip()
             for r in (last_period_data or [])
             if isinstance(r, dict) and str(r.get("PRODUCTNAME", "")).strip() != "合计"]
    if names:
        return True

    # col 0 有 合计/总计/小计 → 行产品表
    for r in table:
        if not isinstance(r, list) or len(r) < 2:
            continue
        lab = str(r[0] or "").strip()
        if re.match(r"^(合[计計]|總[计計]|小[计計])", lab):
            return True
        
    return False

def get_res(selected, info_code, reason_arr, notice_date="", last_period_data=None):
    res = {"target_res": [], "pipe_meta": {"selected_count": 0, "source_pages": []}}
    if not selected: reason_arr.append("未选到目标表"); return res

    target_table = selected.get("target_table") if isinstance(selected, dict) else selected
    page_number = selected.get("page_number") if isinstance(selected, dict) else None
    title = selected.get("title") if isinstance(selected, dict) else ""
    if page_number is not None: res["pipe_meta"]["source_pages"] = [page_number]
    res["pipe_meta"]["selected_count"] = 1
    if not target_table: reason_arr.append("目标表为空"); return res

    rows = []
    typ = classify_table(target_table, title, last_period_data)
    if not typ:
        reason_arr.append("未识别表类型")
        return res

    if typ == "收入_行产品":
        rows = extract_type1(target_table)
    if not rows:
        reason_arr.append("提取为空")
        return res

    res["target_res"] = rows
    return res



def extract_type1(table):
    """从行产品表的表身中提取每行：产品名 + 收入金额"""
    out = []

    return out

