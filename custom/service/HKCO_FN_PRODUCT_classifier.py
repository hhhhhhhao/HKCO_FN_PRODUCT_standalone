# -*- coding: utf-8 -*-
"""简单的 HKCO 表格分类。

分类依据：章节第一行标题 + 表格特征。

classify_main_inner(main_inner_lines)：
- 给 main_inner_lines 里每张表加 classification 字段
- 格式：title分类/table分类
- 返回原 main_inner_lines

分类目标只有三类：
- profit_loss：标题为损益表/综合损益表
  例：AN202602241819995282，第 8 页「簡明綜合損益表」
  例：AN202603051820302534，第 38 页「合併損益表」
  例：AN202603201820669174，第 2 页「綜合損益表」
  例：AN202603271820801411，第 139 页「綜合損益表」
- product_in_rows：产品名横向在表头行
  例：AN202502211643369388，第 8 页（商品業務/主要投資及金融服務在表头）
  例：AN202501201642376981，第 8 页（環保產品/自來水廠在表头）
  例：AN202602091819840147，第 10 页（日本的四季康樂活動業務等产品在表头）
  例：AN202602131819921438，第 9 页（物流服務/物業投資等产品在表头）
  例：AN202602271820105743，第 16 页（工程解決方案-運動控制/可再生能源等在表头）
  例：AN202503281648794424，第 12 页（酒店營運及配套業務/物業投資等产品在表头）
  例：AN202603201820657945，第 28 页（新秀麗/TUMI/American Tourister 在表头）
- product_in_columns：产品名纵向在第一列
  例：AN202502261643530572，第 12 页（全科醫療服務/專科醫療服務/牙科服務在第一列）
  例：AN202502271643575865，第 12 页（智慧城市解決方案/可再生能源等在第一列）
  例：AN202503251647430529，第 8 页（提供氣膜建造服務等在第一列）
  例：AN202602131819926770，第 6 页（娛樂場/客房/購物中心等在第一列）
  例：AN202503141644357580，第 45 页（銷售原鋁及合金/銷售氧化鋁等在第一列）
  例：AN202602261820070247，第 6 页（股票、期權、基金及期貨經紀等在第一列）
  例：AN202502281643617359，第 6 页（銀行存款之利息收入等在第一列）
  例：AN202603051820293798，第 13 页（酒店/投資物業/發展物業等在第一列）
  例：AN202602271820101294，第 11 页（銷售傢俱產品/資訊科技管理服務等在第一列）
  例：AN202503281648796250，第 7 页（融資擔保收益/顧問服務費等在第一列）
  例：AN202603271820814256，第 6 页（融資擔保收益/顧問及維護服務費等在第一列）
  例：AN202603011820160854，第 8 页（自來水供應及相關服務收入等在第一列）
  例：AN202602271820100616，第 19 页（物業銷售/物業租賃等在第一列）
  例：AN202602271820107497，第 6 页（種植業務/水果分銷業務等在第一列）
  例：AN202503281648703371，第 7 页（銷售PHC管樁/銷售商品混凝土等在第一列）
  例：AN202603101820443458，第 14 页（投資物業/發展物業/酒店等在第一列）
  例：AN202603121820513984，第 18 页（銷售發展物業/管理及服務收入等在第一列）
  例：AN202603031820229304，第 53 页（銅/鋅/鉛/金/銀等在第一列）
  例：AN202504301664954261，第 5 页（交易費及交易系統使用費等在第一列）
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

# 产品/收入分拆关键词：表内出现这些内容说明是产品收入分布表（非纯损益表）
PRODUCT_SPLIT_KEYWORDS = [
    "客戶合約收益", "客户合约收益", "客戶合約收入", "客户合约收入",
    "貨品類型", "货品类型", "產品類型", "产品类型", "服務類型", "服务类型",
    "商品和服務的類型", "商品和服务的类型",
    "收入明細", "收入明细", "收益明細", "收益明细",
    "按主要產品", "按主要产品", "按產品", "按产品",
    "按客戶", "按客户", "按業務", "按业务",
    "銷售產品", "销售产品", "提供服務", "提供服务",
    "分部收益", "分部收入", "分部業績", "分部业绩",
    "來自客戶合約", "来自客户合约",
    "來自客戶合同", "来自客户合同",
    "按商品類型", "按商品类型",
]


def classify_table(rows, prior_names):
    if not rows:
        return "unsupported"

    # 规则 1：上期产品名命中 → 方向信号可信
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
            # 规则 1a：平局时仅当结构信号极强（≥3倍差）时才打破，否则保持原默认
            if max_row == max_col:
                first_row_text = sum(1 for c in (rows[0] if rows else []) if str(c).strip() and not format_number(c))
                first_col_text = sum(1 for r in rows if r and str(r[0]).strip() and not format_number(r[0]))
                if first_col_text >= 6 and first_col_text >= first_row_text * 3:
                    return "product_in_columns"
                if first_row_text >= 6 and first_row_text >= first_col_text * 3:
                    return "product_in_rows"
            return "product_in_rows" if max_row >= max_col else "product_in_columns"

    # 规则 2：收入确认时间关键词（隨時間/於某一時點等）→ 检验同行/同列文本密度
    row_votes = 0
    col_votes = 0
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            cell_text = str(cell)
            if not any(keyword in cell_text for keyword in KEYWORDS):
                continue
            same_row = [
                str(value) for value in row
                if str(value).strip() and str(value) != cell_text and not format_number(value)
            ]
            same_col = [
                str(other_row[col_index]) for other_row in rows
                if col_index < len(other_row) and str(other_row[col_index]).strip()
                and str(other_row[col_index]) != cell_text and not format_number(other_row[col_index])
            ]
            if same_row:
                row_votes += 1
            if same_col:
                col_votes += 1
    if row_votes or col_votes:
        return "product_in_rows" if row_votes >= col_votes else "product_in_columns"

    # 规则 3：产品/收入分拆关键词 → 结合结构判断方向
    has_product_kw = any(
        any(keyword in str(cell) for keyword in PRODUCT_SPLIT_KEYWORDS)
        for row in rows for cell in row
    )
    first_row_text = sum(1 for c in (rows[0] if rows else []) if str(c).strip() and not format_number(c))
    first_col_text = sum(1 for r in rows if r and str(r[0]).strip() and not format_number(r[0]))
    if has_product_kw and (first_row_text >= 2 or first_col_text >= 2):
        return "product_in_rows" if first_row_text >= first_col_text else "product_in_columns"

    # 规则 4：纯结构推断 — 2+行2+列表格，第一行/列文本密度决定方向
    if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
        if first_row_text >= 2 and first_row_text > first_col_text:
            return "product_in_rows"
        if first_col_text >= 2 and first_col_text > first_row_text:
            return "product_in_columns"

    return "unsupported"


title_classification_patterns = [
    (r"損益表|损益表|收益表|綜合損益|综合损益|全面收益|全面收入|虧損表|亏损表|"
     r"income statement|profit or loss|profit and loss", "profit_loss"),
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

        if title_classification == "profit_loss":
            inner_line["classification"] = 'profit_loss'
            continue

        if table_classification == 'product_in_rows':
            inner_line["classification"] = "product_in_rows"
            continue

        if table_classification == 'product_in_columns':
            inner_line["classification"] = "product_in_columns"
            continue

    return main_inner_lines
