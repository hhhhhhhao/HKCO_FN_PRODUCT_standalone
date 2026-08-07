# HKCO 定位/选表 Debug 思路

## 1. 定位判定原则

- 定位只看 `selected_lines`，不要把 `related_inner_lines` 混进来。
- 定位只看 `MBREVENUE`，不看 `MBCOST`，也不看 `GROSS_PROFIT`。
- 最终字段比较仍然要比较 `GROSS_PROFIT`，只是定位阶段不看。
- 如果 GT 数值个数 > 4，且 >= 70% 出现在 `selected_lines`，也算定位成功。
- 如果 `selected_lines` 第一行包含“分部”，定位直接算成功。

## 2. 单个公告的 Debug 顺序

1. 先读 GT：产品名、期间、`MBREVENUE / MBCOST / GROSS_PROFIT`。
2. 跑当前选表：记录 `selected_pages`、`selected_first`、`from_full`、相关章节页。
3. 算 `missing_in_selected`，再看 `missing_in_all_lines`。
4. 在全文里搜缺失数值，确认它们实际在 PDF 哪一页。
5. 如果全文都搜不到，先确认 GT 数值和 PDF 是否来自同一份材料，不要急着改代码。
6. 如果数值在别的页，再看那个页所在的 GROUP 为什么没进候选。
7. 改完必须回归之前修过的公告，不能只看当前一篇。

## 3. 已确认的选表规则

### 章节拆分 `is_title_line`

- 行末以 3 位以上数字结尾的行不是标题，例如：
  - `分佔按權益法入賬之投資業績376`
- 标题一般在页面最左侧：
  - 当 `x0 > 200` 时，不判为标题
  - 表格里的行列标题通常 `x0` 较靠右

### 表格判断 `is_table`

- 列表编号 `(1)`、`(2)`、`(3)`、`(4)` 会被误认为数字，需要在 `is_table` 里用正则去掉：

```python
word['text'] = re.sub(r"[\(\（]\d[\)\）]", "", word['text'])
```

- 只删单位数枚举编号。
- `(15)`、`(123,456)`、`(2,516)` 是负金额，不能删。

### 章节过滤 `include_words`

- 不能只看 GROUP 的第一行。
- 如果第一行没有关键词，但整章文本包含收入/分部/分類等关键词，并且是表格，应该允许进入候选。

### 多个 full_history 候选并列

- 正文和真表都可能匹配上期产品名。
- 如果真表的标题有明显关键词，例如 `業務分部`，直接加到 `_TABLE_CLASS_PATTERNS` 最高优先级。
- 案例：`AN202503271648321354` 目标表是 `(a)業務分部(續)`，位于第 337 页；把 `業務分部` 加到第一个 pattern 后，程序会跳过前面模糊命中的正文 GROUP，直接选到真表。
- 不要为了当前案例去改候选排序或硬编码页码，先用表名关键词进表型优先级。

## 4. 改代码纪律

- 先定位问题，再改代码，不猜。
- 只改能解释规律的代码，不针对单个公告硬编码。
- 每个改动都要验证：
  - 当前公告 `missing=[]`
  - 之前修过的公告回归
  - `py_compile` 通过
- 不要为了当前案例通过，引入其它案例的误判。

## 5. 涉及文件

- `custom/service/HKCO_FN_PRODUCT_document.py`
- `custom/service/HKCO_FN_PRODUCT_selector.py`
- `custom/service/HKCO_FN_PRODUCT_utils.py`
- `run_backtest.py`
- `scripts/run_backtest.py`
