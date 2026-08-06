# 修复 HKCO_FN_PRODUCT 回测中 385 篇需修复文档

## 背景

港股主营业务收入产品分布表抽取系统。全量 1061 篇公告回测：676 Perfect (64%)，385 需修复。

报告：`batch_runs/HKCO_FN_PRODUCT/20260806_000547/report.html`

385 篇问题分解：
- **196 篇 empty_output**：pipeline 失败，0 输出
- **148 篇混合问题**：同时存在少抓+多抓+抓错
- **29 篇纯值不一致**，**8 篇纯漏产品**，**4 篇纯多抓**

目标：逐个排查、修复，最终全量 perfect。这套逻辑最终要跑 1w+ 公告，只改允许的文件。

---

## 核心原则：修规则，不修数据

这套逻辑最终要跑 1w+ 公告。**不要逐篇修到 perfect——要从问题文档中归纳缺失的规则/模式，把规则补上。** GT 只是回测验证手段，不是优化目标。过拟合到 1061 篇的 GT 对 1w+ 场景毫无意义。

正确的工作流：
1. 看一批问题文档，归纳出**共同模式**（比如：某种表头写法没被识别、某类章节标题没被切分）
2. 在允许的文件里**补一条规则**（加个正则、加个关键词、加个分类）
3. **跑全量回测**看这条规则修了多少篇、有没有误杀
4. 重复

错误的工作流（禁止）：
- 逐篇看、逐篇调、调完单篇就算过 ← 这是过拟合
- 为了让某篇 perfect 而加一条只适用于它的 hack

---

## 允许修改的文件

| 文件 | 允许的改动 |
|---|---|
| `custom/service/HKCO_FN_PRODUCT_document.py` | **只改正则和关键词**（章节切分逻辑） |
| `custom/service/HKCO_FN_PRODUCT_selector.py` | **只改正则和关键词**（选表逻辑） |
| `custom/service/HKCO_FN_PRODUCT_classifier.py` | **任意增删改**（表格分类，可分出新类型） |
| `custom/service/PDF_BASELINE_BACKTEST/` 下所有文件 | **任意改**（提取器逻辑） |
| `custom/service/HKCO_FN_PRODUCT_metric_enrichment.py` | **任意改**（指标补全） |
| `custom/service/EAPS_HKCO_FN_PRODUCT_format_data.py` | **任意改**（格式化输出） |

**不能改的文件**：`run_backtest.py`、`common.py`（提取公共函数）、`__init__.py`（合并逻辑）——这些已经修好了。

---

## 系统架构

```
PDF/JSON → get_lines → get_lines_grouped (切章)
         → select_main_table (选主表，可用 prior_names)
         → classify_main_inner (表格分类)
         → extract_main_table (三类提取器之一)
         → enrich_metrics (补成本/毛利)
         → format_records (格式化入库)
```

三类提取器在 `custom/service/PDF_BASELINE_BACKTEST/` 下：
- `product_in_columns.py` — 产品名列在纵轴
- `product_in_rows.py` — 产品名列在横轴
- `profit_loss.py` — 损益表

分类器在 `HKCO_FN_PRODUCT_classifier.py`，可以给新表型新增分类。

---

## 关键约束

1. **GT 只能用于回测验证，不能用于提取逻辑**。`select_main_table` 和 extractor 不能用 GT 数据。
2. **上期数据（prior_names / last_data.json）可以用**，这是生产环境能拿到的。
3. **只改允许的文件**，不要动 run_backtest.py、common.py、__init__.py。
4. **改完一个文档要跑回测验证**：`python run_backtest.py --task HKCO_FN_PRODUCT --infocode AN... --workers 1`
5. **不能硬做**——如果某个文档的问题根本不在允许修改的文件范围内（如 OCR 错误、GT 标注缺失），跳过。
6. **每次改动后跑全量或至少跑 30 篇 subset 确认不回归**。

---

## 排查一个文档的标准流程

```bash
# 1. 跑单篇回测
python run_backtest.py --task HKCO_FN_PRODUCT --infocode AN2025... --workers 1

# 2. 看中间结果
cat batch_runs/HKCO_FN_PRODUCT/<latest>/intermediates/AN2025..._extract.json

# 3. 调试提取器
python3 << 'EOF'
import json, sys; sys.path.insert(0, '.')
from custom.service.HKCO_FN_PRODUCT_utils import fullwidth_to_halfwidth
from custom.service.HKCO_FN_PRODUCT_document import get_lines_grouped
from custom.service.HKCO_FN_PRODUCT_selector import select_main_table
from custom.service.HKCO_FN_PRODUCT_classifier import classify_main_inner
from custom.service.HKCO_FN_PRODUCT_extraction import extract_main_table
from custom.service.HKCO_FN_PRODUCT_extraction.common import _rows

# 加载文档...
# 选中表...
# 看原始表格结构和提取结果...
EOF
```

---

## 已知已修复的问题（不要再改）

- CJK 异体字归一化（淨→净）
- subtotal 正则补充（分部小計、收入總額等）
- product_in_columns：合计验证+子集搜索过滤非产品行
- product_in_columns：多段列同年去重取最后一列
- product_in_columns：空名多数字行识别为总计
- product_in_rows：label_row 补齐对齐 metric_row
- _merge_facts：两张表合计差>1%不合并
- find_section_break：子标题行（- 前缀）不算截断
- get_currency：裸 $ 默认港元

---

## 如何从问题文档归纳规则

不要逐篇打开看。第一步先做聚合分析：

```bash
# 找出所有 empty_output 文档的 pipeline 阶段和分类
python3 -c "
import json, os, glob
D = sorted(glob.glob('batch_runs/HKCO_FN_PRODUCT/*/'))[-1]
for f in os.listdir(D+'/intermediates'):
    if not f.endswith('_extract.json'): continue
    data = json.load(open(D+'/intermediates/'+f))
    debug = data['data']['debug']
    stage = debug.get('stage','?')
    msg = debug.get('message','?')
    clf = debug.get('classification','?')
    if stage != 'success':
        print(f'{f[:25]}: {stage} | {msg} | {clf}')
" | sort | uniq -c | sort -rn
```

归纳出最常见的失败模式后，针对每一类模式看几个样本、提炼规则。改完一条规则重新跑全量看改善幅度。

## 优先级建议

1. **先看 196 篇 empty_output 的聚合分布**，找出占比最高的失败模式，一条规则可能修几十篇
2. **再看混合问题的共同特征**
3. **纯值差异/漏产品/多抓量少、优先级低**
