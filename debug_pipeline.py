#!/usr/bin/env python3
"""全链路 debug：候选表→选表→分类→提取，每步对比 GT。

Usage:
  python debug_pipeline.py AN202603201820676051
  python debug_pipeline.py AN202603201820676051 --pdf   # 含 PDF 重跑选表
"""
import json, os, sys, re, importlib
from collections import Counter

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# ── load data ──
with open(os.path.join(ROOT, "all_selected.json")) as f:
    ALL_SELECTED = json.load(f)
with open(os.path.join(ROOT, "tasks/HKCO_FN_PRODUCT/ground_truth.json")) as f:
    GT = json.load(f)
with open(os.path.join(ROOT, "tasks/HKCO_FN_PRODUCT/last_data.json")) as f:
    LP = json.load(f)

import custom.service.EAPS_HKCO_FN_PRODUCT_get_res as get_res
importlib.reload(get_res)

SEP = "=" * 80
SEP2 = "-" * 60


def debug(info, run_pdf=False):
    sel = ALL_SELECTED.get(info)
    if not sel:
        print(f"NOT FOUND in all_selected.json: {info}")
        return

    tbl = sel.get("target_table", [])
    title = str(sel.get("title", ""))
    page = sel.get("page_number", "?")
    lp_items = LP.get(info, [])
    gt_items = GT.get(info, [])

    print(f"\n{'='*80}")
    print(f"INFOCODE: {info}")
    print(f"Page: {page}  |  Table: {len(tbl)} rows × {max((len(r) for r in tbl), default=0)} cols")
    print(f"Title: {title[:200]}")
    print(f"LP ({len(lp_items)} items): {[(p.get('PRODUCTNAME',''), p.get('MBREVENUE','')) for p in lp_items[:8]]}")
    print(f"GT ({len(gt_items)} items):")
    for g in gt_items:
        print(f"  {g.get('PRODUCTNAME','')}  |  {g.get('MBREVENUE','')}  |  {g.get('STARTDATE','')}  |  {g.get('REPORTDATE','')}")

    # ─── 1. PRINT FULL TABLE ───
    print(f"\n{SEP}")
    print("1. TARGET TABLE (full)")
    print(SEP)
    for i, row in enumerate(tbl):
        # Truncate long cells
        display = []
        for c in row:
            s = str(c or "").replace("\n", "\\n")
            if len(s) > 60:
                s = s[:57] + "..."
            display.append(s)
        print(f"  row[{i:02d}]: {display}")

    # ─── 2. SPLIT HEADER / BODY ───
    print(f"\n{SEP}")
    print("2. HEADER / BODY SPLIT")
    print(SEP)
    hdr, body = get_res._split_header_body(tbl)
    print(f"Header ({len(hdr)} rows):")
    for i, r in enumerate(hdr):
        print(f"  hdr[{i}]: {r}")
    print(f"Body ({len(body)} rows):")
    for i, r in enumerate(body[:15]):
        print(f"  body[{i}]: col0=\"{r[0]}\" | rest={r[1:min(5,len(r))]}")
    if len(body) > 15:
        print(f"  ... ({len(body) - 15} more body rows)")

    # ─── 3. CLASSIFICATION ───
    print(f"\n{SEP}")
    print("3. CLASSIFICATION")
    print(SEP)

    # 3a. Structure signal
    bs, hs = get_res._table_structure_signal(tbl)
    print(f"\n3a. _table_structure_signal: body_score={bs}, hdr_score={hs}")

    # 3b. LP matching trace
    print(f"\n3b. LP matching in is_row_product:")
    names = [str(r.get("PRODUCTNAME", "")).strip()
             for r in lp_items
             if isinstance(r, dict) and str(r.get("PRODUCTNAME", "")).strip() != "合计"]
    rows_all = [r for r in tbl if isinstance(r, list) and len(r) > 0]

    row_hits = []
    col_hits = []
    for n in names:
        for ri, r in enumerate(rows_all):
            c0 = str(r[0] or "")
            has_amt = any(re.search(r'\d', str(c or "")) for c in r[1:])
            if n in c0 and not get_res._UNIT_RE.search(c0):
                row_hits.append((ri, n, c0[:60], has_amt))
        for ri, r in enumerate(rows_all[:6]):
            for ci in range(1, len(r)):
                cell = str(r[ci] or "")
                if n in cell:
                    col_hits.append((ri, ci, n, cell[:60]))

    print(f"  LP names: {names}")
    print(f"  Row hits ({len(row_hits)}):")
    for ri, n, c0, has_amt in row_hits:
        flag = " ← HAS AMT" if has_amt else " ← NO AMT (header row!)"
        print(f"    row[{ri}] col0=\"{c0}\" matched \"{n}\"{flag}")
    print(f"  Col hits ({len(col_hits)}):")
    for ri, ci, n, cell in col_hits:
        print(f"    row[{ri}] col{ci}=\"{cell}\" matched \"{n}\"")

    row_hit_count = len(row_hits)
    col_hit_count = len(col_hits)
    print(f"  row_hit={row_hit_count}, col_hit={col_hit_count} → ", end="")
    if row_hit_count > 0 or col_hit_count > 0:
        print(f"{'ROW-product' if row_hit_count >= col_hit_count else 'COL-product'}")
    else:
        print("LP inconclusive, fall through to structure")

    # 3c. Final classification
    is_row = get_res.is_row_product(tbl, lp_items)
    typ = get_res.classify_table(tbl, title, lp_items)
    print(f"\n3c. Final: is_row_product={is_row}, type=\"{typ}\"")

    # ─── 4. EXTRACTION ───
    print(f"\n{SEP}")
    print("4. EXTRACTION")
    print(SEP)

    # Show sub_metrics if detected
    sub = get_res._detect_subcolumn_metrics(tbl)
    if sub:
        print(f"  Sub-column metrics: {sub}")
        print(f"  Skip cols: {{c for c,label in sub.items() if get_res._should_skip_subcolumn(label)}}")

    # Run extraction
    reasons = []
    result = get_res.get_res(sel, info, reasons, "", lp_items)
    rows = result.get("target_res", [])
    print(f"  Reasons: {reasons}")
    print(f"  Extracted ({len(rows)} items):")
    for r in rows:
        print(f"    {r['product_name']:40s} | {r['mbrevenue']:>20s} | {r['start_date']} | {r['end_date']}")

    # ─── 5. COMPARISON ───
    print(f"\n{SEP}")
    print("5. GT vs EXTRACTED COMPARISON")
    print(SEP)

    ext_names = {r['product_name'] for r in rows}
    gt_names = {g.get('PRODUCTNAME', '') for g in gt_items}

    matched = ext_names & gt_names
    missing_names = gt_names - ext_names
    extra_names = ext_names - gt_names

    print(f"  Matched names: {len(matched)}")
    if matched:
        print(f"    {sorted(matched)}")
    print(f"  Missing from GT: {len(missing_names)}")
    if missing_names:
        print(f"    {sorted(missing_names)}")
    print(f"  Extra (not in GT): {len(extra_names)}")
    if extra_names:
        print(f"    {sorted(extra_names)}")

    # Value comparison for matched names
    print(f"\n  Value comparison (matched names):")
    for gn in sorted(matched):
        gt_vals = [(g.get('MBREVENUE',''), g.get('STARTDATE',''), g.get('REPORTDATE',''))
                   for g in gt_items if g.get('PRODUCTNAME','') == gn]
        ext_vals = [(r['mbrevenue'], r['start_date'], r['end_date'])
                    for r in rows if r['product_name'] == gn]
        print(f"    {gn}:")
        for gv in gt_vals:
            print(f"      GT: {gv}")
        for ev in ext_vals:
            print(f"      EXT: {ev}")

    # ─── 6. COST SECTION DETECTION ───
    print(f"\n{SEP}")
    print("6. COST SECTION / P&L DETECTION")
    print(SEP)
    has_cost = get_res._has_cost_section(tbl)
    print(f"  _has_cost_section: {has_cost}")
    if has_cost:
        trimmed = get_res._trim_cost_section(tbl)
        print(f"  Trimmed table: {len(tbl)} → {len(trimmed)} rows")

    # ─── 7. PDF re-run if requested ───
    if run_pdf:
        print(f"\n{SEP}")
        print("7. PDF FULL PIPELINE RE-RUN")
        print(SEP)
        pdf_path = os.path.join(ROOT, "pdf", f"{info}.pdf")
        if not os.path.exists(pdf_path):
            print(f"  PDF not found: {pdf_path}")
        else:
            from custom.service.EAPS_HKCO_FN_PRODUCT import extract_init
            result = extract_init(
                pdf_path, info, "debug",
                configs={"cache_dir": "parse_cache", "force_reparse": False, "pipeline_debug": True},
                task_info_list=None,
            )
            if isinstance(result, dict):
                data = result.get("data", {})
                pipe = data.get("pipeline", {}) if isinstance(data, dict) else {}
                records = data.get("records", []) if isinstance(data, dict) else []
                print(f"  Status: {result.get('status')}")
                print(f"  Pipeline stage: {pipe.get('stage', 'N/A')}")
                print(f"  Selected count: {pipe.get('selected_count', 0)}")
                print(f"  Source pages: {pipe.get('source_pages', [])}")
                print(f"  Records ({len(records)}):")
                for r in records[:20]:
                    print(f"    {r}")

    print(f"\n{'='*80}")
    print("DONE")
    print("=" * 80)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("infocode", nargs="?", default=None)
    ap.add_argument("--pdf", action="store_true", help="Also re-run full PDF pipeline")
    ap.add_argument("--batch", type=int, default=0, help="Run first N unforgiven cases")
    args = ap.parse_args()

    if args.batch:
        # Find top unforgiven cases
        import importlib
        import custom.service.EAPS_HKCO_FN_PRODUCT_get_res as m
        importlib.reload(m)
        # Find latest per_doc
        batch_dirs = sorted(os.listdir(os.path.join(ROOT, "batch_runs/HKCO_FN_PRODUCT")))
        latest = batch_dirs[-1]
        with open(os.path.join(ROOT, f"batch_runs/HKCO_FN_PRODUCT/{latest}/metrics/per_doc.json")) as f:
            per_doc = json.load(f)

        bad = []
        for row in per_doc['rows']:
            info = row['infocode']; stats = row['stats']
            if info not in ALL_SELECTED: continue
            typ = m.classify_table(ALL_SELECTED[info]['target_table'], ALL_SELECTED[info]['title'], LP.get(info, []))
            if stats.get('biz_hit', 0) != 1.0:
                score = stats.get('extra', 0) + stats.get('missing', 0)
                bad.append((info, score, typ))
        bad.sort(key=lambda x: -x[1])

        for info, score, typ in bad[:args.batch]:
            print(f"\n{'#'*80}")
            print(f"# BATCH [{bad.index((info,score,typ))+1}/{len(bad)}]: {info}  score={score}  type={typ}")
            print(f"{'#'*80}")
            debug(info, run_pdf=args.pdf)
    elif args.infocode:
        debug(args.infocode, run_pdf=args.pdf)
    else:
        ap.print_help()
