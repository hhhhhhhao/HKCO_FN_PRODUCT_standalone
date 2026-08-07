#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全流程回测：定位 + 抽取 + 格式化，对比 GT 金额判断定位是否正确。"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def backtest_one(task):
    code, pdf_path, gt_records = task
    try:
        from custom.service.EAPS_HKCO_FN_PRODUCT import extract_init
        result = extract_init(
            pdf_path=str(pdf_path),
            info_code=code,
            request_id="backtest",
            configs={"debug_enabled": False},
            task_info_list=None,
        )
        if not isinstance(result, dict):
            return {"code": code, "status": "error", "error": "extract_init 返回非 dict"}
        records = result.get("data", {}).get("records", [])
        debug = result.get("data", {}).get("debug", {})
        return {
            "code": code,
            "status": result.get("status", "unknown"),
            "stage": debug.get("stage", ""),
            "message": debug.get("message", ""),
            "source_pages": debug.get("source_pages", []),
            "records": records,
            "selected_lines": debug.get("selected_lines", []),
            "error": result.get("error_message", ""),
        }
    except Exception as exc:
        return {"code": code, "status": "error", "error": str(exc)}


def _amount_key(name):
    """产品名归一化，用于 GT 与抽取结果匹配。"""
    return re.sub(r"\s+", "", str(name or "")).lower()


def _parse_amount(val):
    """安全解析金额。"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def compare_records(extracted, gt_records):
    """对比抽取结果与 GT。

    返回 (missing_products, wrong_amounts) — GT 中未被覆盖的产品及金额不一致项。
    """
    # 按产品名建 GT 索引
    gt_by_name = {}
    for r in gt_records:
        name = str(r.get("PRODUCTNAME") or "").strip()
        if not name or name in {"合计", "合計"}:
            continue
        key = _amount_key(name)
        gt_by_name[key] = {
            "name": name,
            "revenue": _parse_amount(r.get("MBREVENUE")),
            "cost": _parse_amount(r.get("MBCOST")),
            "gross_profit": _parse_amount(r.get("GROSS_PROFIT")),
        }

    # 抽取结果按产品名建索引
    ext_by_name = {}
    for r in extracted:
        name = str(r.get("PRODUCTNAME") or "").strip()
        if not name:
            continue
        key = _amount_key(name)
        ext_by_name[key] = {
            "name": name,
            "revenue": _parse_amount(r.get("MBREVENUE")),
            "cost": _parse_amount(r.get("MBCOST")),
            "gross_profit": _parse_amount(r.get("GROSS_PROFIT")),
        }

    missing_products = []
    wrong_amounts = []
    matched = 0

    for gt_key, gt_item in gt_by_name.items():
        ext_item = ext_by_name.get(gt_key)
        if ext_item is None:
            missing_products.append(gt_item["name"])
            # 模糊匹配
            for ek, ev in ext_by_name.items():
                if gt_key in ek or ek in gt_key:
                    missing_products[-1] += f"~{ev['name']}"
                    ext_item = ev
                    break
            if ext_item is None:
                continue

        matched += 1
        for field in ("revenue", "cost", "gross_profit"):
            gt_val = gt_item[field]
            ext_val = ext_item.get(field)
            if gt_val is not None:
                if ext_val is None:
                    wrong_amounts.append(f"{gt_item['name']}.{field}: GT={gt_val} ext=None")
                elif abs(gt_val - ext_val) > 0.01 and abs(gt_val) > 0.01:
                    wrong_amounts.append(f"{gt_item['name']}.{field}: GT={gt_val} ext={ext_val}")

    return missing_products, wrong_amounts, matched, len(gt_by_name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 个")
    args = parser.parse_args()

    from custom.service.HKCO_FN_PRODUCT_utils import missing_gt_values_in_selected_lines

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "batch_runs" / "HKCO_FN_PRODUCT" / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    gt = json.loads(
        (ROOT / "tasks" / "HKCO_FN_PRODUCT" / "ground_truth.json").read_text(encoding="utf-8")
    )

    tasks = []
    skipped = 0
    for code in sorted(gt):
        pdf_path = ROOT / "pdf_json" / f"{code}.pdf"
        if not pdf_path.is_file():
            skipped += 1
            continue
        gt_records = gt[code]
        tasks.append((code, pdf_path, gt_records))

    if args.limit:
        tasks = tasks[: args.limit]

    print(f"GT 条目: {len(gt)}, 跳过无 PDF: {skipped}, 待跑: {len(tasks)}")

    if args.workers <= 1 or len(tasks) <= 1:
        results = [backtest_one(t) for t in tasks]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            results = list(executor.map(backtest_one, tasks, chunksize=4))

    # 定位只看 GT 数值是否全部出现在 select_main_table 返回的选中行里。
    locate_wrong = []  # GT 数值未全部命中选中行 → 定位错误
    extract_wrong = []  # 定位正确但抽取有误
    fully_ok = []  # 定位正确 + 抽取正确
    other_wrong = []  # 异常/报错
    detail = {}

    for result in results:
        code = result["code"]
        status = result["status"]
        stage = result.get("stage", "")
        message = result.get("message", "")
        pages = result.get("source_pages", [])
        records = result.get("records", [])
        err = result.get("error", "")
        selected_lines = result.get("selected_lines", [])
        gt_records = gt.get(code, [])
        missing_loc_values = missing_gt_values_in_selected_lines(gt_records, selected_lines)

        detail[code] = {
            "status": status,
            "stage": stage,
            "message": message,
            "source_pages": pages,
            "record_count": len(records),
            "locate_ok": not missing_loc_values,
            "missing_loc_values": missing_loc_values[:20],
        }

        if status == "error" or err:
            other_wrong.append((code, status, stage, pages, f"status={status} msg={message} err={err[:100]}"))
            continue

        if missing_loc_values:
            locate_wrong.append((
                code,
                pages,
                f"GT数值缺失={len(missing_loc_values)} missing={missing_loc_values[:5]}",
            ))
            continue

        if status != "success":
            extract_wrong.append((
                code,
                pages,
                f"定位正确但{stage}: msg={message} err={err[:100]}",
            ))
            continue

        missing, wrong_amts, matched, gt_total = compare_records(records, gt_records)

        detail[code]["gt_total"] = gt_total
        detail[code]["matched"] = matched
        detail[code]["missing_products"] = missing
        detail[code]["wrong_amounts"] = wrong_amts

        if missing or wrong_amts:
            extract_wrong.append((code, pages, f"matched={matched}/{gt_total} missing={missing[:5]} wrong_amt={wrong_amts[:3]}"))
        else:
            fully_ok.append((code, pages))

    # 写汇总
    lines = []
    lines.append(f"全流程回测汇总")
    lines.append(f"总任务数: {len(results)}")
    lines.append(f"定位正确+抽取正确: {len(fully_ok)}")
    lines.append(f"定位正确+抽取有误: {len(extract_wrong)}")
    lines.append(f"定位错误(GT数值未命中选中行): {len(locate_wrong)}")
    lines.append(f"异常/报错: {len(other_wrong)}")

    if locate_wrong:
        lines.append(f"\n--- 定位错误（GT数值未全部出现在选中行，共{len(locate_wrong)}）---")
        for code, pages, detail_str in locate_wrong:
            lines.append(f"  {code}  pages={pages}  {detail_str}")

    if extract_wrong:
        lines.append(f"\n--- 定位正确但抽取有误（{len(extract_wrong)}）---")
        for code, pages, detail_str in sorted(extract_wrong)[:30]:
            lines.append(f"  {code}  pages={pages}  {detail_str}")
        if len(extract_wrong) > 30:
            lines.append(f"  ... 还有 {len(extract_wrong)-30} 个")

    if other_wrong:
        lines.append(f"\n--- 异常/报错（{len(other_wrong)}）---")
        for code, status, stage, pages, detail_str in other_wrong:
            lines.append(f"  {code}  pages={pages}  {detail_str}")

    # 写 wrong_table_selection_codes.txt
    loc_wrong_path = ROOT / "analysis" / "HKCO_FN_PRODUCT" / "wrong_table_selection_codes.txt"
    loc_codes = set(c for c, _, _ in locate_wrong)
    loc_wrong_path.parent.mkdir(parents=True, exist_ok=True)
    loc_wrong_path.write_text(
        "\n".join([f"# 定位错误 {len(loc_codes)}"] + sorted(loc_codes)) + "\n"
    )

    # 写 wrong_selected_pages.tsv
    tsv_path = ROOT / "analysis" / "HKCO_FN_PRODUCT" / "wrong_selected_pages.tsv"
    tsv_lines = ["infocode\tselected_pages\tselected_line_count\treason"]
    for code, status, stage, pages, detail_str in other_wrong:
        tsv_lines.append(f"{code}\t{'|'.join(str(p) for p in pages)}\t0\t{stage}: {detail_str}")
    for code, pages, detail_str in locate_wrong:
        tsv_lines.append(f"{code}\t{'|'.join(str(p) for p in pages)}\t0\tlocate_wrong: {detail_str}")
    for code, _, detail_str in extract_wrong:
        tsv_lines.append(f"{code}\t0\t0\textract_issue: {detail_str}")
    tsv_path.write_text("\n".join(tsv_lines) + "\n")

    summary_path = out_dir / "summary.txt"
    summary_path.write_text("\n".join(lines), encoding="utf-8")

    summary_json_path = out_dir / "summary.json"
    summary_json_path.write_text(
        json.dumps(
            {
                "total": len(results),
                "fully_ok": len(fully_ok),
                "extract_wrong": len(extract_wrong),
                "locate_wrong": len(locate_wrong),
                "other_wrong": len(other_wrong),
                "locate_codes": sorted(loc_codes),
                "detail": detail,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"完全通过={len(fully_ok)} 抽取有误={len(extract_wrong)} 定位错误={len(locate_wrong)} 异常={len(other_wrong)}")
    print(summary_path)
    print(loc_wrong_path)


if __name__ == "__main__":
    import sys

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    main()
