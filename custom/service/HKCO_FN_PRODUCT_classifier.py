# -*- coding: utf-8 -*-
"""简单的 HKCO 表格分类。

分类依据：章节第一行标题 + 表格特征。

classify_main_inner(main_inner_lines)：
- 给 main_inner_lines 里每张表加 classification 字段
- 格式：title分类/table分类
- 返回原 main_inner_lines

title 分类：
- product_service：产品/服务拆分
- business：分部/业务拆分
- geography：按地区
- sales_channel：按销售渠道
- customer：按客户
- recognition_time：按收入确认时间
- unknown：看不出名称

table 分类：
- product_in_rows：产品在行
- product_in_columns：产品在列
- unsupported：形状不支持
"""
import re

from custom.service.HKCO_FN_PRODUCT_utils import fullwidth_to_halfwidth, last_name_matches


def format_number(text):
    if not text or text == '-' or text == '——':
        return ""

    text = str(text)

    if '-' not in text and '(' in text and ')' in text:
        text = '-' + text

    text = re.sub(r"[^\d.-]", "", text)
    return text


KEYWORDS = [
    "按時間段",
    "按时间段",
    "於某一時點",
    "于某一时点",
    "按某個時點",
    "按某个时点",
    "隨時間",
    "随时间",
    "時間點",
    "时间点",
    "確認收益時間",
    "确认收益时间",
    "收入確認時間",
    "收入确认时间",
    "收益確認時間",
    "收益确认时间",
]


def classify_table(rows, prior_names):
    if not rows:
        return "unsupported"

    if prior_names:
        max_row = 0
        for row in rows:
            count = 0
            for cell in row:
                cell_key = str(cell).replace(" ", "").replace("\\n", "").replace("\n", "").lower()
                if any(last_name_matches(name, cell_key) for name in prior_names):
                    count += 1
            if count > max_row:
                max_row = count

        max_col = 0
        col_count = max(len(row) for row in rows)
        for col_index in range(col_count):
            count = 0
            for row in rows:
                if col_index < len(row):
                    cell_key = str(row[col_index]).replace(" ", "").replace("\\n", "").replace("\n", "").lower()
                    if any(last_name_matches(name, cell_key) for name in prior_names):
                        count += 1
            if count > max_col:
                max_col = count

        if max_row or max_col:
            return "product_in_rows" if max_row >= max_col else "product_in_columns"

    row_votes = 0
    col_votes = 0
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            cell_text = str(cell)
            if not any(keyword in cell_text for keyword in KEYWORDS):
                continue
            same_row = [
                str(value)
                for value in row
                if str(value).strip()
                and str(value) != cell_text
                and not format_number(value)
            ]
            same_col = [
                str(other_row[col_index])
                for other_row in rows
                if col_index < len(other_row)
                and str(other_row[col_index]).strip()
                and str(other_row[col_index]) != cell_text
                and not format_number(other_row[col_index])
            ]
            if same_row:
                row_votes += 1
            if same_col:
                col_votes += 1
    if row_votes or col_votes:
        return "product_in_rows" if row_votes >= col_votes else "product_in_columns"

    return "unsupported"


title_classification_patterns = [
    (r"損益表|损益表|收益表|綜合損益|综合损益|全面收益|全面收入|虧損表|亏损表|"
     r"income statement|profit or loss|profit and loss", "profit_loss"),
    (r"按(?:主要)?(?:產品|产品|商品|貨品|货品|服務|服务|類別|类别|類型|类型)", "product_service"),
    (r"(?:收入|收益|營業收入|營業額|銷售收入|銷售額|revenue|turnover|sales)"
     r".{0,10}(?:分析|分類|分类|分拆|細分|细分|明細|明细|分解|劃分|划分|構成|构成|情況|情况)", "product_service"),
    (r"(?:分拆|細分|细分|分類|分类|明細|明细).{0,10}(?:收入|收益|營業收入|營業額)", "product_service"),
    (r"^[\d(a-i).、\s]*(?:收入|收益|營業收入|營業額|銷售收入|銷售額)$", "product_service"),
    (r"產品|产品|商品|貨品|货品|服務|服务|product|service", "product_service"),
    (r"分部|業務|业务|板塊|板块|segment|business", "business"),
    (r"銷售渠道|销售渠道|渠道|批發|批发|零售|channel", "sales_channel"),
    (r"收入確認時間|收入确认时间|收益確認時間|收益确认时间|時間點|时间点|隨時間|随时间|over time", "recognition_time"),
    (r"客戶|客户|customer", "customer"),
    (r"地區|地区|地域|地理|國家|国家|geograph|region|country", "geography"),
]


def get_title_classification(title):
    for pattern, classification in title_classification_patterns:
        if re.search(pattern, title):
            return classification
    return "unknown"


def classify_main_inner(main_inner_lines, prior_names):
    first_line = main_inner_lines[0]['text']
    title_classification = get_title_classification(first_line)

    for inner_line in main_inner_lines:
        if not inner_line.get("is_table") or not inner_line.get("table"):
            continue
        table_classification = classify_table(inner_line["table"], prior_names)
        # inner_line["classification"] = f"{title_classification}/{table_classification}"

        if table_classification == 'profit_loss':
            inner_line["classification"] = 'profit_loss'
            continue

        if table_classification == 'product_in_rows':
            inner_line["classification"] = "product_in_rows"
            continue

        if table_classification == 'product_in_columns':
            inner_line["classification"] = "product_in_columns"
            continue

    return main_inner_lines
