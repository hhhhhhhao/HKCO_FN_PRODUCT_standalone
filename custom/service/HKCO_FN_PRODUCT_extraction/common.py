# -*- coding: utf-8 -*-
"""三类抽取共用的最小工具函数。"""
from __future__ import annotations

import calendar
import datetime
import re
from typing import Any, Dict, List, Optional


COST = re.compile(r"成本|cost", re.I)
GROSS_PROFIT = re.compile(r"毛利|毛損|毛损|gross profit|gross loss", re.I)
HEADER_SCAN_ROWS = 5
CN = {"〇": 0, "零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
      "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

PATTERNS = {
    "number": r"^(?P<number>\(?-?[\d,]+(?:\.\d+)?\)?)$",
    "year": r"(?P<year>20\d{2}|二零[〇零一二三四五六七八九]{2}|二〇[〇零一二三四五六七八九]{2})",
    "revenue": r"(?P<revenue>收入|收益|營業額|营业额|銷售額|销售额|銷售|销售|revenue|turnover|sales)",
    "revenue_label": r"^(?P<label>收入|收益|營業額|營業收入|銷售收入|銷售額|revenue|turnover|sales)$",
    "pl_line": r"(?P<pl>成本|毛利|溢利|虧損|損失|開支|費用|profit|loss)",
    "metric": r"^(?P<metric>分部業績|分部收益|毛利|成本|溢利|虧損|開支|費用|折舊|利息收入|profit|loss|expense|subtotal|total)$",
    "metric_contains": r"(?P<metric>分部業績|分部收益|毛利|成本|溢利|虧損|損失|開支|費用|折舊|利息收入|EBITDA|EBIT|非流動資產|流動資產|股東應佔|第三方|關聯方|銷售產品|提供服務|profit|loss|expense|subtotal|total)",
    "external": r"(?P<external>外部|對外|对外|external)",
    "header": r"(?P<header>20\d{2}|二零[〇零一二三四五六七八九]{2}|二〇[〇零一二三四五六七八九]{2}|千元|千港元|百萬|百万|期間|期间|附註|附注|年度|止年度|止六個月|止六个月)",
    "recognition": r"(?P<recognition>時間點|時點|时间点|隨時間|随时间|一段時間|一段时间|某一時點|某一时点|某個時點|某个时点)",
    "note": r"^(?P<note>附註|附注|註|注|note)",
    "cjk": r"(?P<cjk>[\u4e00-\u9fff])",
    "final_total": r"^(?P<final>合計|合计|總計|总计|總額|总额|總收入|总收入|總收益|总收益|淨收益總額|净收益总额|收益總額|收入總額|銷售淨額|销售净额|總營業額|总营业额|集團總額|集团总额|綜合|综合|本集團|本集团|集團|集团|合併|合并|total)$",
    "subtotal": r"^(?P<sub>可報告分部總計|可報告分部总计|可呈報分部總計|可呈報分部总计|須予報告分部小計|应予报告分部小计|應呈報分類總計|應呈報分類总计|持續經營分類總計|持续经营分类总计|報告分部總計|报告分部总计|分部總計|分部总计|分部總額|分部总额|分部小計|分部小计|擔保費收益總額|担保费收益总额|擔保費收益淨額|担保费收益净额|收益總額|收益淨額|收益净额|收入總額|收入总额|銷售產品|销售产品|提供服務|提供服务|主要地區收益總額|主要地区收益总额|來自客户合約的收益|來自客戶合約的收益|來自客户合約的收入|來自客戶合約的收入|來自客户合約之總收入|來自客戶合約之總收入|按客户類型劃分的收益總額|按客戶類型劃分的收益總額|第三方|關聯方|可呈報分部|可報告分部|小計|小计|subtotal|sub-total)$",
}


def match_named_patterns(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, str(text or ""), re.I)
        if match and match.groupdict():
            return {key: value for key, value in match.groupdict().items() if value is not None}
    return None


def match_patterns(s, patterns):
    for pattern, group in patterns:
        match = re.search(pattern, str(s or ""), re.I)
        if match and match.group(group):
            return match.group(group)
    return None


# CJK 异体字归一化：MinerU OCR 输出可能混用多个 Unicode 码点表示的"同一个字"，
# 在正则匹配前统一折叠到正则中已覆盖的字符，避免漏匹配。
_CJK_NORM = str.maketrans({
    "凈": "淨",   # 净的异体 (U+51C8 → U+6DE8)
})


def _matches(name: str, text: str) -> bool:
    """正则匹配（归一化 CJK 异体字后）。"""
    return match_patterns(text.translate(_CJK_NORM), [(PATTERNS[name], 1)]) is not None


def get_currency(arr):
    units = ['人民币','人民幣','港元','港幣','美元','欧元','歐元','日元','日圓','新加坡元','新元','加拿大元','马来西亚林吉特','馬來西亞林吉特','澳门元','澳門元']
    aliases = {'人民幣': '人民币', '港幣': '港元', '港币': '港元', '歐元': '欧元', '日圓': '日元', '新加坡元': '新加坡元', '新元': '新加坡元', '馬來西亞林吉特': '马来西亚林吉特', '澳門元': '澳门元'}
    for a in arr:
        if 'HK$' in a or 'HK＄' in a:
            return '港元'
        if 'RMB' in a or 'CNY' in a or 'RMB＄' in a:
            return '人民币'
        if 'US$' in a or 'USD' in a or 'US＄' in a:
            return '美元'
        for unit in units:
            if unit in a:
                return aliases.get(unit, unit)
    # 裸 $：港股公告中 $ 默认为港元
    for a in arr:
        if '$' in a or '＄' in a:
            return '港元'
    return ''


def get_unit(arr):
    units = ['千港元','千新元','千新加坡元','千元','千美元','千人民币','百萬','百万','亿元','億元','万元','萬元','萬','万','元','million','thousand']
    for a in arr:
        if re.search(r"\$\s*[']?000|[']000", a):
            return '千元'
        for unit in units:
            if unit in a:
                return unit
    return ''


def _cn_number(token):
    token = str(token or "").replace("○", "零")
    if token.isdigit():
        return int(token)
    if "十" in token:
        left, right = token.split("十", 1)
        tens = CN.get(left, 1) if left else 1
        units = CN.get(right, 0) if right else 0
        return tens * 10 + units
    return CN.get(token, 0)


def get_end_date(text_line):
    text_line = text_line.replace(" ", "").replace("\n", "").replace("\t", "")
    patterns = [
        r"(?P<year>二零[〇零一二三四五六七八九]{2}|二〇[〇零一二三四五六七八九]{2})年(?P<month>[〇零一二三四五六七八九十]{1,3})月(?P<day>[〇零一二三四五六七八九十]{1,3})日",
        r"(?P<year>\d{4})年(?P<month>\d{1,2})月",
        r"(?P<year>\d{4})年(?P<month>\d{1,2})末",
        r"(?P<year>\d{4})年(?P<season>第[一二三四1234]季度)",
        r"(?P<year>\d{4})年(?P<season>[一二三四1234]季度)",
        r"(?P<year>\d{4})年\d{1,2}\-(?P<season>\d{1,2}季度)",
        r"(?P<year>\d{4})年(?P<previous_season>前[一二三四1234]季度)",
        r"(?P<year>\d{4})年(?P<half_year>半年度|上半年)",
        r"(?P<year>\d{4})(?P<annual>年度|年部分经营数据|年公司境内|年经营)",
        r"(?P<year>\d{4})年(?P<month_range>\d{1,2}-\d{1,2})月",
        r"(?P<year>\d{4})年(?P<annual>年度)",
        r"(?P<year>\d{4})年(?P<annual>年末|末)",
        r"(?P<year>\d{4})(?P<annual>年)(共同|发行)",
    ]
    match_res = match_named_patterns(text_line, patterns)
    if match_res:
        year = match_res.get("year")
        season = match_res.get("season")
        month = match_res.get("month")
        half_year = match_res.get("half_year")
        annual = match_res.get("annual")
        previous_season = match_res.get("previous_season")
        month_range = match_res.get("month_range")
        last_month = match_res.get("last_month")
        day = None
        month = _cn_number(month) if month and not str(month).isdigit() else month
        day = _cn_number(day) if day and not str(day).isdigit() else day
        if year and not str(year).isdigit():
            year = 2000 + int("".join(str(CN.get(ch, 0)) for ch in year[2:]))

        if month and day is not None:
            return f"{year}-{int(month):02d}-{int(day):02d}"

        if season:
            if "一季度" in season or "1季度" in season:
                month = "03"
                day = "31"
            elif "二季度" in season or "2季度" in season:
                month = "06"
                day = "30"
            elif "三季度" in season or "3季度" in season:
                month = "09"
                day = "30"
            elif "四季度" in season or "4季度" in season:
                month = "12"
                day = "31"
        elif previous_season:
            if "四季度" in previous_season or "4季度" in previous_season:
                month = "12"
                day = "31"
            elif "三季度" in previous_season or "3季度" in previous_season:
                month = "09"
                day = "30"
            elif "二季度" in previous_season or "2季度" in previous_season:
                month = "06"
                day = "30"
            elif "一季度" in previous_season or "1季度" in previous_season:
                month = "03"
                day = "31"
        elif half_year:
            month = "06"
            day = "30"
        elif annual:
            month = "12"
            day = "31"
        elif month:
            month = f"{int(month):02d}"
            if month in ["01", "03", "05", "07", "08", "10", "12"]:
                day = "31"
            elif month in ["04", "06", "09", "11"]:
                day = "30"
            elif month == "02":
                day = "29" if (int(year) % 4 == 0 and (int(year) % 100 != 0 or int(year) % 400 == 0)) else "28"
        elif month_range:
            month = month_range.split('-')[1]
            month = f"{int(month):02d}"
            if month in ["01", "03", "05", "07", "08", "10", "12"]:
                day = "31"
            elif month in ["04", "06", "09", "11"]:
                day = "30"
            elif month == "02":
                day = "29" if (int(year) % 4 == 0 and (int(year) % 100 != 0 or int(year) % 400 == 0)) else "28"

        return f"{year}-{month}-{day}"

    return None


def get_start_date(start_date, report_date, this_year, text=""):
    if start_date:
        return start_date
    if report_date:
        end_date = datetime.date.fromisoformat(report_date)
        if re.search(r"六個月|六个月|6\s*個月|6\s*个月|six months", text, re.I):
            months = 6
        elif re.search(r"九個月|九个月|9\s*個月|9\s*个月|nine months", text, re.I):
            months = 9
        elif re.search(r"季度", text):
            months = 3
        else:
            months = 12
        start = _shift_months(end_date, -months) + datetime.timedelta(days=1)
        return start.isoformat()
    year = str(this_year).replace('年', '')
    return f"{year}-01-01"


def _shift_months(value, months):
    month = value.month + months
    year = value.year
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return datetime.date(year, month, day)




def _number(value: Any) -> Optional[float]:
    result = match_named_patterns(value, [PATTERNS["number"]])
    if not result:
        return None
    text = result["number"]
    negative = text.startswith("(") and text.endswith(")")
    amount = float(text.strip("() ").replace(",", ""))
    return -amount if negative else amount


def _year(value: Any) -> Optional[int]:
    result = match_named_patterns(value, [PATTERNS["year"]])
    if not result:
        return None
    token = result["year"]
    if token.isdigit():
        return int(token)
    return 2000 + int("".join(str(CN.get(ch, 0)) for ch in token[2:]))


def _rows(table: Dict[str, Any]) -> List[List[Any]]:
    return [list(row) for row in table.get("table", []) if isinstance(row, (list, tuple))]


def _column_header(rows: List[List[Any]], width: int, column: int) -> str:
    values = []
    for row in rows[:HEADER_SCAN_ROWS]:
        source = column - max(0, width - len(row))
        if 0 <= source < len(row):
            values.append(str(row[source] or ""))
    return " ".join(values)


def _clean_name(value: Any) -> str:
    return re.sub(r"^(?:其中[:：]?\s*|[-–—·•]{1,3}\s*)", "", str(value or "").strip())


def _name_overlap(name, prior_names) -> bool:
    name_key = name.replace(" ", "")
    for prior in prior_names or ():
        prior_key = prior.replace(" ", "")
        if prior_key and (name_key in prior_key or prior_key in name_key):
            return True
    return False


def _is_header_row(row) -> bool:
    text = " ".join(str(cell or "") for cell in row)
    return _matches("header", text)


def _clean_header(value: Any) -> str:
    text = str(value or "").replace("\n", " ")
    text = re.sub(r"20\d{2}|二零[〇零一二三四五六七八九]{2}|二〇[〇零一二三四五六七八九]{2}", "", text)
    text = re.sub(r"人民幣|人民币|港幣|港币|港元|美元|歐元|欧元|日圓|日元", "", text)
    text = re.sub(r"百萬|百万|萬|万|千|元|million|thousand", "", text, flags=re.I)
    text = re.sub(r"未經審核|未审核|未經審計|未审计|unaudited", "", text, flags=re.I)
    return re.sub(r"[()（）\s]+", "", text)


def _label_kind(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.rstrip(":：")
    text = re.sub(r"截至.*止(?:六個月|六个月|九個月|九个月|年度|年).*$", "", text).strip()
    if _matches("subtotal", text):
        return "subtotal"
    if _matches("final_total", text):
        return "final"
    return None


def find_section_break(rows: List[List[str]], prior_names=()) -> int:
    """找到第一个「像新子表标题」的行：第一列有文字、其余列全无数字、且不在
    prior_names 里。返回该行下标，后续行视为另一个子表，不再抽取。
    找不到则返回 len(rows) 表示整张表都可用。
    prior_names 为空时不截断（没有上期数据无法判断安全截断点）。
    """
    if not prior_names:
        return len(rows)
    data_started = False
    for ri, row in enumerate(rows):
        if not row:
            continue
        name = _clean_name(row[0])
        # 还没看到第一个数据行之前，不触发截断（跳过表头区域）
        if not data_started:
            if name and any(_number(cell) is not None for cell in row[1:]):
                data_started = True
            continue
        # 子标题/子分类行（带 - 前缀或含收入/收益/成本/费用等业务关键词）
        # 不是 section break，不截断
        raw = str(row[0] or "").strip()
        is_sub_header = raw.startswith("-") or raw.startswith("–") or raw.startswith("—") or _matches("revenue", name) or _matches("pl_line", name)
        # 第一列有文字 + 其余列全无数字 + 不是上期产品名 + 不是子标题 → 截断点
        if name and not _name_overlap(name, prior_names) and not is_sub_header:
            if all(_number(cell) is None for cell in row[1:]):
                return ri
    return len(rows)


def _fact(table: Dict[str, Any], name: str, amount: float, start: str, end: str,
          currency: str, unit: str) -> Dict[str, Any]:
    return {
        "table_id": table.get("id", ""),
        "metric": "MBREVENUE",
        "product_name": name,
        "amount": amount,
        "start_date": start,
        "end_date": end,
        "currency": currency,
        "unit": unit,
    }
