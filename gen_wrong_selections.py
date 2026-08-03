#!/usr/bin/env python3
"""Generate wrong_selections.html from candidate JSON files."""
import json, re
from pathlib import Path

ROOT = Path(__file__).parent
GT = json.loads((ROOT/'tasks/HKCO_FN_PRODUCT/ground_truth.json').read_text('utf-8'))

def amount_set(tbl):
    nums = set()
    for n in re.findall(r'[\d,]+(?:\.\d+)?', json.dumps(tbl, ensure_ascii=False)):
        try: nums.add(int(float(n.replace(',',''))))
        except: pass
    return nums

def all_batch_dirs():
    base = ROOT/'batch_runs/HKCO_FN_PRODUCT'
    return sorted([d for d in base.iterdir() if d.is_dir() and (d/'debug').exists()])

def latest_batch():
    return all_batch_dirs()[-1]

BATCH = latest_batch()
DEBUG = BATCH/'debug'
print(f'Using {BATCH.name}')

def to_arr(tbl):
    """Ensure 2D list format."""
    if not isinstance(tbl, list): return []
    return [[str(c or '') for c in (r if isinstance(r, list) else [str(r)])] for r in tbl]

# Find the actual target_item (not candidate) for each infocode
# target_item files are named {ic}_target_item.json
# candidate files are named {ic}_candidate_N_target_item.json
target_items = {}  # ic -> {json data}
candidates = {}    # ic -> [{json data}, ...]

for f in sorted(DEBUG.iterdir()):
    name = f.name
    if not name.endswith('_target_item.json'): continue
    if name.startswith('.'): continue

    if '_candidate_' in name:
        # Extract ic: everything before _candidate_
        ic = name[:name.index('_candidate_')]
        try:
            data = json.loads(f.read_text('utf-8'))
            candidates.setdefault(ic, []).append(data)
        except: pass
    else:
        ic = name.replace('_target_item.json', '')
        if re.match(r'^AN\d{18}$', ic):
            try:
                data = json.loads(f.read_text('utf-8'))
                target_items[ic] = data
            except: pass

print(f'Target items: {len(target_items)}, Infocodes with candidates: {len(candidates)}')

cases = []
for ic, sel in target_items.items():
    gt = GT.get(ic, [])
    real = [g for g in gt if (g.get('PRODUCTNAME') or '').strip() != '合计']
    if not real: continue
    gtn = set(int(g['MBREVENUE']) for g in real if g.get('MBREVENUE'))
    total = len(gtn)
    if not gtn: continue

    sel_tbl = sel.get('target_table', [])
    sel_nums = amount_set(sel_tbl)
    if gtn <= sel_nums: continue  # already correct

    cands = candidates.get(ic, [])
    if not cands: continue

    # Check if any candidate has all GT
    has_correct = False
    for c in cands:
        c_nums = amount_set(c.get('target_table', []))
        if gtn <= c_nums: has_correct = True; break
    if not has_correct: continue

    # Build table info
    table_info = []
    for ci, c in enumerate(cands):
        tbl = c.get('target_table', [])
        c_nums = amount_set(tbl)
        match = len(gtn & c_nums) if gtn else 0
        is_sel = (sel.get('title','') == c.get('title','') and
                  sel.get('page_number') == c.get('page_number'))
        pg = c.get('page_number', '?')
        title = c.get('title', '') or ''
        arr = to_arr(tbl)
        table_info.append({
            'idx': ci, 'pg': pg, 'title': title[:150],
            'rows': len(arr), 'match': match,
            'arr': arr, 'sel': is_sel
        })

    # Sort: selected first, then GT full
    table_info.sort(key=lambda t: (not t['sel'], -(t['match'])))
    cases.append({
        'ic': ic, 'gt': real, 'total': total,
        'tables': table_info
    })

cases.sort(key=lambda x: x['ic'])
print(f'Wrong cases: {len(cases)}')

# Build HTML
h = '<html><head><meta charset="utf-8"><title>Wrong Selections</title><style>'
h += 'body{font:12px/1.3 monospace;margin:10px;background:#f5f5f5} '
h += '.card{border:1px solid #ccc;margin:10px 0;padding:10px;background:#fff;border-left:4px solid #f44336} '
h += '.tbl{border:1px solid #ddd;margin:4px 0;padding:4px;background:#fff} '
h += '.tbl-sel{border:2px solid #2196f3} .tbl-ok{border:2px solid #4caf50} '
h += 'table{border-collapse:collapse;font-size:11px;width:100%} '
h += 'td,th{border:1px solid #eee;padding:1px 5px;text-align:left;max-width:250px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis} '
h += '.t-sel{background:#2196f3;color:#fff;padding:0 5px;border-radius:2px;font-size:10px;margin:0 2px} '
h += '.t-ok{background:#4caf50;color:#fff;padding:0 5px;border-radius:2px;font-size:10px;margin:0 2px} '
h += '.chap{color:#e65100;font-size:10px;margin:1px 0} .gt{color:#1565c0;margin:4px 0} h3{margin:0;font-size:13px} '
h += '</style></head><body><h2>{} Wrong Selections</h2>'.format(len(cases))

for it in cases:
    sel_count = sum(1 for t in it['tables'] if t['sel'])
    ok_count = sum(1 for t in it['tables'] if t['match'] == it['total'] > 0)
    h += f'<div class="card"><h3>{it["ic"]} 已选:{sel_count} GT全含:{ok_count}/{len(it["tables"])}</h3>'
    h += f'<div class="gt"><b>GT:</b> {[(g["PRODUCTNAME"][:35],g["MBREVENUE"]) for g in it["gt"][:6]]}</div>'
    for t in it['tables']:
        cls = ''
        tags = ''
        if t['sel']: cls += ' tbl-sel'; tags += '<span class="t-sel">★已选</span>'
        if t['match'] == it['total'] > 0: cls += ' tbl-ok'; tags += '<span class="t-ok">GT全含</span>'
        h += f'<div class="tbl{cls}"><div>候选{t["idx"]} p.{t["pg"]} ({t["rows"]}行) [{t["match"]}/{it["total"]}GT] {tags}</div>'
        h += f'<div class="chap">章节: {t["title"]}</div>'
        h += '<table>'
        for row in t['arr'][:15]:
            h += '<tr>' + ''.join(f'<td>{c}</td>' for c in row[:6]) + '</tr>'
        h += '</table></div>'
    h += '</div>'

h += '</body></html>'
out = ROOT / 'wrong_selections.html'
out.write_text(h, 'utf-8')
print(f'Saved: {out}')
