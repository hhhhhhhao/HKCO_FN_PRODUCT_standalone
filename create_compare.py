# -*- coding: utf-8 -*-
"""One clean HTML: type → card list, each card = table left + GT right."""
import json, os, re

import pathlib
BASE = pathlib.Path(__file__).parent
BATCH_DIR = str(BASE / "batch_runs" / "HKCO_FN_PRODUCT")
DEBUG_DIRS = sorted(
    os.path.join(BATCH_DIR, d, "debug")
    for d in os.listdir(BATCH_DIR)
    if os.path.isdir(os.path.join(BATCH_DIR, d, "debug"))
)
GT_FILE = str(BASE / "tasks" / "HKCO_FN_PRODUCT" / "ground_truth.json")
OUT = str(BASE / "table_gt_compare.html")

with open(GT_FILE, "r", encoding="utf-8") as f:
    gt_all = json.load(f)

def ts(v):
    s = str(v or "").replace("&","&amp;").replace("<","&lt;")
    return s

# ─── classify ───
def kw(title):
    t = (title or "")[:200]
    if not t: return "?"
    if re.search(r"損益|损益|利潤|利润|合併.*虧損|合并.*亏损", t): return "利润"
    if re.search(r"分部", t): return "分部"
    if re.search(r"利息", t): return "利息"
    return "收入"

def st(table):
    nr = len(table); nc = max((len(r) for r in table if isinstance(r,list)), default=0)
    if nc < 2 or nr < 2: return "未找到表"
    labs = []
    for r in table:
        if isinstance(r,list) and r: labs.append(str(r[0] or "").replace("\n"," ").strip())
    for i, lab in enumerate(labs):
        if re.search(r"^(收益|收入|營業額|营业额)$", lab):
            if i+1 < len(labs) and re.search(r"^(銷售成本|销售成本|服務成本|服务成本|直接成本|成本|费用)", labs[i+1]): return "P&L"
            if i+2 < len(labs) and re.search(r"^(毛利|毛損|毛损)", labs[i+2]): return "P&L"
    if any(l.startswith("-") or l.startswith("–") for l in labs): return "嵌套"
    return "行产品"

# ─── group all ───
groups = {}
seen = set()
# Reverse: newest batch runs first, skip older duplicates
for debug_dir in reversed(DEBUG_DIRS):
    for fn in sorted(os.listdir(debug_dir)):
        if not fn.endswith("_target_item.json"): continue
        ic = fn.replace("_target_item.json","")
        if ic in seen: continue
        seen.add(ic)
        with open(os.path.join(debug_dir, fn), "r", encoding="utf-8") as f: item = json.load(f)
        t = item.get("target_table", [])
        typ = f"{kw(item.get('title',''))}__{st(t)}"
        if typ not in groups: groups[typ] = []
        groups[typ].append((ic, item.get("title","")[:80], t, item.get("page_number","")))

# ─── html ───
def tbl(data, cls=""):
    nr = len(data); nc = max((len(r) for r in data if isinstance(r,list)), default=0)
    if nc < 2: return ""
    h = f"<table class='{cls}'><tr>"
    for j in range(nc): h += f"<th>C{j}</th>"
    h += "</tr>"
    limit = min(15, nr)
    for i in range(limit):
        row = data[i]
        if not isinstance(row,list) or not row: continue
        is_sum = (not row or not row[0]) or (row and row[0] and re.search(r"合計|合计|總計|总计|總額|总额", str(row[0] or "")))
        h += "<tr class='sum'>" if is_sum else "<tr>"
        for j in range(nc):
            v = row[j] if j < len(row) else ""
            s = ts(v)
            c = "c0" if j == 0 else "num" if re.search(r"\d", str(v).replace(",","").replace("(","").replace(")","").replace("-","")) else ""
            h += f"<td class='{c}'>{s}</td>"
        h += "</tr>"
    if nr > limit: h += f"<tr><td colspan='{nc}' style='color:#999;text-align:center'>... 共 {nr} 行，显示前 {limit} 行</td></tr>"
    h += "</table>"
    return h

def gt_tbl(rows):
    if not rows: return ""
    # deduplicate by product name
    seen = {}
    for r in rows:
        pn = str(r.get("PRODUCTNAME","")).strip()
        if pn not in seen: seen[pn] = r
    h = "<table class='gt'><tr><th>PRODUCTNAME</th><th>REVENUE</th><th>COST</th><th>GP</th><th>CUR</th><th>UNIT</th></tr>"
    for pn in sorted(seen):
        r = seen[pn]
        h += f"<tr><td class='c0'>{ts(pn)}</td><td class='num'>{r.get('MBREVENUE','')}</td><td class='num'>{r.get('MBCOST','')}</td><td class='num'>{r.get('GROSS_PROFIT','')}</td><td>{r.get('CURRENCY','')}</td><td>{r.get('UNIT','')}</td></tr>"
    h += "</table>"
    return h

css = """<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font:12px/1.4 system-ui;background:#f5f5f5;padding:16px 24px}
h1{font-size:18px;margin-bottom:16px}
h2{font-size:14px;margin:24px 0 8px;padding:6px 12px;background:#1a1a2e;color:#fff;border-radius:4px;cursor:pointer;position:sticky;top:0;z-index:10}
h2 .badge{background:#fff3;padding:1px 8px;border-radius:10px;font-weight:400;font-size:11px;float:right}
.section{margin-bottom:20px}
.section.folded{display:none}
.card{display:grid;grid-template-columns:minmax(300px,1fr) 400px;gap:12px;margin-bottom:12px;align-items:start}
.card .panel{background:#fff;border-radius:4px;box-shadow:0 1px 2px rgba(0,0,0,.08);overflow-x:auto;min-width:0}
.card .panel .head{padding:5px 10px;font-weight:600;font-size:10px}
.card .panel.table-panel .head{background:#e3f2fd;color:#0d47a1}
.card .panel.gt-panel .head{background:#e8f5e9;color:#1b5e20}
.card .panel .head .info{font-weight:400;color:#888}
.card .panel .body{padding:4px;overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:10px}
td,th{padding:3px 8px;text-align:left;border-bottom:1px solid #eee;white-space:nowrap}
th{font-weight:600;color:#666;font-size:9px;background:#fafafa}
tr:hover td{background:#f5f5f5}
.sum td{background:#fff9c4;font-weight:600}
.c0{width:auto;min-width:120px;max-width:220px;white-space:normal;word-break:keep-all;overflow-wrap:break-word}
.num{text-align:right;font-family:SF Mono,monospace;color:#1565c0}
.gt .num{color:#2e7d32}
.gt td{border-bottom:1px solid #e8f5e9}
</style>
<script>
function toggle(e){
  var s=e.nextElementSibling;
  s.classList.toggle('folded');
  e.textContent = (s.classList.contains('folded')?'▶ ':'▼ ') + e.textContent.replace(/^[▶▼] /,'');
}
</script>"""

def build():
    h = f"<html><head><meta charset='utf-8'><title>表格 vs GT</title>{css}</head><body>"
    h += '<h1>表格 vs GT — 全量对比 <span style="font-weight:400;font-size:12px;margin-left:16px"><a href="#" onclick="var ss=document.querySelectorAll(\'.section\');var o=ss[0].classList.contains(\'folded\');ss.forEach(function(s){o?s.classList.remove(\'folded\'):s.classList.add(\'folded\')});var hs=document.querySelectorAll(\'h2\');hs.forEach(function(h){h.textContent=(o?\'▼ \':\'▶ \')+h.textContent.replace(/^[▶▼] /,\'\')});return false" style="color:#1565c0">展开/折叠全部</a></span></h1>'

    for typ in sorted(groups, key=lambda x: -len(groups[x])):
        items = groups[typ]
        n = len(items)
        # Collect valid cards first
        cards_html = ""
        valid = 0
        for ic, title, table, pg in items:
            nr = len(table); nc = max((len(r) for r in table if isinstance(r,list)), default=0)
            gt = gt_all.get(ic, [])
            if nc < 2: continue
            valid += 1
            h += f"<div class='card'>"
            h += f"<div class='panel table-panel'><div class='head'><a href='https://pdf.dfcfw.com/pdf/H2_{ic}_1.pdf' target='_blank' style='color:#0d47a1;font-weight:700'>{ic}</a> &nbsp; <span class='info'>p.{pg} &nbsp; {nr}x{nc}</span><br><span class='info'>{ts(title)[:70]}</span></div>{tbl(table)}</div>"
            h += f"<div class='panel gt-panel'><div class='head'>GT &nbsp; <span class='info'>{len(gt)} 条</span></div>{gt_tbl(gt)}</div>"
            h += "</div>"
        h += "</div>"
    h += "</body></html>"
    return h

with open(OUT, "w", encoding="utf-8") as f:
    f.write(build())

print(f"Done → {OUT}")
