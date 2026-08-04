# -*- coding: utf-8 -*-
"""Cheap, amount-free table features used only for table classification."""
import re
from typing import List, Sequence

from custom.service.HKCO_FN_PRODUCT_fact_model import TableEvidence, TableRef
from custom.service.HKCO_FN_PRODUCT_identity import (
    identity_match_profile,
    identity_matched_current_keys,
)


NUMBER = re.compile(r"^\s*\(?-?[\d,]+(?:\.\d+)?\)?\s*$")
PERIOD = re.compile(r"20\d{2}|二零[〇零一二三四五六七八九]{2}|二〇[〇零一二三四五六七八九]{2}", re.I)
CURRENCY = re.compile(r"人民幣|人民币|港幣|港币|港元|美元|歐元|欧元|日圓|日圆|日元|新加坡元|坡元", re.I)
UNIT = re.compile(
    r"百萬(?:元|日圓|日元|港元|美元|歐元|欧元)?|百万(?:元|日元|港元|美元|欧元)?|"
    r"萬元|万元|千(?:元|港元|美元|歐元|欧元|日圓|日圆|日元|令吉特)|元|million|thousand",
    re.I,
)
REVENUE = re.compile(r"收入|收益|營業額|营业额|銷售額|销售额|revenue|turnover|sales", re.I)
REVENUE_METRIC_LABEL = re.compile(
    r"^(?:(?:總|总|淨|净)?(?:收入|收益|營業額|营业额|銷售額|销售额)|"
    r"(?:分部|可呈報分部|可呈报分部)(?:收入|收益)(?:總額|总额)?|"
    r"(?:本集團|本集团|集團|集团)(?:收入|收益))$|"
    r"^外部(?:客戶|客户).*(?:收入|收益)$|"
    r"^(?:來自|来自).*(?:客戶|客户).*(?:收入|收益)$|"
    r"^(?:對外|对外|外部)(?:部)?(?:來源|来源)?(?:銷售|销售)(?:額|额)?$|"
    r"^(?:綜合|综合).*(?:收入|收益)$|"
    r"^(?:本集團|本集团)?(?:分部)?(?:收入|收益|營業收入|营业收入)\s*[（(].*[）)]$|"
    r"^(?:external (?:customer )?)?(?:revenue|turnover|sales)$",
    re.I,
)
EXTERNAL_REVENUE_METRIC_LABEL = re.compile(
    r"(?:(?:收入|收益).*(?:來自|来自).*(?:外部|外界).*(?:客戶|客户)|"
    r"(?:來自|来自).*(?:外部|外界).*(?:客戶|客户).*(?:收入|收益)|"
    r"^(?:對外|对外|外部)(?:銷售|销售)(?:額|额)?$|"
    r"external.*customer.*(?:revenue|turnover|sales))",
    re.I,
)
PRIMARY_REVENUE_ANALYSIS_TITLE = re.compile(
    r"^\s*(?:\d+[.、]?\s*)?(?:(?:本集團|本集团)"
    r"(?:(?:於|于)?(?:本年度|本期間|本期间|年內|年内))?"
    r"(?:之|的)?)?(?:收入|收益)(?:之|的)?分析(?:如下|為|为)?\s*[:：。]?\s*$",
    re.I,
)
SUPPLEMENTAL_REVENUE = re.compile(r"補充|补充|supplemental", re.I)
PRODUCT_SERVICE_BREAKDOWN = re.compile(
    r"貨品或服務類型|货品或服务类型|商品及服務類型|商品及服务类型|"
    r"產品或服務類型|产品或服务类型|type of (?:goods|products|services)", re.I,
)
OTHER_INCOME = re.compile(r"其他.*(?:收入|收益)|other (?:operating )?income|投資收益|投资收益|investment income", re.I)
PRIMARY_WITH_OTHER_INCOME = re.compile(
    r"^\s*(?:\d+[.、]?\s*)?(?!其他)(?:(?:本集團|本集团)(?:之|的)?)?"
    r"(?:收入|收益).{0,12}(?:、|及|和|與|与).{0,12}其他(?:收入|收益)",
    re.I,
)
ALTERNATIVE = re.compile(r"經調整|经调整|adjusted|合約負債|合同负债|contract liabilit|以前期間.*履約義務|以前期间.*履约义务", re.I)
PRODUCT = re.compile(r"產品|产品|商品|服務|服务|product|service", re.I)
BUSINESS = re.compile(r"業務|业务|分部|板塊|板块|segment|business", re.I)
GEOGRAPHY = re.compile(
    r"地區|地区|地域|地理|省份|城市|國家|国家|"
    r"(?:客戶|客户).{0,8}(?:位置|所在地|地點|地点)|"
    r"(?:交付|送達|送达|服務提供|服务提供).{0,8}(?:地點|地点|位置)|"
    r"geograph|region|country|customer location|place of delivery",
    re.I,
)
SALES_CHANNEL = re.compile(
    r"銷售渠道|销售渠道|分銷渠道|分销渠道|渠道劃分|渠道划分|"
    r"眾籌平台|众筹平台|網店|网店|批發|批发|直銷|直销|經銷|经销|"
    r"sales channel|distribution channel|wholesale|crowdfunding", re.I,
)
CUSTOMER_AXIS = re.compile(
    r"主要客戶|主要客户|單一客戶|单一客户|客戶集中|客户集中|"
    r"(?=.*客[戶户])(?=.*(?:10\s*\\?[%％]|百分之十))(?=.*(?:收入|收益)).*|"
    r"客[戶户].{0,20}(?:10\s*[%％]|百分之十).{0,20}(?:收入|收益)|"
    r"(?:收入|收益).{0,20}客[戶户].{0,20}(?:10\s*[%％]|百分之十)|"
    r"customer concentration|major customers?", re.I,
)
RECOGNITION_AXIS = re.compile(
    r"收入確認時間|收入确认时间|收益確認時間|收益确认时间|"
    r"於某一時間點|于某一时间点|在某一時點|在某一时点|"
    r"隨時間確認|随时间确认|一段時間內確認|一段时间内确认|"
    r"timing of revenue recognition|point in time|over time", re.I,
)
MEASUREMENT_AXIS = re.compile(
    r"過渡方法|过渡方法|修正追溯|公允價值法|公允价值法|"
    r"計量模型|计量模型|保費分配法|保费分配法|通用模型|"
    r"transition method|measurement model|premium allocation approach", re.I,
)
GEOGRAPHIC_IDENTITY = re.compile(
    r"^(?:中國|中国|香港|澳門|澳门|台灣|台湾|美國|美国|英國|英国|意大利|"
    r"歐洲|欧洲|亞洲|亚洲|大洋洲|非洲|北美|南美|海外|境外|國內|国内|"
    r"華北|华北|華南|华南|華東|华东|華中|华中|東北|东北|西北|西南)", re.I,
)
SECTION = re.compile(
    r"按.+(?:劃分|划分|分類|分类|分析)|"
    r"產品類別|产品类别|主要產品|主要产品|"
    r"商品或服務(?:種類|类型|類型)|商品或服务(?:种类|类型)|貨品或服務種類|货品或服务种类|"
    r"服務類型|服务类型|服務類別|服务类别|收入類型|收入类型|收益類型|收益类型|"
    r"地區市場|地区市场|市場地區|市场地区|客户類別|客戶類別|客户类别|客戶类别|"
    r"收入確認|收入确认|收益確認|收益确认|確認收入|确认收入|"
    r"type of services|type of goods|geographical market|revenue recognition",
    re.I,
)
METRIC_LABEL = re.compile(r"成本|毛利|毛利率|利潤|利润|溢利|業績|业绩|EBITDA|百分比|%|數目|数目|收入|收益|營業額|营业额|cost|margin|profit|result|revenue", re.I)
NON_REVENUE_METRIC_IDENTITY = re.compile(
    r"業績|业绩|成本|毛利|利潤|利润|溢利|虧損|亏损|開支|开支|費用|费用|"
    r"EBITDA|result|cost|margin|profit|loss|expense", re.I,
)
COST_TITLE = re.compile(r"銷售成本|销售成本|營業成本|营业成本|收入成本|產品分類的成本|产品分类的成本|cost of sales", re.I)
PROFIT_TITLE = re.compile(r"毛利|毛損|毛损|gross profit|gross loss", re.I)
AGING_TITLE = re.compile(r"(?:應收|应收|receivable).*(?:賬齡|账龄|ageing|aging)|(?:賬齡|账龄|ageing|aging).*(?:應收|应收|receivable)", re.I)
EXPENSE_TITLE = re.compile(r"成本|開支|开支|費用|费用|expense|cost", re.I)
EXPENSE_ROW = re.compile(
    r"成本|開支|开支|費用|费用|折舊|折旧|攤銷|摊销|審核|审核|核數|核数|"
    r"保險|保险|税項|稅項|expense|cost|depreciation|amortisation|audit fee",
    re.I,
)
TOTAL_HEADER = re.compile(
    r"(?:合計|合计|合共|總計|总计|總額|总额|總收入|总收入|總收益|总收益|"
    r"本集團|本集团|綜合|综合|consolidated|the group|total)", re.I,
)
FINANCIAL_STATEMENT = re.compile(
    r"(?:綜合|综合|合併|合并|簡明|简明|未經審核|未经审核|未經審計|未经审计)?"
    r"(?:損益表|损益表|利潤表|利润表|全面收益表|全面收入表|經營及全面.*收入表)|"
    r"income statement|statement of profit|statements? of operations",
    re.I,
)
NON_REVENUE_MEASURE = re.compile(
    r"^(?:分部)?(?:資產|资产|負債|负债)$|"
    r"僱員人數|雇员人数|員工人數|员工人数|全職僱員|全职雇员|"
    r"新訂單|新订单|訂單明細|订单明细|"
    r"應收賬款|应收账款|應收款項|应收款项|賬齡|账龄|"
    r"headcount|number of employees|segment assets?|segment liabilities?",
    re.I,
)
OPERATIONAL_MEASUREMENT = re.compile(
    r"噸|吨|公斤|千克|克|盎司|平方米|平方尺|立方米|兆瓦|千瓦|"
    r"銷量|销量|產量|产量|平均售價|平均售价|單價|单价|"
    r"volume|tonnes?|tons?|units?|average (?:selling )?price|%|百分比",
    re.I,
)
PHYSICAL_MEASUREMENT = re.compile(
    r"噸|吨|公斤|千克|克|盎司|平方米|平方尺|立方米|兆瓦|千瓦|"
    r"銷量|销量|產量|产量|平均售價|平均售价|單價|单价|"
    r"volume|tonnes?|tons?|units?|average (?:selling )?price",
    re.I,
)


def _number(value):
    text = str(value or "").strip().replace("，", ",")
    if not NUMBER.match(text):
        return None
    negative = text.startswith("(") and text.endswith(")")
    number = float(text.strip("()").replace(",", ""))
    return -number if negative else number


def _unit_code(value):
    text = str(value or "").strip()
    if re.search(r"百萬|百万|million", text, re.I):
        return "004"
    if re.search(r"千|thousand", text, re.I):
        return "002"
    if text:
        return "001"
    return ""


def _metric_label(value):
    label = re.sub(r"^(?:其中[:：]?\s*|[-–—·•]{1,3}\s*)", "", str(value or "").strip())
    return re.sub(
        r"[（(](?=[^（）()]*?(?:人民幣|人民币|港元|美元|歐元|欧元|日圓|日元|千|萬|万|百萬|百万))[^（）()]*[）)]\s*$",
        "", label, flags=re.I,
    ).rstrip(":：")


def _header_index(row, width, col):
    """Align MinerU header rows that omit the empty identity stub."""
    if len(row) >= width:
        return col
    first = str(row[0] or "").strip() if row else ""
    header_only = bool(
        PERIOD.search(first) or UNIT.search(first) or CURRENCY.search(first)
        or re.search(r"截至|年度|期間|期间|財年|财年", first)
    )
    return col - 1 if header_only else col - (width - len(row))


def column_identity_labels(cells):
    """Return semantic column identities, excluding dates and measurements.

    Corpus insight: two textual cells above a revenue row do not establish a
    column-identity matrix.  Repeated years, currency/unit banners and
    amount-volume-price subcolumns are measurement coordinates, not products.
    A column matrix exists only when one header row contributes at least two
    distinct semantic names after those coordinates are removed.
    """
    labels = []
    for cell in cells:
        raw = str(cell or "").strip().replace("\n", " ")
        if not raw or NUMBER.match(raw) or PERIOD.fullmatch(raw):
            continue
        name = PERIOD.sub("", raw)
        name = CURRENCY.sub("", name)
        name = UNIT.sub("", name)
        name = re.sub(r"[()（）\s/%]+", "", name)
        if not name or OPERATIONAL_MEASUREMENT.search(name) or METRIC_LABEL.fullmatch(name):
            continue
        labels.append(name)
    return list(dict.fromkeys(labels))


def table_refs_from_sources(sources: Sequence[dict]) -> List[TableRef]:
    refs = []
    for index, source in enumerate(sources or []):
        if not isinstance(source, dict) or not isinstance(source.get("target_table"), list):
            continue
        page = source.get("page_number")
        refs.append(TableRef(
            f"p{page if page is not None else 'x'}:{index}", page,
            str(source.get("title") or ""), source["target_table"],
            str(source.get("measurement_context") or ""),
        ))
    return refs


class TableEvidenceScanner:
    def scan(self, tables, document_period_text="", prior_names=None,
             prior_fiscal_month_day=(), stable_unit=""):
        table_period_titles = " ".join(
            table.title for table in tables
            if re.search(r"截至.*(?:月|month).*(?:日|day).*(?:止年度|year ended)", table.title, re.I)
        )
        fiscal_dates = re.findall(
            r"(?:\d{1,2}|[一二三四五六七八九十]{1,3})月"
            r"(?:\d{1,2}|[一二三四五六七八九十]{1,3})日",
            table_period_titles or document_period_text,
        )
        period_context = max(set(fiscal_dates), key=fiscal_dates.count) if fiscal_dates else ""
        context = " ".join(filter(None, (period_context, document_period_text)))
        return [self._scan(table, context, prior_names or [], prior_fiscal_month_day, stable_unit)
                for table in tables]

    def _scan(self, table, document_period_text="", prior_names=None,
              prior_fiscal_month_day=(), stable_unit=""):
        rows = [list(row) for row in table.rows if isinstance(row, (list, tuple))]
        width = max((len(row) for row in rows), default=0)
        text = " ".join([table.title, table.context]
                        + [str(cell or "") for row in rows for cell in row])
        header = " ".join(str(cell or "") for row in rows[:4] for cell in row)
        title_signals = set()
        if REVENUE.search(table.title): title_signals.add("revenue")
        if PRIMARY_REVENUE_ANALYSIS_TITLE.fullmatch(table.title):
            title_signals.add("primary_revenue_analysis")
        if REVENUE.search(table.title) and SUPPLEMENTAL_REVENUE.search(table.title):
            title_signals.add("supplemental_revenue")
        if PRODUCT_SERVICE_BREAKDOWN.search(" ".join(
                [table.title] + [str(cell or "") for row in rows[:4] for cell in row])):
            title_signals.add("product_service_breakdown")
        if COST_TITLE.search(table.title): title_signals.add("cost")
        if EXPENSE_TITLE.search(table.title): title_signals.add("expense")
        if PROFIT_TITLE.search(table.title): title_signals.add("gross_profit")
        if AGING_TITLE.search(table.title): title_signals.add("aging_schedule")
        if OTHER_INCOME.search(table.title): title_signals.add("other_income")
        if PRIMARY_WITH_OTHER_INCOME.search(table.title):
            title_signals.add("primary_with_other_income")
        if ALTERNATIVE.search(table.title): title_signals.add("alternative_basis")
        # A note may say that its figures are "consistent with the consolidated
        # income statement".  That prose does not turn the note into a P&L.
        if len(table.title.strip()) <= 50 and FINANCIAL_STATEMENT.search(table.title):
            title_signals.add("financial_statement")
        first_labels = [str(row[0] or "").strip() for row in rows if row and str(row[0] or "").strip()]
        if first_labels and re.match(r"^其他(?:收入|收益)", first_labels[0], re.I):
            title_signals.add("other_income_section_only")
        leading_cells = [str(cell or "").strip() for row in rows[:4] for cell in row[:3]]
        if (NON_REVENUE_MEASURE.search(table.title)
                or any(NON_REVENUE_MEASURE.search(cell) for cell in leading_cells)):
            title_signals.add("non_revenue_measure")
        axis_signals = set()
        if PRODUCT.search(text): axis_signals.add("product_service")
        if BUSINESS.search(text): axis_signals.add("business")
        if GEOGRAPHY.search(" ".join([table.title, header])): axis_signals.add("geography")
        if SALES_CHANNEL.search(" ".join([table.title, header])): axis_signals.add("sales_channel")
        if CUSTOMER_AXIS.search(" ".join([table.title, header])): axis_signals.add("customer")
        if RECOGNITION_AXIS.search(table.title): axis_signals.add("recognition_time")
        if MEASUREMENT_AXIS.search(table.title): axis_signals.add("measurement_method")

        numeric_rows = [index for index, row in enumerate(rows) if row
                        and any(NUMBER.match(str(cell or "")) for cell in row[1:])]
        first_data = min(numeric_rows, default=len(rows))
        identity_rows = [row for row in rows if row and str(row[0] or "").strip()
                         and not NUMBER.match(str(row[0] or ""))
                         and any(NUMBER.match(str(cell or "")) for cell in row[1:])]
        identity_labels = [str(row[0] or "").strip() for row in identity_rows]
        if sum(bool(GEOGRAPHIC_IDENTITY.search(label)) for label in identity_labels) >= 2:
            axis_signals.add("body_geography")
        if sum(bool(SALES_CHANNEL.search(label)) for label in identity_labels) >= 2:
            axis_signals.add("body_sales_channel")
        if sum(bool(RECOGNITION_AXIS.search(label)) for label in identity_labels) >= 2:
            axis_signals.add("body_recognition_time")
        if sum(bool(MEASUREMENT_AXIS.search(label)) for label in identity_labels) >= 2:
            axis_signals.add("body_measurement_method")
        if sum(bool(BUSINESS.search(label)) for label in identity_labels) >= 2:
            axis_signals.add("body_business")
        if sum(bool(PRODUCT.search(label)) for label in identity_labels) >= 1:
            axis_signals.add("body_product_service")
        layout = set()
        if len(identity_rows) >= 2: layout.add("row_identity")
        expense_rows = [row for row in identity_rows if EXPENSE_ROW.search(str(row[0] or ""))]
        if len(expense_rows) >= 4 and len(expense_rows) * 2 >= len(identity_rows):
            layout.add("expense_ledger")
        metric_identity_rows = [label for label in identity_labels if METRIC_LABEL.search(label)]
        if (width <= 3
                and sum(bool(NON_REVENUE_METRIC_IDENTITY.search(label))
                        for label in identity_labels) >= 2
                and any(REVENUE.search(label) for label in identity_labels)):
            layout.add("metric_ledger")
        revenue_metric_rows = [index for index, row in enumerate(rows)
                               if row and (REVENUE_METRIC_LABEL.fullmatch(_metric_label(row[0]))
                                           or EXTERNAL_REVENUE_METRIC_LABEL.search(_metric_label(row[0])))
                               and sum(bool(NUMBER.match(str(cell or ""))) for cell in row[1:]) >= 2]
        revenue_metric_lines = [index for index, row in enumerate(rows)
                                if row and (
                                    REVENUE_METRIC_LABEL.fullmatch(_metric_label(row[0]))
                                    or (REVENUE.search(_metric_label(row[0]))
                                        and TOTAL_HEADER.search(_metric_label(row[0])))
                                ) and any(NUMBER.match(str(cell or "")) for cell in row[1:])]
        if revenue_metric_lines:
            layout.add("revenue_metric_line")
        if revenue_metric_rows:
            layout.add("revenue_metric_row")
            if any(EXTERNAL_REVENUE_METRIC_LABEL.search(_metric_label(rows[index][0]))
                   for index in revenue_metric_rows):
                layout.add("external_revenue_metric_row")
            for metric_row in revenue_metric_rows:
                if any(len(column_identity_labels(row[1:])) >= 2
                       for row in rows[max(0, metric_row - 8):metric_row]):
                    layout.add("column_identity")
                    break
        row_identity_names = []
        for row in rows:
            numeric = [index for index, cell in enumerate(row) if NUMBER.match(str(cell or ""))]
            if not numeric:
                continue
            labels = [str(cell or "").strip() for cell in row[:min(numeric)]
                      if str(cell or "").strip() not in {"", "-", "–", "—"}
                      and not NUMBER.match(str(cell or ""))]
            if labels:
                row_identity_names.append(labels[0])
        column_identity_names = []
        for metric_row in revenue_metric_rows:
            for row in rows[max(0, metric_row - 8):metric_row]:
                column_identity_names.extend(column_identity_labels(row[1:]))
        if sum(bool(GEOGRAPHIC_IDENTITY.search(label))
               for label in column_identity_names) >= 2:
            axis_signals.add("body_geography")
        if sum(bool(SALES_CHANNEL.search(label))
               for label in column_identity_names) >= 2:
            axis_signals.add("body_sales_channel")
        prior_names = list(prior_names or [])
        row_hits, row_strength = identity_match_profile(prior_names, row_identity_names)
        column_hits, column_strength = identity_match_profile(prior_names, column_identity_names)
        prior_hits = max(row_hits, column_hits)
        prior_axis = ("row" if row_hits > column_hits else "column" if column_hits > row_hits
                      else "mixed" if prior_hits else "")
        identity_axis = ("mixed" if "row_identity" in layout and "column_identity" in layout
                         else "column" if "column_identity" in layout else
                         "row" if "row_identity" in layout else "")
        year_counts = {}
        for col in range(1, width):
            col_header = " ".join(str(row[col] if col < len(row) else "") for row in rows[:max(1, first_data)])
            match = PERIOD.search(col_header)
            if match: year_counts[match.group(0)] = year_counts.get(match.group(0), 0) + 1
        if any(count >= 2 for count in year_counts.values()): layout.add("repeated_period_columns")
        if first_data:
            descriptors = {}
            for col in range(1, width):
                values = []
                for row in rows[:first_data]:
                    index = _header_index(row, width, col)
                    values.append(str(row[index] if 0 <= index < len(row) else ""))
                descriptors[col] = " ".join(values)
            total_columns = [
                col for col, descriptor in descriptors.items()
                if TOTAL_HEADER.search(descriptor)
            ]
            dimensional_columns = [
                col for col, descriptor in descriptors.items()
                if not TOTAL_HEADER.search(descriptor)
                and re.search(r"[一-鿿A-Za-z]", descriptor)
            ]
            if total_columns and dimensional_columns and (
                    PERIOD.search(table.title) or "repeated_period_columns" in layout):
                layout.add("explicit_total_column")
            if "explicit_total_column" in layout and "repeated_period_columns" in layout:
                layout.add("explicit_total_period_columns")
        if first_data and sum(bool(METRIC_LABEL.search(str(cell or "")))
                              for row in rows[:first_data] for cell in row[1:]) >= 2:
            layout.add("metric_columns")
        if first_data and any(
                REVENUE_METRIC_LABEL.fullmatch(_metric_label(cell))
                for row in rows[:first_data] for cell in row[1:]
                if str(cell or "").strip()
        ):
            layout.add("revenue_metric_column")
        header_metric_cells = [
            str(cell or "").strip() for row in rows[:first_data] for cell in row[1:]
            if str(cell or "").strip()
        ]
        non_revenue_metric = re.compile(
            r"成本|毛利|業績|业绩|利潤|利润|溢利|cost|profit|result", re.I
        )
        if (any(REVENUE.search(cell) and not non_revenue_metric.search(cell)
                for cell in header_metric_cells)
                and any(non_revenue_metric.search(cell) for cell in header_metric_cells)):
            layout.add("multi_financial_metric_columns")
        if first_data and sum(
                str(cell or "").strip() in {"%", "百分比"}
                for row in rows[:first_data] for cell in row[1:]
        ) >= 2:
            layout.add("amount_percentage_columns")
        if first_data and "repeated_period_columns" in layout:
            header_cells = [
                str(cell or "") for row in rows[:first_data] for cell in row[1:]
                if str(cell or "").strip()
            ]
            if (any((CURRENCY.search(cell) or UNIT.search(cell)) for cell in header_cells)
                    and any(PHYSICAL_MEASUREMENT.search(cell) for cell in header_cells)):
                layout.add("mixed_measurement_columns")
        section_markers = []
        for index, row in enumerate(rows):
            label = " ".join(str(cell or "").strip() for cell in row[:2])
            if not SECTION.search(label): continue
            axis = ("geography" if GEOGRAPHY.search(label) else
                    "business" if BUSINESS.search(label) else
                    "customer" if re.search(r"客[戶户].*(?:類別|类别)", label) else
                    "product_service" if (PRODUCT.search(label) or re.search(
                        r"(?:收入|收益)(?:類型|类型)", label, re.I
                    )) else "unknown")
            section_markers.append({"row": index, "axis": axis, "label": label})
        if len(section_markers) >= 2: layout.add("multi_section")
        # OCR often assigns a generic nearby paragraph as the table title even
        # though the first in-table banner explicitly says "revenue by product".
        # That banner is current-announcement evidence and must have the same
        # candidacy force as an external title; it is not a prior-data rescue.
        if any(REVENUE.search(marker["label"]) for marker in section_markers):
            layout.add("embedded_revenue_heading")
        prefixed_numeric = [row for row in identity_rows if re.match(
            r"^(?:其中[:：]?|[-–—·•]{1,3})", str(row[0] or "").strip()
        )]
        plain_numeric = [row for row in identity_rows if row not in prefixed_numeric]
        if len(prefixed_numeric) >= 2 and plain_numeric:
            layout.add("hierarchy")
        if any(row and (re.search(r"合計|合计|合共|總計|总计|total", str(row[0] or ""), re.I)
                        or not str(row[0] or "").strip() and any(NUMBER.match(str(cell or "")) for cell in row[1:])) for row in rows):
            layout.add("total_row")
        measurement = " ".join([header] + [str(cell or "") for row in rows for cell in row]
                               + [table.title, table.context])
        unit_tokens = tuple(dict.fromkeys(UNIT.findall(measurement)))
        current_unit = _unit_code(unit_tokens[0]) if unit_tokens else ""
        unit_continuity = (1 if stable_unit and current_unit == stable_unit else
                           -1 if stable_unit and current_unit and current_unit != stable_unit else 0)
        # A closed current monetary identity table remains meaningful when OCR
        # loses its nearby revenue heading.  This is current-table arithmetic,
        # never a comparison with prior amounts.
        if "total_row" in layout and len(identity_rows) >= 3 and current_unit:
            total_index = next((index for index in range(len(rows) - 1, -1, -1)
                                if rows[index]
                                and (not str(rows[index][0] or "").strip()
                                     or TOTAL_HEADER.search(str(rows[index][0] or "")))
                                and any(_number(cell) is not None
                                        for cell in rows[index][1:])), None)
            vectors = [
                tuple(_number(cell) for cell in row[1:])
                for row in rows[:total_index or 0]
                if row and str(row[0] or "").strip()
                and any(_number(cell) is not None for cell in row[1:])
            ]
            if total_index is not None and len(vectors) >= 2:
                total = tuple(_number(cell) for cell in rows[total_index][1:])
                closes = any(
                    total[col] is not None
                    and abs(sum((vector[col] or 0.0) for vector in vectors) - total[col])
                    <= max(1.0, abs(total[col]) * 1e-8)
                    for col in range(min([len(total)] + [len(vector) for vector in vectors]))
                )
                if closes:
                    layout.add("closed_monetary_identity_rows")
        revenue_relation = (
            "metric_row" if "revenue_metric_row" in layout else
            "metric_line" if "revenue_metric_line" in layout else
            "metric_column" if "revenue_metric_column" in layout else
            "embedded_heading" if "embedded_revenue_heading" in layout else
            "product_breakdown" if (
                "product_service_breakdown" in title_signals
                and "explicit_total_column" in layout
            ) else
            "identity_total" if (
                "closed_monetary_identity_rows" in layout and prior_hits >= 2
            ) else
            "title" if "revenue" in title_signals else ""
        )
        return TableEvidence(
            table=table, row_count=len(rows), column_count=width,
            title_signals=title_signals, layout_signals=layout,
            axis_signals=axis_signals,
            period_tokens=tuple(dict.fromkeys(PERIOD.findall(text))),
            currency_tokens=tuple(dict.fromkeys(CURRENCY.findall(measurement))),
            unit_tokens=unit_tokens,
            section_markers=section_markers,
            document_period_text=document_period_text,
            identity_axis=identity_axis,
            prior_axis=prior_axis,
            prior_row_hits=row_hits,
            prior_column_hits=column_hits,
            prior_identity_hits=prior_hits,
            prior_identity_strength=max(row_strength, column_strength),
            prior_matched_row_keys=identity_matched_current_keys(
                prior_names, row_identity_names
            ),
            prior_matched_column_keys=identity_matched_current_keys(
                prior_names, column_identity_names
            ),
            prior_identity_coverage=(prior_hits / len(prior_names) if prior_names else 0.0),
            unit_continuity=unit_continuity,
            revenue_relation=revenue_relation,
            current_identity_count=len(set(row_identity_names)),
            prior_fiscal_month_day=tuple(prior_fiscal_month_day or ()),
        )
