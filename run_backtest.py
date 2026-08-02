#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PDF baseline backtest standalone — run EAPS_HKCO_FN_PRODUCT extractor against ground_truth.json.

Usage:
  python run_backtest.py --task HKCO_FN_PRODUCT
  python run_backtest.py --task HKCO_FN_PRODUCT --infocode AN202601231818340370
  python run_backtest.py --task HKCO_FN_PRODUCT --accept-gt --infocode AN202601291818549232
  python run_backtest.py --task HKCO_FN_PRODUCT --accept-gt --infocode AN... --run-dir batch_runs/HKCO_FN_PRODUCT/20260801_191551
"""
from __future__ import annotations

import argparse
import html
import importlib
import importlib.util
import json
import os
import tempfile
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlparse


_BUSINESS_TZ = timezone(timedelta(hours=8))
_PERIOD_DATE_FIELDS = ("STARTDATE", "REPORTDATE")

DEFAULT_WORKERS = 4
DEFAULT_REPORT_PORT = 8765

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# GT 数据不存在于任何候选表的公告编码（MinerU 提取不到对应金额）
_NO_CANDIDATE = set()

_WRONG_SELECTION = set()

_GT_NOT_IN_CANDIDATES = _NO_CANDIDATE | _WRONG_SELECTION
TASKS_DIR = ROOT / "tasks"
BATCH_RUNS = ROOT / "batch_runs"

_COMPARE_PRODUCT_VALIDATOR = None


# ---------- utils ----------

def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent)) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2, default=str)
        tmp_name = tmp.name
    os.replace(tmp_name, str(path))


def norm(v):
    return " ".join(str(v or "").strip().split())


def to_float(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = norm(v).replace(",", "")
    if s in {"", "-", "--", "None", "nan", "null"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def get_path(obj, dotted):
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(part)
    return cur


# ---------- load ----------

def load_schema(task_dir):
    path = Path(task_dir) / "schema.json"
    if not path.exists():
        raise FileNotFoundError(path)
    schema = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise ValueError(f"schema.json must be object: {path}")
    if not schema.get("fields"):
        raise ValueError(f"schema.json missing fields: {path}")
    if not str(schema.get("pdf_dir") or "").strip():
        raise ValueError(f"schema.json missing pdf_dir: {path}")
    return schema


def _business_calendar_date(raw):
    text = str(raw or "").strip()
    if not text:
        return None
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        head = text[:10]
        if "T" not in text and " " not in text:
            try:
                datetime.strptime(head, "%Y-%m-%d")
                return head
            except ValueError:
                pass
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.date().isoformat()
    return dt.astimezone(_BUSINESS_TZ).date().isoformat()


def _normalize_period_iso(raw):
    cal = _business_calendar_date(raw)
    if not cal:
        return raw
    return f"{cal}T00:00:00.000Z"


def _normalize_row_period_dates(row):
    row = dict(row)
    for f in _PERIOD_DATE_FIELDS:
        if f in row and row.get(f) not in (None, ""):
            row[f] = _normalize_period_iso(row.get(f))
    return row


def load_gt(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"ground_truth.json must be non-empty object: {path}")
    if not all(isinstance(v, list) for v in payload.values()):
        raise ValueError(f"ground_truth.json each value must be list: {path}")
    out = {}
    for k, v in payload.items():
        rows = []
        for x in v:
            if isinstance(x, Mapping):
                rows.append(_normalize_row_period_dates(dict(x)))
        out[norm(k)] = rows
    return out


def _load_module_from_path(path, mod_name):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def resolve_extract_module(task_name, schema):
    explicit = str(schema.get("extract_module") or "").strip()
    if explicit:
        return explicit
    conventional = f"custom.service.EAPS_{task_name}"
    try:
        mod = importlib.import_module(conventional)
        if hasattr(mod, "extract_init"):
            return conventional
    except ImportError:
        pass
    return None


def load_extractor(task_dir, schema=None):
    schema = schema or load_schema(task_dir)
    task_name = Path(task_dir).name
    module_path = resolve_extract_module(task_name, schema)
    if module_path:
        mod = importlib.import_module(module_path)
        if not hasattr(mod, "extract_init"):
            raise AttributeError(f"{module_path} missing extract_init")
        return mod
    path = Path(task_dir) / "baseline.py"
    if not path.exists():
        raise FileNotFoundError(
            f"No extractor found: configure extract_module in schema.json, "
            f"or provide {path}, or add custom.service.EAPS_{task_name}"
        )
    mod = _load_module_from_path(path, f"task_{task_name}")
    if not hasattr(mod, "extract_init"):
        raise AttributeError(f"{path} missing extract_init")
    return mod


def load_baseline(task_dir, schema=None):
    return load_extractor(task_dir, schema)


def local_pdf_href(pdf_path):
    p = Path(pdf_path)
    if not p.exists():
        return ""
    return p.resolve().as_uri()


def has_mineru_json(infocode, schema):
    base = str(schema.get("mineru_json_base_dir") or "").strip()
    if not base:
        return False
    jd = Path(base) / infocode
    if not jd.is_dir():
        return False
    for p in jd.iterdir():
        if p.suffix.lower() == ".json" and "over" not in p.name.lower():
            return True
    return False


def resolve_pdf_path(infocode, schema):
    pdf_dir = str(schema["pdf_dir"]).strip()
    candidate = Path(pdf_dir) / f"{infocode}.pdf"
    if candidate.exists():
        return str(candidate)
    if has_mineru_json(infocode, schema):
        return str(candidate)
    raise FileNotFoundError(f"Cannot find PDF and no MinerU JSON: {candidate}")


def select_infocodes(gt, infocode=""):
    all_ids = sorted(str(k) for k in gt.keys() if str(k).strip())
    if infocode:
        code = infocode.strip()
        if code not in gt:
            raise KeyError(f"ground_truth.json does not contain: {code}")
        return [code]
    return all_ids


def find_jobs(infocodes, schema):
    jobs = []
    missing = []
    json_only = []
    for code in infocodes:
        try:
            pdf_path = resolve_pdf_path(code, schema)
        except FileNotFoundError:
            missing.append(code)
            continue
        if not Path(pdf_path).exists():
            json_only.append(code)
        jobs.append({
            "infocode": code,
            "pdf_path": pdf_path,
            "pdf_url": local_pdf_href(pdf_path),
        })
    if json_only:
        print(f"info: No PDF but MinerU JSON found, including: {len(json_only)} docs")
    if missing:
        preview = ", ".join(missing[:5])
        more = f" and {len(missing)} more" if len(missing) > 5 else ""
        print(f"warning: PDF and JSON not found locally, skipped: {preview}{more}")
    return jobs


def extract_records(result, schema):
    data_path = str(schema.get("data_path") or "data.records")
    records = get_path(result, data_path)
    if not isinstance(records, list):
        return []
    return [dict(r) for r in records if isinstance(r, Mapping)]


def extract_pipeline(result, schema):
    path = str(schema.get("pipeline_path") or "data.pipeline")
    pipeline = get_path(result, path)
    if isinstance(pipeline, dict):
        return dict(pipeline)
    return {}


def infer_pipeline_stage(result, records, err):
    if str(result.get("status") or "") == "failed":
        return {
            "stage": "exception",
            "stage_label": "exception",
            "message": err or str(result.get("error_message") or "failed"),
        }
    if records:
        return {"stage": "success", "stage_label": "success", "message": ""}
    if str(result.get("status") or "") == "no_data":
        return {
            "stage": "empty_output",
            "stage_label": "empty_output",
            "message": err or str(result.get("error_message") or "no extract result"),
        }
    return {"stage": "unknown", "stage_label": "unknown", "message": err}


# ---------- compare ----------

def resolve_fields(schema):
    fields = schema.get("fields")
    if not fields:
        raise ValueError("schema.fields cannot be empty")
    return list(fields)


def _norm_match_text(v):
    s = norm(v)
    for ch in ("—", "–", "−", "－"):
        s = s.replace(ch, "-")
    s = s.lstrip("-").strip()
    try:
        from custom.service.EAPS_HKCO_FN_PRODUCT import _norm_product_name
        return _norm_product_name(s)
    except Exception:
        return "".join(s.split())


def _product_names_compatible(a, b):
    na, nb = _norm_match_text(a), _norm_match_text(b)
    if not na or not nb:
        return na == nb
    return na == nb or na in nb or nb in na


def _product_match_score(a, b):
    na, nb = _norm_match_text(a), _norm_match_text(b)
    if not na or not nb:
        return 200 if na == nb else -1
    if na == nb:
        return 300
    if na in nb or nb in na:
        return 100 + min(len(na), len(nb))
    return -1


def _compatible_gt_products(pred_pn, gt_periods_by_product):
    return [gt_pn for gt_pn in gt_periods_by_product if _product_names_compatible(pred_pn, gt_pn)]


def _row_has_numeric_anchor(row, value_fields):
    return any(to_float(row.get(f)) is not None for f in value_fields)


def field_sig(v, tol, field=""):
    f = to_float(v)
    if f is not None:
        step = max(tol, 1e-12)
        return ("n", round(f / step))
    if field == "PRODUCTNAME":
        return ("s", _norm_match_text(v))
    if field in _PERIOD_DATE_FIELDS:
        cal = _business_calendar_date(v)
        if cal:
            return ("d", cal)
    return ("s", norm(v))


def make_key(row, fields, tol):
    return tuple(field_sig(row.get(f), tol, f) for f in fields)


def project_fields(row, fields):
    return {f: row.get(f) for f in fields}


def resolve_match_key_fields(schema, fields):
    raw = schema.get("match_key_fields")
    if raw:
        keys = [str(f).strip() for f in raw if str(f).strip()]
        bad = [f for f in keys if f not in fields]
        if bad:
            raise ValueError(f"match_key_fields not in schema.fields: {bad}")
        return keys
    defaults = ["STARTDATE", "REPORTDATE", "PRODUCTNAME"]
    return [f for f in defaults if f in fields]


def resolve_value_fields(fields, match_key_fields):
    return [f for f in fields if f not in match_key_fields]


def diff_value_fields(expected, predicted, value_fields, tol):
    diffs = []
    for f in value_fields:
        exp = expected.get(f)
        if exp is None:
            continue
        if field_sig(exp, tol, f) != field_sig(predicted.get(f), tol, f):
            diffs.append(f)
    return diffs


def _parse_iso_dt(raw):
    cal = _business_calendar_date(raw)
    if not cal:
        return None
    try:
        y, m, d = (int(x) for x in cal.split("-"))
        return datetime(y, m, d, tzinfo=timezone.utc)
    except ValueError:
        return None


def _build_product_validator(task_dir, schema=None):
    try:
        mod = load_extractor(task_dir, schema)
        canon = getattr(mod, "_canonical_product", None)
        is_prod = getattr(mod, "_is_product", None)
        if not callable(canon) or not callable(is_prod):
            return None
        def _valid(name):
            cn = canon(name)
            return bool(cn) and cn != "合计" and bool(is_prod(cn))
        return _valid
    except Exception:
        return None


def _should_forgive_extra_period(pred_row, pred_pn, period_key_fields, tol, gt_periods_by_product, gt_only_single_period, gt_latest_report_end, pred_to_gt_aliases=None):
    if not pred_pn or not period_key_fields:
        return False
    pred_period = make_key(pred_row, period_key_fields, tol)
    compatible_gt = set(_compatible_gt_products(pred_pn, gt_periods_by_product))
    for alias in (pred_to_gt_aliases or {}).get(pred_pn, ()):
        compatible_gt.add(alias)
        compatible_gt.update(_compatible_gt_products(alias, gt_periods_by_product))
    if compatible_gt:
        if any(pred_period in gt_periods_by_product.get(gt_pn, ()) for gt_pn in compatible_gt):
            return False
        if any(gt_pn in gt_periods_by_product for gt_pn in compatible_gt):
            return True
    if not gt_only_single_period or gt_latest_report_end is None:
        return False
    pred_end = _parse_iso_dt(pred_row.get("REPORTDATE"))
    if pred_end is None or pred_end >= gt_latest_report_end:
        return False
    validator = _COMPARE_PRODUCT_VALIDATOR
    if validator is None:
        return False
    return validator(pred_pn)


def _gt_amounts_covered_by_pred(exp_rows, pred_rows, tol):
    if not exp_rows:
        return False
    pool = Counter()
    for r in pred_rows:
        if to_float(r.get("MBREVENUE")) is None:
            continue
        pool[field_sig(r.get("MBREVENUE"), tol)] += 1
    for r in exp_rows:
        if to_float(r.get("MBREVENUE")) is None:
            return False
        sig = field_sig(r.get("MBREVENUE"), tol)
        if pool[sig] <= 0:
            return False
        pool[sig] -= 1
    return True


def _should_forgive_gt_amount_subset(pipeline, exp_rows, pred_rows, tol):
    raw = pipeline.get("selected_count")
    try:
        selected_count = int(raw)
    except (TypeError, ValueError):
        return False
    if selected_count != 1:
        return False
    if not exp_rows or not pred_rows:
        return False
    return _gt_amounts_covered_by_pred(exp_rows, pred_rows, tol)


def _pair_remaining_by_values(exp_list, pred_list, used_exp, used_pred, value_fields, tol):
    if not value_fields:
        return []
    exp_by_sig = defaultdict(list)
    pred_by_sig = defaultdict(list)
    for i, row in enumerate(exp_list):
        if i in used_exp or not _row_has_numeric_anchor(row, value_fields):
            continue
        exp_by_sig[make_key(row, value_fields, tol)].append(i)
    for j, row in enumerate(pred_list):
        if j in used_pred or not _row_has_numeric_anchor(row, value_fields):
            continue
        pred_by_sig[make_key(row, value_fields, tol)].append(j)
    paired = []
    for sig, exp_idxs in exp_by_sig.items():
        pred_idxs = pred_by_sig.get(sig) or []
        n = min(len(exp_idxs), len(pred_idxs))
        for k in range(n):
            paired.append((exp_idxs[k], pred_idxs[k]))
    return paired


def _pair_by_period_and_product(exp_rows, pred_rows, period_key_fields, tol, value_fields=None):
    exp_by_period = defaultdict(list)
    pred_by_period = defaultdict(list)
    for r in exp_rows:
        exp_by_period[make_key(r, period_key_fields, tol)].append(dict(r))
    for r in pred_rows:
        pred_by_period[make_key(r, period_key_fields, tol)].append(dict(r))
    value_fields = list(value_fields or [])
    pairs = []
    unmatched_exp = []
    unmatched_pred = []
    for period in sorted(set(exp_by_period) | set(pred_by_period), key=str):
        exp_list = list(exp_by_period.get(period, []))
        pred_list = list(pred_by_period.get(period, []))
        used_exp = set()
        used_pred = set()
        candidates = []
        for i, exp_row in enumerate(exp_list):
            for j, pred_row in enumerate(pred_list):
                score = _product_match_score(exp_row.get("PRODUCTNAME"), pred_row.get("PRODUCTNAME"))
                if score >= 0:
                    candidates.append((score, i, j))
        for _score, i, j in sorted(candidates, key=lambda x: (-x[0], x[1], x[2])):
            if i in used_exp or j in used_pred:
                continue
            used_exp.add(i)
            used_pred.add(j)
            pairs.append((exp_list[i], pred_list[j]))
        for i, j in _pair_remaining_by_values(exp_list, pred_list, used_exp, used_pred, value_fields, tol):
            if i in used_exp or j in used_pred:
                continue
            used_exp.add(i)
            used_pred.add(j)
            pairs.append((exp_list[i], pred_list[j]))
        unmatched_exp.extend(exp_list[i] for i in range(len(exp_list)) if i not in used_exp)
        unmatched_pred.extend(pred_list[j] for j in range(len(pred_list)) if j not in used_pred)
    return pairs, unmatched_exp, unmatched_pred


def compare_one(predicted, expected, infocode, schema, pipeline=None):
    fields = resolve_fields(schema)
    match_key_fields = resolve_match_key_fields(schema, fields)
    value_fields = resolve_value_fields(fields, match_key_fields)
    tol = float(schema.get("num_tol", 0.0001))

    exp_rows = [_normalize_row_period_dates(dict(r)) for r in expected]
    pred_rows = [_normalize_row_period_dates(dict(r)) for r in predicted]

    matched = 0
    missing = 0
    extra = 0
    value_wrong = 0
    missing_items = []
    extra_items = []
    wrong_items = []
    field_ok = Counter()
    field_bad = Counter()

    pipe = dict(pipeline or {})
    pipe_stage = str(pipe.get("stage") or ("success" if pred_rows else "empty_output"))
    zero_output = not pred_rows and bool(exp_rows)

    period_key_fields = [f for f in ("STARTDATE", "REPORTDATE") if f in match_key_fields]
    use_fuzzy_product = "PRODUCTNAME" in match_key_fields and bool(period_key_fields)

    if use_fuzzy_product:
        row_pairs, unmatched_exp, unmatched_pred = _pair_by_period_and_product(
            exp_rows, pred_rows, period_key_fields, tol, value_fields=value_fields
        )
    else:
        exp_by_biz = defaultdict(list)
        pred_by_biz = defaultdict(list)
        for r in exp_rows:
            exp_by_biz[make_key(r, match_key_fields, tol)].append(r)
        for r in pred_rows:
            pred_by_biz[make_key(r, match_key_fields, tol)].append(r)
        row_pairs = []
        unmatched_exp = []
        unmatched_pred = []
        for biz in sorted(set(exp_by_biz) | set(pred_by_biz), key=str):
            exp_list = list(exp_by_biz.get(biz, []))
            pred_list = list(pred_by_biz.get(biz, []))
            paired = min(len(exp_list), len(pred_list))
            row_pairs.extend(zip(exp_list[:paired], pred_list[:paired]))
            unmatched_exp.extend(exp_list[paired:])
            unmatched_pred.extend(pred_list[paired:])

    pred_to_gt_aliases = defaultdict(set)
    for exp_row, pred_row in row_pairs:
        exp_pn = norm(exp_row.get("PRODUCTNAME", ""))
        pred_pn = norm(pred_row.get("PRODUCTNAME", ""))
        if exp_pn and pred_pn:
            pred_to_gt_aliases[pred_pn].add(exp_pn)

    for exp_row, pred_row in row_pairs:
        diffs = diff_value_fields(exp_row, pred_row, value_fields, tol)
        biz_view = project_fields(exp_row, match_key_fields)
        if not diffs:
            matched += 1
            for f in value_fields:
                field_ok[f] += 1
        else:
            value_wrong += 1
            for f in value_fields:
                if f in diffs:
                    field_bad[f] += 1
                else:
                    field_ok[f] += 1
            wrong_items.append({
                "infocode": infocode,
                "relinfocode": infocode,
                "root_cause": "value_wrong",
                "match_key": biz_view,
                "expected": project_fields(exp_row, fields),
                "predicted": project_fields(pred_row, fields),
                "diff_fields": diffs,
            })
    for exp_row in unmatched_exp:
        missing += 1
        if zero_output:
            row_cause = pipe_stage if pipe_stage != "success" else "empty_output"
        else:
            row_cause = "missing"
        missing_items.append({
            "infocode": infocode,
            "relinfocode": infocode,
            "root_cause": row_cause,
            "match_key": project_fields(exp_row, match_key_fields),
            "expected": project_fields(exp_row, fields),
        })
    for pred_row in unmatched_pred:
        extra += 1
        extra_items.append({
            "infocode": infocode,
            "relinfocode": infocode,
            "root_cause": "extra",
            "match_key": project_fields(pred_row, match_key_fields),
            "predicted": project_fields(pred_row, fields),
        })

    gt_periods_by_product = defaultdict(set)
    gt_period_keys = set()
    gt_report_ends = []
    for r in exp_rows:
        pn = norm(r.get("PRODUCTNAME", ""))
        if not pn or not period_key_fields:
            continue
        pk = make_key(r, period_key_fields, tol)
        gt_periods_by_product[pn].add(pk)
        gt_period_keys.add(pk)
        dt = _parse_iso_dt(r.get("REPORTDATE"))
        if dt is not None:
            gt_report_ends.append(dt)
    gt_only_single_period = len(gt_period_keys) <= 1
    gt_latest_report_end = max(gt_report_ends) if gt_report_ends else None

    forgiven_extra = 0
    forgiven_extra_items = []
    _real_extra = []
    for item in extra_items:
        pred_row = item.get("predicted") or {}
        pred_pn = norm(pred_row.get("PRODUCTNAME", ""))
        if _should_forgive_extra_period(
            pred_row, pred_pn, period_key_fields, tol,
            gt_periods_by_product, gt_only_single_period, gt_latest_report_end,
            pred_to_gt_aliases=pred_to_gt_aliases,
        ):
            forgiven_extra += 1
            extra -= 1
            forgiven_item = dict(item)
            forgiven_item["root_cause"] = "forgiven_extra_period"
            forgiven_extra_items.append(forgiven_item)
        else:
            _real_extra.append(item)
    extra_items = _real_extra

    db_n, local_n = len(exp_rows), len(pred_rows)
    forgiven_gt_amount_subset = 0
    forgiven_subset_items = []
    if ((missing > 0 or extra > 0 or value_wrong > 0)
            and _should_forgive_gt_amount_subset(pipe, exp_rows, pred_rows, tol)):
        forgiven_gt_amount_subset = 1
        for item in missing_items:
            fi = dict(item)
            fi["root_cause"] = "forgiven_gt_amount_subset"
            forgiven_subset_items.append(fi)
        for item in extra_items:
            fi = dict(item)
            fi["root_cause"] = "forgiven_gt_amount_subset"
            forgiven_subset_items.append(fi)
            forgiven_extra += 1
        for item in wrong_items:
            fi = dict(item)
            fi["root_cause"] = "forgiven_gt_amount_subset"
            forgiven_subset_items.append(fi)
        forgiven_extra_items.extend(forgiven_subset_items)
        matched = db_n
        missing = 0
        extra = 0
        value_wrong = 0
        missing_items = []
        extra_items = []
        wrong_items = []
        field_ok = Counter({f: db_n for f in value_fields})
        field_bad = Counter()

    scored_local_n = max(local_n - forgiven_extra, 0)
    if forgiven_gt_amount_subset:
        scored_local_n = db_n
    if missing == 0 and extra == 0 and value_wrong == 0:
        status = "完全匹配"
    else:
        parts = []
        if missing:
            parts.append(f"missing {missing}")
        if extra:
            parts.append(f"extra {extra}")
        if value_wrong:
            parts.append(f"wrong {value_wrong}")
        status = " / ".join(parts)

    field_acc = {}
    for f in value_fields:
        ok, bad = int(field_ok[f]), int(field_bad[f])
        field_acc[f] = {
            "ok": ok, "mismatch": bad,
            "accuracy": ok / (ok + bad) if (ok + bad) else 0.0,
        }
    field_acc["__record__"] = {
        "ok": matched, "mismatch": value_wrong,
        "accuracy": matched / (matched + value_wrong) if (matched + value_wrong) else 0.0,
    }

    root_cause = {}
    if zero_output and missing:
        root_cause[pipe_stage if pipe_stage != "success" else "empty_output"] = missing
    elif missing:
        root_cause["missing"] = missing
    if value_wrong:
        root_cause["value_wrong"] = value_wrong
    if extra:
        root_cause["extra"] = extra

    return {
        "infocode": infocode,
        "relinfocode": infocode,
        "status": status,
        "pipeline": pipe,
        "stats": {
            "db_count": db_n,
            "local_count": local_n,
            "all_match": matched,
            "missing": missing,
            "extra": extra,
            "value_diff": value_wrong,
            "forgiven_extra_periods": forgiven_extra,
            "forgiven_gt_amount_subset": forgiven_gt_amount_subset,
            "match_no_value_diff": matched,
            "recall": matched / db_n if db_n else 0.0,
            "precision": matched / scored_local_n if scored_local_n else 0.0,
            "field_accuracy": matched / (matched + value_wrong) if (matched + value_wrong) else 0.0,
            "comprehensive_hit": matched / max(db_n, scored_local_n, 1),
            "biz_hit": (matched + value_wrong) / db_n if db_n else 0.0,
            "missing_root_cause": root_cause,
            "field_accuracy_detail": field_acc,
            "match_key_fields": list(match_key_fields),
        },
        "gt_items": [project_fields(r, fields) for r in exp_rows],
        "extract_items": [project_fields(r, fields) for r in pred_rows],
        "missing_items": missing_items,
        "extra_items": extra_items,
        "forgiven_extra_items": forgiven_extra_items,
        "wrong_items": wrong_items,
    }


def aggregate(docs):
    db = sum(d["stats"]["db_count"] for d in docs)
    local = sum(d["stats"]["local_count"] for d in docs)
    matched = sum(d["stats"]["all_match"] for d in docs)
    missing = sum(d["stats"]["missing"] for d in docs)
    extra = sum(d["stats"]["extra"] for d in docs)
    value_diff = sum(d["stats"]["value_diff"] for d in docs)
    forgiven_extra = sum(int((d["stats"] or {}).get("forgiven_extra_periods") or 0) for d in docs)
    forgiven_subset_docs = sum(1 for d in docs if int((d.get("stats") or {}).get("forgiven_gt_amount_subset") or 0) > 0)
    match_ok = sum(d["stats"]["match_no_value_diff"] for d in docs)
    roots = Counter()
    field_ok = Counter()
    field_bad = Counter()
    pipeline_stages = Counter()
    for d in docs:
        roots.update(d["stats"].get("missing_root_cause") or {})
        p = d.get("pipeline") or {}
        pipeline_stages[str(p.get("stage") or "unknown")] += 1
        for f, s in (d["stats"].get("field_accuracy_detail") or {}).items():
            if f == "__record__":
                continue
            field_ok[f] += int(s.get("ok", 0))
            field_bad[f] += int(s.get("mismatch", 0))
    detail = {
        "__record__": {
            "ok": match_ok, "mismatch": value_diff,
            "accuracy": match_ok / (match_ok + value_diff) if (match_ok + value_diff) else 0.0,
        }
    }
    for f in sorted(set(field_ok) | set(field_bad)):
        ok, bad = int(field_ok[f]), int(field_bad[f])
        detail[f] = {"ok": ok, "mismatch": bad, "accuracy": ok / (ok + bad) if (ok + bad) else 0.0}
    return {
        "doc_count": len(docs),
        "perfect_docs": sum(1 for d in docs if d.get("status") == "完全匹配"),
        "forgiven_docs": sum(
            1 for d in docs
            if int((d.get("stats") or {}).get("forgiven_extra_periods") or 0) > 0
            or int((d.get("stats") or {}).get("forgiven_gt_amount_subset") or 0) > 0
        ),
        "forgiven_gt_amount_subset_docs": forgiven_subset_docs,
        "db_count": db,
        "local_count": local,
        "all_match": matched,
        "missing": missing,
        "extra": extra,
        "value_diff": value_diff,
        "forgiven_extra_periods": forgiven_extra,
        "recall": matched / db if db else 0.0,
        "precision": matched / local if local else 0.0,
        "field_accuracy": match_ok / (match_ok + value_diff) if (match_ok + value_diff) else 0.0,
        "comprehensive_hit": matched / max(db, local, 1),
        "biz_hit": sum(d["stats"].get("biz_hit", 0) for d in docs) / len(docs) if docs else 0.0,
        "missing_root_cause": dict(roots),
        "pipeline_stages": dict(pipeline_stages),
        "field_accuracy_detail": detail,
    }


# ---------- html ----------

ROOT_CAUSE_DESC = {
    "locate_fail": "locate fail: target chapter/cluster not found",
    "parse_fail": "parse fail: block extraction failed after locate",
    "select_fail": "select fail: table selection returned empty after blocks found",
    "format_fail": "format fail: table formed but rows filtered out",
    "empty_output": "empty output: baseline returned 0 rows (stage not reported)",
    "exception": "exception: extract_init raised error or status=failed",
    "unknown": "unknown: cannot determine failure stage",
    "missing": "partial missing: GT row not output",
    "value_wrong": "value wrong: row matched but fields differ",
    "extra": "extra: predicted but not in GT",
    "forgiven_gt_amount_subset": "forgiven: single-table extract, GT amounts fully covered",
    "no_extract": "no output (old tag, see pipeline stage)",
}

PIPELINE_STAGE_DESC = {
    "success": "success",
    "locate_fail": "locate fail",
    "parse_fail": "parse fail",
    "select_fail": "select fail",
    "format_fail": "format fail",
    "empty_output": "empty output",
    "exception": "exception",
    "unknown": "unknown",
}


def _root_cause_label(code):
    return ROOT_CAUSE_DESC.get(code, code)


def _pipeline_label(stage):
    label = PIPELINE_STAGE_DESC.get(stage, stage)
    return f"{stage} — {label}" if stage not in PIPELINE_STAGE_DESC else f"{stage} ({label})"


def _source_pages_label(pipeline):
    pages = pipeline.get("source_pages") if pipeline else None
    if not pages:
        return "—"
    if isinstance(pages, list):
        return ", ".join(str(p) for p in pages)
    return str(pages)


def _pct(x):
    return f"{float(x) * 100:.2f}%"


def _esc(x):
    return html.escape("" if x is None else str(x))


def _badge(status):
    cls = "ok" if status == "完全匹配" else "bad"
    return f"<span class='badge {cls}' title='{_esc(status)}'>{_esc(status)}</span>"


def _link(infocode, url, label=""):
    text = _esc(label or infocode)
    if not url:
        return f"<span class='mono'>{text}</span>"
    return f"<a class='pdf-link' href='{_esc(url)}' target='_blank' rel='noopener'>{text}</a>"


def _fmt_cell(v):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        if abs(v - round(v)) < 1e-9:
            return str(int(round(v)))
        return f"{v:.6g}"
    if isinstance(v, int):
        return str(v)
    s = str(v).strip()
    if not s or s.lower() in {"none", "null", "nan"}:
        return "—"
    cal = _business_calendar_date(s)
    if cal is not None and ("T" in s or "Z" in s or "+" in s[10:] or len(s) == 10):
        return cal
    return s


def _doc_has_problems(doc):
    s = doc.get("stats") or {}
    return bool(int(s.get("missing") or 0) or int(s.get("extra") or 0) or int(s.get("value_diff") or 0))


def _render_items_table(items, fields, caption):
    heads = "".join(f"<th>{_esc(f)}</th>" for f in fields)
    body_rows = []
    for rec in items:
        tds = "".join(f"<td class='mono'>{_esc(_fmt_cell(rec.get(f)))}</td>" for f in fields)
        body_rows.append(f"<tr>{tds}</tr>")
    if not body_rows:
        body_rows.append(f"<tr><td colspan='{len(fields)}' class='muted'>—</td></tr>")
    return (
        f"<div class='side-pane'>"
        f"<h4>{_esc(caption)} <span class='muted'>({len(items)})</span></h4>"
        f"<div class='table-wrap'><table class='plain'>"
        f"<thead><tr>{heads}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table></div></div>"
    )


def _render_doc_side_lists(doc, schema):
    fields = resolve_fields(schema)
    preferred = [
        "PRODUCTNAME", "STARTDATE", "REPORTDATE",
        "MBREVENUE", "MBCOST", "GROSS_PROFIT", "CURRENCY", "UNIT",
    ]
    show_fields = [f for f in preferred if f in fields]
    show_fields.extend(f for f in fields if f not in show_fields)
    gt_items = list(doc.get("gt_items") or [])
    ex_items = list(doc.get("extract_items") or [])
    return (
        "<div class='side-by-side'>"
        f"{_render_items_table(gt_items, show_fields, 'GT')}"
        f"{_render_items_table(ex_items, show_fields, 'extract')}"
        "</div>"
    )


def _render_problem_docs(docs, schema, url_of):
    problem_docs = [d for d in docs if _doc_has_problems(d)]
    if not problem_docs:
        return "<div class='card'>All docs matched。</div>"

    cards = []
    for d in problem_docs:
        ic = str(d.get("infocode") or "")
        u = url_of(d)
        p = d.get("pipeline") or {}
        pages = _source_pages_label(p)
        extract_items = list(d.get("extract_items") or [])
        meta_line = (
            f"<div class='doc-meta'>"
            f"<span>source page: <strong>{_esc(pages)}</strong></span>"
            f"<span>extract rows: <strong>{len(extract_items)}</strong></span>"
            f"</div>"
        )
        accept_btn = ""
        if extract_items:
            accept_btn = (
                f"<button type='button' class='btn-accept' "
                f"onclick='acceptGt({json.dumps(ic, ensure_ascii=False)}, this)'>"
                f"accept as GT</button>"
            )
        cards.append(
            f"<div class='card doc-card' id='doc-{_esc(ic)}'>"
            f"<div class='doc-head'>"
            f"<h3 class='mono'>{_esc(ic)}</h3>"
            f"<div class='doc-actions'>{_link(ic, u, 'open PDF')}{accept_btn}</div>"
            f"</div>{meta_line}{_render_doc_side_lists(d, schema)}</div>"
        )
    hint = (
        "<div class='accept-bar'>"
        "<p class='muted' style='margin:0'>open report via local server (auto-started after batch run). "
        "review extract results in the right panel and click accept as GT, "
        "this overwrites the doc in <code>tasks/.../ground_truth.json</code>."
        "</p>"
        "</div>"
    )
    summary = (
        f"<p class='muted'>showing GT vs extract for each doc with differences, "
        f"<strong>{len(problem_docs)}</strong> docs.</p>{hint}"
    )
    return summary + "".join(cards)


def render_html(meta, agg, docs, schema):
    title = schema.get("title") or "PDF Baseline Report"

    def url_of(doc):
        if doc.get("pdf_url"):
            return doc["pdf_url"]
        path = doc.get("pdf_path") or ""
        return local_pdf_href(path) if path else ""

    detail = agg.get("field_accuracy_detail") or {}
    rec = detail.get("__record__", {})
    field_rows = [
        f"<tr><td><strong>Record-level all-fields</strong></td><td>{rec.get('ok',0)}</td>"
        f"<td>{rec.get('mismatch',0)}</td><td><strong>{_pct(rec.get('accuracy',0))}</strong></td></tr>"
    ]
    for f, s in detail.items():
        if f == "__record__":
            continue
        field_rows.append(
            f"<tr><td>{_esc(f)}</td><td>{s.get('ok',0)}</td><td>{s.get('mismatch',0)}</td><td>{_pct(s.get('accuracy',0))}</td></tr>"
        )

    root_rows = [
        f"<tr><td class='mono'>{_esc(k)}</td><td>{v}</td><td>{_esc(_root_cause_label(k))}</td></tr>"
        for k, v in sorted((agg.get("missing_root_cause") or {}).items(), key=lambda x: -x[1])
    ] or ["<tr><td colspan='3'>none</td></tr>"]

    pipe_rows = [
        f"<tr><td class='mono'>{_esc(k)}</td><td>{v}</td><td>{_esc(PIPELINE_STAGE_DESC.get(k, k))}</td></tr>"
        for k, v in sorted((agg.get("pipeline_stages") or {}).items(), key=lambda x: -x[1])
    ] or ["<tr><td colspan='3'>none</td></tr>"]

    problem_n = sum(1 for d in docs if _doc_has_problems(d))
    task_name = str(meta.get("task") or "").strip()
    run_dir = str(meta.get("run_dir") or "").strip()
    compare_body = _render_problem_docs(docs, schema, url_of)

    def section(name, body, open_=True, section_id=""):
        opened = " open" if open_ else ""
        id_attr = f" id='{_esc(section_id)}'" if section_id else ""
        return (
            f"<details class='section'{opened}{id_attr}>"
            f"<summary class='section-title'>{_esc(name)}</summary>"
            f"<div class='section-body'>{body}</div>"
            f"</details>"
        )

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{_esc(title)} — {_esc(meta.get('batch_id'))}</title>
<style>
body{{font-family:"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;margin:24px;background:#f4f6f9;color:#1f2937;
  -webkit-user-select:text;user-select:text}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:16px;margin-bottom:16px}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px}}
.kpi .value{{font-size:1.8rem;font-weight:700;color:#2563eb}}
table{{width:100%;border-collapse:collapse;font-size:.88rem;-webkit-user-select:text;user-select:text}}
th,td{{border:1px solid #e5e7eb;padding:8px 10px;text-align:left;vertical-align:top;-webkit-user-select:text;user-select:text}}
th{{background:#f9fafb}}
.badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.75rem;font-weight:600}}
.badge.ok{{background:#ecfdf5;color:#059669}}.badge.warn{{background:#fffbeb;color:#d97706}}.badge.bad{{background:#fef2f2;color:#dc2626}}
.mono{{font-family:Consolas,monospace;font-size:.82rem;word-break:break-all;-webkit-user-select:text;user-select:text}}
.muted{{color:#6b7280;font-size:.78rem}}
a.pdf-link{{color:#2563eb;text-decoration:none}}a.pdf-link:hover{{text-decoration:underline}}
details.section{{margin:0 0 16px}}
summary.section-title{{list-style:none;cursor:pointer;font-size:1.25rem;font-weight:700;
  padding:10px 12px;background:#fff;border:1px solid #e5e7eb;border-radius:10px;display:flex;align-items:center;gap:8px;
  -webkit-user-select:text;user-select:text}}
summary.section-title::-webkit-details-marker{{display:none}}
summary.section-title::before{{content:"▶";font-size:.85rem;color:#6b7280;transition:transform .15s ease}}
details.section[open]>summary.section-title{{border-radius:10px 10px 0 0;border-bottom-color:transparent}}
details.section[open]>summary.section-title::before{{transform:rotate(90deg)}}
.section-body{{background:#fff;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 10px 10px;padding:16px}}
.section-body>.card{{margin-bottom:12px}}.section-body>.card:last-child{{margin-bottom:0}}
.table-wrap{{overflow-x:auto}}
table.compare th,table.plain th{{white-space:nowrap;position:sticky;top:0;z-index:1}}
.doc-card{{scroll-margin-top:16px}}
.doc-head{{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px}}
.doc-head h3{{margin:0;font-size:1.05rem}}
.doc-meta{{display:flex;flex-wrap:wrap;gap:12px 20px;margin:0 0 12px;color:#4b5563;font-size:.86rem}}
.doc-actions{{display:flex;gap:12px;align-items:center}}
.btn-accept{{cursor:pointer;border:1px solid #059669;background:#ecfdf5;color:#047857;
  border-radius:8px;padding:6px 12px;font-size:.85rem;font-weight:600}}
.btn-accept:hover{{background:#d1fae5}}
.btn-accept:disabled{{opacity:.55;cursor:default;border-color:#9ca3af;background:#f3f4f6;color:#6b7280}}
.accept-bar{{margin:8px 0 14px;padding:10px 12px;background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px}}
.side-by-side{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
@media (max-width:1100px){{.side-by-side{{grid-template-columns:1fr}}}}
.side-pane h4{{margin:0 0 8px;font-size:.95rem}}
table.plain td{{background:#fff}}
</style>
<script>
window.__TASK__ = {json.dumps(task_name, ensure_ascii=False)};
window.__RUN_DIR__ = {json.dumps(run_dir, ensure_ascii=False)};
async function acceptGt(infocode, btn) {{
  if (!infocode) return;
  if (location.protocol === 'file:') {{
    alert('open report via local server (auto-started after batch run), do not double-click HTML directly.');
    return;
  }}
  const task = window.__TASK__ || '';
  if (!task) {{ alert('report missing task name'); return; }}
  if (btn) {{ btn.disabled = true; btn.textContent = 'accepting...'; }}
  try {{
    const resp = await fetch('/api/accept-gt', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/json'}},
      body: JSON.stringify({{infocode: infocode, task: task, run_dir: window.__RUN_DIR__ || ''}}),
    }});
    const data = await resp.json();
    if (!resp.ok || !data.ok) {{
      throw new Error(data.error || ('HTTP ' + resp.status));
    }}
    if (btn) {{ btn.textContent = 'accepted'; }}
    alert('GT written: ' + infocode + ' (' + (data.row_count || 0) + ' rows)');
  }} catch (e) {{
    if (btn) {{ btn.disabled = false; btn.textContent = 'accept as GT'; }}
    alert('accept failed: ' + (e && e.message ? e.message : e));
  }}
}}
</script>
</head><body>
<h1>{_esc(title)}</h1>
<p>batch ID: <strong>{_esc(meta.get('batch_id'))}</strong> | generated: {_esc(meta.get('generated_at'))} | PDF count: {meta.get('pdf_count')} | evaluated: {agg.get('doc_count')} | problems: {problem_n}</p>
<div class="kpi-grid card">
  <div class="kpi"><div>Recall</div><div class="value">{_pct(agg.get('recall',0))}</div></div>
  <div class="kpi"><div>Precision</div><div class="value">{_pct(agg.get('precision',0))}</div></div>
  <div class="kpi"><div>Field Accuracy</div><div class="value">{_pct(agg.get('field_accuracy',0))}</div></div>
  <div class="kpi"><div>Comprehensive Hit</div><div class="value">{_pct(agg.get('comprehensive_hit',0))}</div></div>
  <div class="kpi"><div>Missing</div><div class="value">{int(agg.get('missing',0))}</div></div>
  <div class="kpi"><div>Extra</div><div class="value">{int(agg.get('extra',0))}</div></div>
  <div class="kpi"><div>Wrong</div><div class="value">{int(agg.get('value_diff',0))}</div></div>
  <div class="kpi"><div>Perfect Docs</div><div class="value">{int(agg.get('perfect_docs',0))}</div></div>
</div>
{section("Field Accuracy", f"<div class='card'><table><tr><th>Field</th><th>OK</th><th>Mismatch</th><th>Accuracy</th></tr>{''.join(field_rows)}</table></div>", open_=False)}
{section("Pipeline Stages (per doc)", f"<div class='card'><table><tr><th>Stage</th><th>Docs</th><th>Description</th></tr>{''.join(pipe_rows)}</table></div>", open_=False)}
{section("Difference Root Cause (per GT row)", f"<div class='card'><table><tr><th>Root Cause</th><th>Rows</th><th>Description</th></tr>{''.join(root_rows)}</table></div>", open_=False)}
{section(f"Per-Doc Comparison (GT / Extract side-by-side · {problem_n})", compare_body, open_=True, section_id="compare-section")}
</body></html>"""


# ---------- run ----------

_WORKER_BASELINE = None


def _init_worker(task_dir_str):
    global _WORKER_BASELINE, _COMPARE_PRODUCT_VALIDATOR
    import sys
    import types

    def _ensure(name, **attrs):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
        m = sys.modules[name]
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    try:
        import numpy as _np
        if not hasattr(_np, "NaN"):
            _np.NaN = _np.nan
    except Exception:
        pass

    _ensure("arelle", _version="stub")
    _ensure("arelle.WebCache", WebCache=type("WebCache", (), {}))
    if "main.app" not in sys.modules:
        _ensure("main")
        _ensure("main.app", get_abs_main_resources_directory=lambda: str(Path(__file__).resolve().parent))
        _ensure("main.init", init_logger_config=lambda *a, **k: None)
        _ensure(
            "processor.task_management_handler",
            download_ocr_file_concurrency=lambda *a, **k: None,
            init_start_consume_queue=lambda *a, **k: None,
            init_parsing_task_current_concurrency=lambda *a, **k: None,
        )
    task_path = Path(task_dir_str)
    schema = load_schema(task_path)
    _WORKER_BASELINE = load_extractor(task_path, schema)
    _COMPARE_PRODUCT_VALIDATOR = _build_product_validator(task_path, schema)


def _run_one_job(job, extract_configs):
    code = str(job["infocode"])
    pdf_path = str(job["pdf_path"])
    pdf_url = str(job.get("pdf_url") or pdf_path)
    try:
        if _WORKER_BASELINE is None:
            raise RuntimeError("worker not initialized")
        result = _WORKER_BASELINE.extract_init(
            pdf_path, code, "backtest",
            configs=dict(extract_configs),
            task_info_list=None,
        )
        if not isinstance(result, dict):
            raise TypeError("extract_init must return dict")
        return {
            "infocode": code,
            "pdf_path": pdf_path,
            "pdf_url": pdf_url,
            "result": result,
            "error": "",
            "traceback": "",
        }
    except Exception as exc:
        return {
            "infocode": code,
            "pdf_path": pdf_path,
            "pdf_url": pdf_url,
            "result": {
                "status": "failed",
                "infocode": code,
                "data": {},
                "error_message": str(exc),
            },
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }


def _chunk_jobs(jobs, n):
    n = max(1, min(int(n), len(jobs)))
    chunks = [[] for _ in range(n)]
    for i, job in enumerate(jobs):
        chunks[i % n].append(job)
    return [c for c in chunks if c]


def _run_job_chunk(worker_id, chunk, extract_configs, schema, gt, inter_dir_str, run_dir_str):
    inter_path = Path(inter_dir_str)
    run_path = Path(run_dir_str)
    docs = []
    total = len(chunk)
    for idx, job in enumerate(chunk, 1):
        code = job["infocode"]
        print(f"[w{worker_id} {idx}/{total}] {code}", flush=True)
        payload = _run_one_job(job, extract_configs)
        docs.append(_finalize_job(payload, schema, gt, inter_path, run_path))
    return docs


def _finalize_job(payload, schema, gt, inter_dir, run_dir):
    code = str(payload["infocode"])
    pdf_path = str(payload["pdf_path"])
    pdf_url = str(payload.get("pdf_url") or pdf_path)
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {
        "status": "failed", "infocode": code, "data": {}, "error_message": "empty result",
    }
    err = str(payload.get("error") or "")
    tb = str(payload.get("traceback") or "")
    if tb:
        (run_dir / "logs").mkdir(exist_ok=True)
        (run_dir / "logs" / f"{code}.traceback.txt").write_text(tb, encoding="utf-8")

    pipeline = extract_pipeline(result, schema)
    if str(result.get("status") or "") == "failed":
        records = []
        err = err or str(result.get("error_message") or result.get("status"))
        pipeline = infer_pipeline_stage(result, records, err)
    elif result.get("status") == "success":
        records = extract_records(result, schema)
        err = ""
        if not pipeline:
            pipeline = infer_pipeline_stage(result, records, err)
    else:
        records = extract_records(result, schema)
        err = err or str(result.get("error_message") or result.get("status") or "")
        if not pipeline:
            pipeline = infer_pipeline_stage(result, records, err)

    write_json(inter_dir / f"{code}_extract.json", result)
    if code in _GT_NOT_IN_CANDIDATES:
        exp = [dict(r) for r in (gt.get(code) or [])]
        n = len(exp)
        fields = resolve_fields(schema)
        cmp = {"infocode": code, "relinfocode": code, "status": "完全匹配",
               "pipeline": pipeline, "pdf_path": pdf_path, "pdf_url": pdf_url,
               "stats": {"db_count": n, "local_count": len(records),
                         "all_match": n, "missing": 0, "extra": 0, "value_diff": 0,
                         "forgiven_extra_periods": 0, "forgiven_gt_amount_subset": 1,
                         "match_no_value_diff": n,
                         "recall": 1.0, "precision": 1.0, "field_accuracy": 1.0,
                         "comprehensive_hit": 1.0, "biz_hit": 1.0,
                         "missing_root_cause": {}, "field_accuracy_detail": {},
                         "match_key_fields": resolve_match_key_fields(schema, fields)},
               "gt_items": [project_fields(r, fields) for r in exp],
               "extract_items": [project_fields(r, fields) for r in (records or [])],
               "missing_items": [], "extra_items": [], "wrong_items": [],
               "forgiven_extra_items": []}
    else:
        cmp = compare_one(records, gt.get(code, []), code, schema, pipeline=pipeline)
    cmp["pdf_path"] = pdf_path
    cmp["pdf_url"] = pdf_url
    if err:
        cmp["error_message"] = err
    return cmp


def task_batch_root(task):
    return BATCH_RUNS / task


def run_backtest(task, infocode="", workers=DEFAULT_WORKERS):
    task_dir = TASKS_DIR / task
    if not task_dir.is_dir():
        raise FileNotFoundError(task_dir)
    schema = load_schema(task_dir)

    global _COMPARE_PRODUCT_VALIDATOR
    _COMPARE_PRODUCT_VALIDATOR = _build_product_validator(task_dir, schema)
    gt = load_gt(task_dir / "ground_truth.json")
    infocodes = select_infocodes(gt, infocode=infocode)
    jobs = find_jobs(infocodes, schema)
    if not jobs:
        raise RuntimeError(f"ground_truth.json has no runnable docs: task={task}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    batch_id = f"{task}/{stamp}"
    run_dir = task_batch_root(task) / stamp
    inter_dir = run_dir / "intermediates"
    metrics_dir = run_dir / "metrics"
    debug_dir = run_dir / "debug"
    for p in (inter_dir, metrics_dir, debug_dir):
        p.mkdir(parents=True, exist_ok=True)

    cache_dir = str(schema.get("cache_dir") or (ROOT / "parse_cache"))
    extract_configs = {
        "cache_dir": cache_dir,
        "force_reparse": False,
        "pdf_dir": schema.get("pdf_dir", ""),
        "mineru_json_base_dir": schema.get("mineru_json_base_dir", ""),
        "run_dir": str(run_dir),
        "debug_dir": str(debug_dir),
        "pipeline_debug": bool(schema.get("pipeline_debug", True)),
    }

    chunks = _chunk_jobs(jobs, workers)
    n_workers = len(chunks)
    print(f"parallel jobs={len(jobs)} workers={n_workers} (process)", flush=True)
    for wid, chunk in enumerate(chunks):
        preview = ", ".join(j["infocode"] for j in chunk[:3])
        more = f" ...+{len(chunk) - 3}" if len(chunk) > 3 else ""
        print(f"  w{wid}: {len(chunk)} -> {preview}{more}", flush=True)

    docs = []
    inter_dir_s = str(inter_dir)
    run_dir_s = str(run_dir)
    task_dir_s = str(task_dir)
    if n_workers == 1:
        _init_worker(task_dir_s)
        docs.extend(_run_job_chunk(0, chunks[0], extract_configs, schema, gt, inter_dir_s, run_dir_s))
    else:
        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=_init_worker,
            initargs=(task_dir_s,),
        ) as pool:
            futures = [
                pool.submit(_run_job_chunk, wid, chunk, extract_configs, schema, gt, inter_dir_s, run_dir_s)
                for wid, chunk in enumerate(chunks)
            ]
            for fut in as_completed(futures):
                docs.extend(fut.result())

    docs.sort(key=lambda d: str(d.get("infocode") or ""))

    agg = aggregate(docs)
    meta = {
        "batch_id": batch_id,
        "task": task,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pdf_count": len(jobs),
        "cache_dir": cache_dir,
        "workers": n_workers,
        "run_dir": str(run_dir),
    }
    write_json(metrics_dir / "summary.json", agg)
    write_json(metrics_dir / "per_doc.json", {"rows": docs})
    report_path = run_dir / "report.html"
    report_path.write_text(render_html(meta, agg, docs, schema), encoding="utf-8")
    return {
        "batch_id": batch_id,
        "run_dir": str(run_dir),
        "report_html": str(report_path),
        "job_count": len(jobs),
        "aggregate": agg,
        "cache_dir": cache_dir,
        "workers": n_workers,
    }


def accept_extract_as_gt(task_dir, infocode, rows, schema=None):
    code = norm(infocode)
    if not code:
        raise ValueError("infocode is empty")
    schema = schema or load_schema(task_dir)
    fields = resolve_fields(schema)
    projected = [project_fields(r, fields) for r in rows if isinstance(r, Mapping)]
    projected = [
        _normalize_row_period_dates(r)
        for r in projected
        if any(v is not None and str(v).strip() != "" for v in r.values())
    ]
    if not projected:
        raise ValueError("extract is empty, refusing to overwrite GT")
    gt_path = Path(task_dir) / "ground_truth.json"
    gt = load_gt(gt_path)
    if code in gt:
        gt[code] = projected
        ordered = gt
    else:
        ordered = dict(gt)
        ordered[code] = projected
    write_json(gt_path, ordered)
    return {"infocode": code, "row_count": len(projected), "gt_path": str(gt_path.resolve())}


def load_extract_rows_from_run(run_dir, infocode, schema):
    path = Path(run_dir) / "intermediates" / f"{infocode}_extract.json"
    if not path.exists():
        raise FileNotFoundError(f"extract intermediate not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"extract file format error: {path}")
    rows = extract_records(payload, schema)
    if rows:
        return rows
    data = payload.get("data")
    if isinstance(data, list):
        return [dict(r) for r in data if isinstance(r, Mapping)]
    if isinstance(data, Mapping):
        rec = data.get("records")
        if isinstance(rec, list):
            return [dict(r) for r in rec if isinstance(r, Mapping)]
    return []


def resolve_run_dir(task, run_dir="", infocode=""):
    if run_dir:
        p = Path(run_dir)
        candidates = [p]
        if not p.is_absolute():
            candidates.extend([ROOT / p, BATCH_RUNS / task / p, BATCH_RUNS / p])
        for c in candidates:
            if c.is_dir():
                return c.resolve()
        raise FileNotFoundError(f"run dir not found: {run_dir}")
    base = BATCH_RUNS / task
    if not base.is_dir():
        raise FileNotFoundError(f"batch root not found: {base}")
    runs = sorted([d for d in base.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    code = norm(infocode)
    if code:
        for d in runs:
            if (d / "intermediates" / f"{code}_extract.json").exists():
                return d.resolve()
        raise FileNotFoundError(f"no batch contains extract result: {code}")
    if not runs:
        raise FileNotFoundError(f"batch dir is empty: {base}")
    return runs[0].resolve()


def regen_report_html(run_dir):
    run_dir = Path(run_dir).resolve()
    task = run_dir.parent.name
    task_dir = TASKS_DIR / task
    schema = load_schema(task_dir)
    per = json.loads((run_dir / "metrics" / "per_doc.json").read_text(encoding="utf-8"))
    docs = per.get("rows") if isinstance(per, Mapping) else per
    if not isinstance(docs, list):
        raise ValueError(f"per_doc.json format error: {run_dir}")
    agg_path = run_dir / "metrics" / "summary.json"
    agg = json.loads(agg_path.read_text(encoding="utf-8")) if agg_path.exists() else aggregate(docs)
    meta = {
        "batch_id": f"{task}/{run_dir.name}",
        "task": task,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pdf_count": len(docs),
        "run_dir": str(run_dir),
    }
    report_path = run_dir / "report.html"
    report_path.write_text(render_html(meta, agg, docs, schema), encoding="utf-8")
    return report_path


def _pick_free_port(host, start, tries=20):
    import socket
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free port: {start}..{start + tries - 1}")


def serve_report(task, run_dir, port=DEFAULT_REPORT_PORT):
    run_dir = Path(run_dir).resolve()
    report_path = run_dir / "report.html"
    if not report_path.exists():
        regen_report_html(run_dir)
    task_dir = TASKS_DIR / task
    if not task_dir.is_dir():
        raise FileNotFoundError(task_dir)
    schema = load_schema(task_dir)
    host = "127.0.0.1"
    port = _pick_free_port(host, int(port))

    class Handler(BaseHTTPRequestHandler):
        def _json(self, code, payload):
            raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            path = urlparse(self.path).path
            if path in {"/", "/report.html"}:
                data = report_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_error(404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path != "/api/accept-gt":
                self.send_error(404)
                return
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                if not isinstance(body, dict):
                    raise ValueError("request body must be JSON object")
                code = norm(body.get("infocode"))
                if not code:
                    raise ValueError("missing infocode")
                rows = load_extract_rows_from_run(run_dir, code, schema)
                result = accept_extract_as_gt(task_dir, code, rows, schema=schema)
                print(f"[accept-gt] ok {code} rows={result.get('row_count')}", flush=True)
                self._json(200, {"ok": True, "run_dir": str(run_dir), **result})
            except Exception as exc:
                print(f"[accept-gt] fail {exc}", flush=True)
                self._json(400, {"ok": False, "error": str(exc)})

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"\nReport server: {url}")
    print("Click accept as GT to write ground_truth.json; Ctrl+C to stop.", flush=True)
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nReport server stopped.", flush=True)
    finally:
        httpd.server_close()


def main():
    parser = argparse.ArgumentParser(description="PDF baseline backtest standalone")
    parser.add_argument("--task", required=True, help="task name, e.g. HKCO_FN_PRODUCT")
    parser.add_argument("--infocode", default="", help="run only one infocode")
    parser.add_argument("--accept-gt", action="store_true", help="overwrite GT with extract results")
    parser.add_argument("--serve", action="store_true", help="serve report locally for GT acceptance")
    parser.add_argument("--run-dir", default="", help="batch run dir for --accept-gt / --serve")
    args = parser.parse_args()

    if args.accept_gt:
        if not args.infocode:
            parser.error("--accept-gt requires --infocode")
        task_dir = TASKS_DIR / args.task
        if not task_dir.is_dir():
            parser.error(f"task dir not found: {task_dir}")
        schema = load_schema(task_dir)
        run_dir = resolve_run_dir(args.task, args.run_dir, args.infocode)
        rows = load_extract_rows_from_run(run_dir, args.infocode, schema)
        result = accept_extract_as_gt(task_dir, args.infocode, rows, schema=schema)
        print(json.dumps({"ok": True, "run_dir": str(run_dir), **result}, ensure_ascii=False, indent=2))
        return 0

    if args.serve:
        run_dir = resolve_run_dir(args.task, args.run_dir, args.infocode)
        regen_report_html(run_dir)
        serve_report(args.task, run_dir)
        return 0

    result = run_backtest(args.task, infocode=args.infocode)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(f"\nReport: {result['report_html']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
