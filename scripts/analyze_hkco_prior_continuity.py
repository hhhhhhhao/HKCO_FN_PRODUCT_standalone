#!/usr/bin/env python3
"""Analyze prior/current identity continuity without feeding GT into extraction.

Ground truth is used only in this offline report.  The production pipeline does
not import this module and never sees its outputs.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import unicodedata


TOTALS = {"合计", "合計", "总计", "總計", "总额", "總額", "total"}
TRANSLATION = str.maketrans(
    "臺裡裏為於與業務產銷售開發網據聯車醫藥護兒電纜風險資產物業項類體國華萬億圓號",
    "台里里为于与业务产销售开发网据联车医药护儿电缆风险资产物业项类体国华万亿圆号",
)


def normalize(value):
    text = unicodedata.normalize("NFKC", str(value or "")).translate(TRANSLATION).lower()
    text = re.sub(
        r"\([^)]*(?:附註|附注|note|[ivx\d]+)[^)]*\)|"
        r"（[^）]*(?:附註|附注|note|[ivx\d]+)[^）]*）",
        "", text, flags=re.I,
    )
    return re.sub(r"[\s:：,，。;；、()（）\[\]【】/\\_\-–—]+", "", text)


NORMALIZED_TOTALS = {normalize(value) for value in TOTALS}


def identities(rows, field="PRODUCTNAME"):
    return list(dict.fromkeys(
        name for name in (normalize(row.get(field)) for row in rows or [])
        if name and name not in NORMALIZED_TOTALS
    ))


def similarity(left, right):
    if left == right:
        return 1.0
    if left in right or right in left:
        return 0.8 + 0.15 * min(len(left), len(right)) / max(len(left), len(right))
    return SequenceMatcher(None, left, right).ratio()


def maximum_match(left, right, threshold=0.7):
    edges = [[index for index, value in enumerate(right)
              if similarity(item, value) >= threshold] for item in left]
    matched = {}

    def augment(item_index, seen):
        for target_index in edges[item_index]:
            if target_index in seen:
                continue
            seen.add(target_index)
            if target_index not in matched or augment(matched[target_index], seen):
                matched[target_index] = item_index
                return True
        return False

    return sum(bool(augment(index, set())) for index in range(len(left)))


def dominant(rows, field):
    values = Counter(str(row.get(field) or "") for row in rows or [] if row.get(field))
    return values.most_common(1)[0][0] if values else ""


def local_month_day(value):
    text = str(value or "")
    try:
        if "T" in text:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo:
                parsed = parsed.astimezone(timezone(timedelta(hours=8)))
            return parsed.month, parsed.day
    except ValueError:
        pass
    match = re.search(r"\d{4}[-/](\d{1,2})[-/](\d{1,2})", text)
    return (int(match.group(1)), int(match.group(2))) if match else None


def dominant_month_day(rows):
    values = Counter(filter(None, (local_month_day(row.get("REPORTDATE")) for row in rows or [])))
    return values.most_common(1)[0][0] if values else None


def candidate_identity_sets(row):
    candidates = []
    selected = identities(row.get("extract_items"), "PRODUCTNAME")
    if selected:
        candidates.append(("selected", selected))
    for item in (row.get("pipeline") or {}).get("rejected_hypotheses") or []:
        if item.get("rejection_reasons"):
            continue
        values = list(dict.fromkeys(
            name for name in (normalize(value) for value in item.get("fact_identities") or [])
            if name and name not in NORMALIZED_TOTALS
        ))
        if values:
            candidates.append((str(item.get("table_id") or "candidate"), values))
    unique = []
    seen = set()
    for source, values in candidates:
        signature = tuple(sorted(values))
        if signature in seen:
            continue
        seen.add(signature)
        unique.append((source, values))
    return unique


def analyze(prior, ground_truth, per_doc, threshold):
    rows = per_doc["rows"]
    codes = [row["infocode"] for row in rows if row.get("infocode") in prior and row.get("infocode") in ground_truth]
    pairs = [(code, identities(prior[code]), identities(ground_truth[code])) for code in codes]
    total_gt = sum(len(current) for _, _, current in pairs)
    result = {
        "doc_count": len(rows),
        "docs_with_prior_and_gt": len(pairs),
        "threshold": threshold,
        "prior_product_count": sum(len(previous) for _, previous, _ in pairs),
        "gt_product_count": total_gt,
        "exact_same_identity_set_docs": sum(set(previous) == set(current) for _, previous, current in pairs),
        "same_identity_count_docs": sum(len(previous) == len(current) for _, previous, current in pairs),
    }
    matches = [maximum_match(current, previous, threshold) for _, previous, current in pairs]
    result.update({
        "gt_identity_recall_from_prior": round(sum(matches) / total_gt, 6) if total_gt else 0.0,
        "all_gt_identities_matched_docs": sum(match == len(current)
                                               for match, (_, _, current) in zip(matches, pairs)),
        "fuzzy_bijection_docs": sum(match == len(previous) == len(current)
                                     for match, (_, previous, current) in zip(matches, pairs)),
        "docs_with_current_additions_or_dimension_change": sum(match < len(current)
                                                                for match, (_, _, current) in zip(matches, pairs)),
        "docs_with_prior_identities_removed": sum(match < len(previous)
                                                    for match, (_, previous, _) in zip(matches, pairs)),
        "currency_stable_docs": sum(dominant(prior[code], "CURRENCY") == dominant(ground_truth[code], "CURRENCY")
                                    for code, _, _ in pairs),
        "unit_stable_docs": sum(dominant(prior[code], "UNIT") == dominant(ground_truth[code], "UNIT")
                                for code, _, _ in pairs),
        "report_month_day_stable_docs": sum(
            bool(dominant_month_day(prior[code])
                 and dominant_month_day(prior[code]) == dominant_month_day(ground_truth[code]))
            for code, _, _ in pairs
        ),
    })

    conflict_docs = 0
    prior_top_contains_gt = 0
    selected_is_gt = 0
    prior_top_improves = 0
    prior_top_regresses = 0
    examples = []
    regression_examples = []
    strategies = {
        "prior_recall": {"improves": 0, "regresses": 0, "unchanged": 0},
        "prior_f1": {"improves": 0, "regresses": 0, "unchanged": 0},
    }
    for minimum_f1 in (0.5, 0.6, 0.7, 0.8):
        strategies[f"guarded_f1_{minimum_f1:.1f}"] = {
            "improves": 0, "regresses": 0, "unchanged": 0, "takeovers": 0,
        }
    for row in rows:
        code = row.get("infocode")
        if code not in prior or code not in ground_truth:
            continue
        candidates = candidate_identity_sets(row)
        if len(candidates) < 2:
            continue
        previous = identities(prior[code])
        current = identities(ground_truth[code])
        if not previous or not current:
            continue
        conflict_docs += 1
        scored = []
        for source, values in candidates:
            matched_prior = maximum_match(previous, values, threshold)
            prior_score = matched_prior / len(previous)
            prior_precision = matched_prior / len(values)
            prior_f1 = (2 * prior_score * prior_precision / (prior_score + prior_precision)
                        if prior_score + prior_precision else 0.0)
            gt_score = maximum_match(current, values, threshold) / len(current)
            is_gt = maximum_match(current, values, threshold) == len(current) == len(values)
            scored.append({
                "prior_recall": prior_score, "prior_f1": prior_f1,
                "gt_coverage": gt_score, "is_gt": is_gt,
                "source": source, "values": values,
            })
        best_prior = max(item["prior_recall"] for item in scored)
        top = [item for item in scored if item["prior_recall"] == best_prior]
        current_selected = next((item for item in scored if item["source"] == "selected"), None)
        if current_selected and current_selected["is_gt"]:
            selected_is_gt += 1
        if any(item["is_gt"] for item in top):
            prior_top_contains_gt += 1
        top_gt = max(item["gt_coverage"] for item in top)
        selected_gt = current_selected["gt_coverage"] if current_selected else 0.0
        prior_top_improves += top_gt > selected_gt
        prior_top_regresses += top_gt < selected_gt
        if len(examples) < 30 and top_gt != selected_gt:
            examples.append({
                "infocode": code,
                "prior": previous,
                "gt": current,
                "selected_gt_coverage": round(selected_gt, 3),
                "prior_top_gt_coverage": round(top_gt, 3),
                "prior_top_sources": [item["source"] for item in top],
            })
        if len(regression_examples) < 20 and top_gt < selected_gt:
            regression_examples.append({
                "infocode": code, "prior": previous, "gt": current,
                "selected": current_selected["values"] if current_selected else [],
                "prior_top": [{"source": item["source"], "values": item["values"]} for item in top],
                "selected_gt_coverage": round(selected_gt, 3),
                "prior_top_gt_coverage": round(top_gt, 3),
            })

        for strategy in ("prior_recall", "prior_f1"):
            chosen = max(scored, key=lambda item: (
                item[strategy], item["source"] == "selected",
            ))
            change = ("improves" if chosen["gt_coverage"] > selected_gt else
                      "regresses" if chosen["gt_coverage"] < selected_gt else "unchanged")
            strategies[strategy][change] += 1
        for minimum_f1 in (0.5, 0.6, 0.7, 0.8):
            key = f"guarded_f1_{minimum_f1:.1f}"
            chosen = max(scored, key=lambda item: (
                item["prior_f1"], item["source"] == "selected",
            ))
            if chosen["prior_f1"] < minimum_f1:
                chosen = current_selected
            elif chosen is not current_selected:
                strategies[key]["takeovers"] += 1
            change = ("improves" if chosen["gt_coverage"] > selected_gt else
                      "regresses" if chosen["gt_coverage"] < selected_gt else "unchanged")
            strategies[key][change] += 1
    result["candidate_conflict_analysis"] = {
        "conflict_docs": conflict_docs,
        "current_selected_identity_perfect_docs": selected_is_gt,
        "prior_top_includes_identity_perfect_candidate_docs": prior_top_contains_gt,
        "prior_top_improves_gt_coverage_docs": prior_top_improves,
        "prior_top_regresses_gt_coverage_docs": prior_top_regresses,
        "examples": examples,
        "regression_examples": regression_examples,
        "deterministic_strategies": strategies,
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--threshold", type=float, default=0.7)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    prior = json.loads((root / "tasks/HKCO_FN_PRODUCT/last_data.json").read_text(encoding="utf-8"))
    ground_truth = json.loads((root / "tasks/HKCO_FN_PRODUCT/ground_truth.json").read_text(encoding="utf-8"))
    per_doc = json.loads((args.run_dir / "metrics/per_doc.json").read_text(encoding="utf-8"))
    report = analyze(prior, ground_truth, per_doc, args.threshold)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
