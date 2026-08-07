#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PDF baseline 回测：跑 schema.extract_module（或 EAPS_<task>）→ 对比 ground_truth.json → 出 report.html

用法:
  python run_backtest.py --task HKCO_FN_PRODUCT
  python run_backtest.py --task HKCO_FN_PRODUCT --infocode AN202601231818340370

  # 用某次跑批的抽取结果覆盖 ground_truth.json 里该公告（整表替换）
  python run_backtest.py --task HKCO_FN_PRODUCT --accept-gt --infocode AN202601291818549232
  python run_backtest.py --task HKCO_FN_PRODUCT --accept-gt --infocode AN... --run-dir batch_runs/HKCO_FN_PRODUCT/20260724_133332

默认跑 ground_truth.json 里全部公告；先均分公告，默认 4 进程各跑各的。
批次目录: batch_runs/{task}/{YYYYMMDD_HHMMSS}/
"""

from __future__ import annotations

import argparse
import html
import importlib
import importlib.util
import json
import multiprocessing
import os
import tempfile
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

# 业务日时区：旧版把「东八区零点」存成 UTC（…T16:00:00Z），新版直接写日历日 …T00:00:00Z。
# 比对/展示一律归一成 Asia/Shanghai 日历日，禁止再按 UTC 日期片（否则 2024-01-01 → 2023-12-31）。
_BUSINESS_TZ = timezone(timedelta(hours=8))
_PERIOD_DATE_FIELDS = ("STARTDATE", "REPORTDATE")

DEFAULT_WORKERS = 4

ROOT = Path(__file__).resolve().parent
# 保证直接 python run_backtest.py 也能 import src 下包
_SRC = ROOT.parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
TASKS_DIR = ROOT / "tasks"
BATCH_RUNS = ROOT / "batch_runs"

# compare_one 可选产品名校验（run_backtest 启动时从 baseline 注入）
_COMPARE_PRODUCT_VALIDATOR: Optional[Callable[[str], bool]] = None


# ---------- utils ----------

def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent)) as tmp:
        json.dump(payload, tmp, ensure_ascii=False, indent=2, default=str)
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def norm(v: Any) -> str:
    return " ".join(str(v or "").strip().split())


def to_float(v: Any) -> Optional[float]:
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


def get_path(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(part)
    return cur


# ---------- load ----------

def load_schema(task_dir: Path) -> Dict[str, Any]:
    path = task_dir / "schema.json"
    if not path.exists():
        raise FileNotFoundError(path)
    schema = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(schema, dict):
        raise ValueError(f"schema.json 必须是对象: {path}")
    if not schema.get("fields"):
        raise ValueError(f"schema.json 缺少 fields: {path}")
    if not str(schema.get("pdf_dir") or "").strip():
        raise ValueError(f"schema.json 缺少 pdf_dir: {path}")
    # Paths in schema.json are resolved relative to the project root.  This
    # keeps the same configuration usable on macOS and Windows.
    for key in ("pdf_dir", "cache_dir"):
        value = str(schema.get(key) or "").strip()
        if value:
            candidate = Path(os.path.expandvars(os.path.expanduser(value)))
            if not candidate.is_absolute():
                candidate = ROOT / candidate
            schema[key] = str(candidate.resolve())
    return schema


def _business_calendar_date(raw: Any) -> Optional[str]:
    """ISO/日期串 → 业务日历日 YYYY-MM-DD（东八区）。"""
    text = str(raw or "").strip()
    if not text:
        return None
    # 已是纯日期
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
        # 无时区：按业务日历日字面量，不按 UTC 偏移
        return dt.date().isoformat()
    return dt.astimezone(_BUSINESS_TZ).date().isoformat()


def _normalize_period_iso(raw: Any) -> Any:
    """期间字段统一为日历日 UTC 零点 ISO；空值原样。"""
    cal = _business_calendar_date(raw)
    if not cal:
        return raw
    return f"{cal}T00:00:00.000Z"


def _normalize_row_period_dates(row: Dict[str, Any]) -> Dict[str, Any]:
    for f in _PERIOD_DATE_FIELDS:
        if f in row and row.get(f) not in (None, ""):
            row[f] = _normalize_period_iso(row.get(f))
    return row


def load_gt(path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """只接受 {infocode: [row, ...]}。期间字段读入即归一成日历日。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"ground_truth.json 必须是非空对象: {path}")
    if not all(isinstance(v, list) for v in payload.values()):
        raise ValueError(f"ground_truth.json 每个 value 必须是 list: {path}")
    out: Dict[str, List[Dict[str, Any]]] = {}
    for k, v in payload.items():
        rows = []
        for x in v:
            if isinstance(x, Mapping):
                rows.append(_normalize_row_period_dates(dict(x)))
        out[norm(k)] = rows
    return out


def _load_module_from_path(path: Path, mod_name: str):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def resolve_extract_module(task_name: str, schema: Mapping[str, Any]) -> Optional[str]:
    """解析抽取模块：schema.extract_module > EAPS_<task> 约定 > None（回退 baseline.py）。"""
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


def load_extractor(task_dir: Path, schema: Optional[Mapping[str, Any]] = None):
    """加载抽取入口：优先 EAPS 生产模块，否则 tasks/<task>/baseline.py。"""
    schema = schema or load_schema(task_dir)
    task_name = task_dir.name
    module_path = resolve_extract_module(task_name, schema)
    if module_path:
        mod = importlib.import_module(module_path)
        if not hasattr(mod, "extract_init"):
            raise AttributeError(f"{module_path} 缺少 extract_init")
        return mod
    path = task_dir / "baseline.py"
    if not path.exists():
        raise FileNotFoundError(
            f"未找到抽取实现：请在 schema.json 配置 extract_module，"
            f"或提供 {path}，或添加 custom.service.EAPS_{task_name}"
        )
    mod = _load_module_from_path(path, f"task_{task_name}")
    if not hasattr(mod, "extract_init"):
        raise AttributeError(f"{path} 缺少 extract_init")
    return mod


def local_pdf_href(pdf_path: str) -> str:
    """报告里用本地 file:// 链接打开 PDF；文件不存在时返回空。"""
    p = Path(pdf_path)
    if not p.exists():
        return ""
    return p.resolve().as_uri()


def resolve_pdf_path(infocode: str, schema: Mapping[str, Any]) -> str:
    pdf_dir = str(schema["pdf_dir"]).strip()
    candidate = Path(pdf_dir) / f"{infocode}.pdf"
    if candidate.exists():
        return str(candidate)
    raise FileNotFoundError(f"找不到 PDF: {candidate}")


def is_pdf_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(5) == b"%PDF-"
    except OSError:
        return False


def select_infocodes(gt: Mapping[str, Any], infocode: str = "") -> List[str]:
    """默认：ground_truth.json 全部 key。"""
    all_ids = sorted(str(k) for k in gt.keys() if str(k).strip())
    if infocode:
        code = infocode.strip()
        if code not in gt:
            raise KeyError(f"ground_truth.json 中没有该公告: {code}")
        return [code]
    return all_ids


def find_jobs(infocodes: Sequence[str], schema: Mapping[str, Any]) -> List[Dict[str, str]]:
    jobs = []
    missing = []
    not_pdf = []
    excluded = {
        str(code).strip()
        for code in (schema.get("exclude_infocodes") or ())
        if str(code).strip()
    }
    skipped = []
    for code in infocodes:
        if code in excluded:
            skipped.append(code)
            continue
        try:
            pdf_path = resolve_pdf_path(code, schema)
        except FileNotFoundError:
            missing.append(code)
            continue
        if not is_pdf_file(Path(pdf_path)):
            not_pdf.append(code)
            continue
        jobs.append(
            {
                "infocode": code,
                "pdf_path": pdf_path,
                "pdf_url": local_pdf_href(pdf_path),
            }
        )
    if missing:
        preview = ", ".join(missing[:5])
        more = f" 等{len(missing)}个" if len(missing) > 5 else ""
        print(f"warning: 本地未找到 PDF，已跳过: {preview}{more}")
    if not_pdf:
        preview = ", ".join(not_pdf[:5])
        more = f" 等{len(not_pdf)}个" if len(not_pdf) > 5 else ""
        print(f"warning: 文件不是有效 PDF，已跳过: {preview}{more}")
    if skipped:
        preview = ", ".join(skipped[:5])
        more = f" 等{len(skipped)}个" if len(skipped) > 5 else ""
        print(f"warning: 配置排除，已跳过: {preview}{more}")
    return jobs


def extract_records(result: Mapping[str, Any], schema: Mapping[str, Any]) -> List[Dict[str, Any]]:
    data_path = str(schema.get("data_path") or "data.records")
    records = get_path(result, data_path)
    if not isinstance(records, list):
        return []
    return [dict(r) for r in records if isinstance(r, Mapping)]


def extract_pipeline(result: Mapping[str, Any], schema: Mapping[str, Any]) -> Dict[str, Any]:
    path = str(schema.get("pipeline_path") or "data.pipeline")
    pipeline = get_path(result, path)
    if isinstance(pipeline, dict):
        return dict(pipeline)
    return {}


# ---------- compare ----------

def resolve_fields(schema: Mapping[str, Any]) -> List[str]:
    fields = schema.get("fields")
    if not fields:
        raise ValueError("schema.fields 不能为空")
    return list(fields)


def _norm_match_text(v: Any) -> str:
    """产品名比较归一：去空白、连字符统一、简繁折叠（与抽取侧一致），便于 in 匹配。"""
    s = norm(v)
    for ch in ("—", "–", "−", "－"):
        s = s.replace(ch, "-")
    s = s.lstrip("-").strip()
    try:
        from custom.service.HKCO_FN_PRODUCT_selector import identity_key

        return identity_key(s)
    except Exception:
        return "".join(s.split())


def _product_names_match(a: Any, b: Any) -> bool:
    """回测产品名匹配：归一后相等，或一方包含另一方。"""
    na, nb = _norm_match_text(a), _norm_match_text(b)
    if not na or not nb:
        return na == nb
    return na == nb or na in nb or nb in na


def _product_match_score(a: Any, b: Any) -> int:
    """配对：精确 > in 包含（公共段越长优先）。"""
    na, nb = _norm_match_text(a), _norm_match_text(b)
    if not na or not nb:
        return 200 if na == nb else -1
    if na == nb:
        return 300
    if na in nb or nb in na:
        return 100 + min(len(na), len(nb))
    return -1


def _matching_gt_products(
    pred_pn: str,
    gt_periods_by_product: Mapping[str, set],
) -> List[str]:
    return [gt_pn for gt_pn in gt_periods_by_product if _product_names_match(pred_pn, gt_pn)]


def _row_has_numeric_anchor(row: Mapping[str, Any], value_fields: Sequence[str]) -> bool:
    """至少有一个可比较的数值字段，避免空值行仅靠币种/单位误配。"""
    return any(to_float(row.get(f)) is not None for f in value_fields)


def _pair_remaining_by_values(
    exp_list: Sequence[Mapping[str, Any]],
    pred_list: Sequence[Mapping[str, Any]],
    used_exp: set,
    used_pred: set,
    value_fields: Sequence[str],
    tol: float,
) -> List[Tuple[int, int]]:
    """名称对不上时：同报告期内按数值字段签名配对（收入/成本/币种/单位等一致则算同一行）。"""
    if not value_fields:
        return []
    exp_by_sig: Dict[Tuple[Any, ...], List[int]] = defaultdict(list)
    pred_by_sig: Dict[Tuple[Any, ...], List[int]] = defaultdict(list)
    for i, row in enumerate(exp_list):
        if i in used_exp or not _row_has_numeric_anchor(row, value_fields):
            continue
        exp_by_sig[make_key(row, value_fields, tol)].append(i)
    for j, row in enumerate(pred_list):
        if j in used_pred or not _row_has_numeric_anchor(row, value_fields):
            continue
        pred_by_sig[make_key(row, value_fields, tol)].append(j)

    paired: List[Tuple[int, int]] = []
    for sig, exp_idxs in exp_by_sig.items():
        pred_idxs = pred_by_sig.get(sig) or []
        n = min(len(exp_idxs), len(pred_idxs))
        for k in range(n):
            paired.append((exp_idxs[k], pred_idxs[k]))
    return paired


def _pair_by_period_and_product(
    exp_rows: Sequence[Mapping[str, Any]],
    pred_rows: Sequence[Mapping[str, Any]],
    period_key_fields: Sequence[str],
    tol: float,
    value_fields: Optional[Sequence[str]] = None,
) -> Tuple[List[Tuple[Dict[str, Any], Dict[str, Any]]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """同一报告期内先按产品名配对，剩余再按数值字段签名配对。"""
    exp_by_period: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    pred_by_period: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
    for r in exp_rows:
        exp_by_period[make_key(r, period_key_fields, tol)].append(dict(r))
    for r in pred_rows:
        pred_by_period[make_key(r, period_key_fields, tol)].append(dict(r))

    value_fields = list(value_fields or [])
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    unmatched_exp: List[Dict[str, Any]] = []
    unmatched_pred: List[Dict[str, Any]] = []
    for period in sorted(set(exp_by_period) | set(pred_by_period), key=str):
        exp_list = list(exp_by_period.get(period, []))
        pred_list = list(pred_by_period.get(period, []))
        used_exp: set = set()
        used_pred: set = set()
        candidates: List[Tuple[int, int, int]] = []
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
        for i, j in _pair_remaining_by_values(
            exp_list, pred_list, used_exp, used_pred, value_fields, tol
        ):
            if i in used_exp or j in used_pred:
                continue
            used_exp.add(i)
            used_pred.add(j)
            pairs.append((exp_list[i], pred_list[j]))
        unmatched_exp.extend(exp_list[i] for i in range(len(exp_list)) if i not in used_exp)
        unmatched_pred.extend(pred_list[j] for j in range(len(pred_list)) if j not in used_pred)
    return pairs, unmatched_exp, unmatched_pred


def field_sig(v: Any, tol: float, field: str = "") -> Tuple[str, Any]:
    f = to_float(v)
    if f is not None:
        # 数值按容差量化，避免 21885 / 21885.0 被当成不同 key
        step = max(tol, 1e-12)
        return ("n", round(f / step))
    # GT 未标注（None）和抽取为空（""）统一成空签名，避免 MBCOST/GROSS_PROFIT
    # None vs "" 被当成不同值造成匹配/比对误判。
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return ("e",)
    if field == "PRODUCTNAME":
        return ("s", _norm_match_text(v))
    # STARTDATE/REPORTDATE：旧 T16:00Z 与新 T00:00Z 都归一成业务日历日再比
    if field in _PERIOD_DATE_FIELDS:
        cal = _business_calendar_date(v)
        if cal:
            return ("d", cal)
    return ("s", norm(v))


def make_key(row: Mapping[str, Any], fields: Sequence[str], tol: float) -> Tuple[Tuple[str, Any], ...]:
    return tuple(field_sig(row.get(f), tol, f) for f in fields)


def project_fields(row: Mapping[str, Any], fields: Sequence[str]) -> Dict[str, Any]:
    return {f: row.get(f) for f in fields}


def resolve_match_key_fields(schema: Mapping[str, Any], fields: Sequence[str]) -> List[str]:
    """业务主键：同一公告内定位「哪条产品行」。"""
    raw = schema.get("match_key_fields")
    if raw:
        keys = [str(f).strip() for f in raw if str(f).strip()]
        bad = [f for f in keys if f not in fields]
        if bad:
            raise ValueError(f"match_key_fields 不在 schema.fields 中: {bad}")
        return keys
    # 默认：报告期 + 产品名
    defaults = ["STARTDATE", "REPORTDATE", "PRODUCTNAME"]
    return [f for f in defaults if f in fields]


def resolve_value_fields(fields: Sequence[str], match_key_fields: Sequence[str]) -> List[str]:
    return [f for f in fields if f not in match_key_fields]


def diff_value_fields(
    expected: Mapping[str, Any],
    predicted: Mapping[str, Any],
    value_fields: Sequence[str],
    tol: float,
) -> List[str]:
    diffs: List[str] = []
    for f in value_fields:
        exp = expected.get(f)
        # GT 未标注（null）不参与比对：新字段未填满时避免误伤 all_match
        if exp is None:
            continue
        if field_sig(exp, tol, f) != field_sig(predicted.get(f), tol, f):
            diffs.append(f)
    return diffs


def _parse_iso_dt(raw: Any) -> Optional[datetime]:
    """解析为业务日历日的 UTC 零点（用于报告期先后比较，不用原始 UTC 瞬时）。"""
    cal = _business_calendar_date(raw)
    if not cal:
        return None
    try:
        y, m, d = (int(x) for x in cal.split("-"))
        return datetime(y, m, d, tzinfo=timezone.utc)
    except ValueError:
        return None


def _build_product_validator(
    task_dir: Path,
    schema: Optional[Mapping[str, Any]] = None,
) -> Optional[Callable[[str], bool]]:
    """复用抽取模块的产品判定，供单向豁免过滤误抓行。"""
    try:
        mod = load_extractor(task_dir, schema)
        canon = getattr(mod, "_canonical_product", None)
        is_prod = getattr(mod, "_is_product", None)
        if not callable(canon) or not callable(is_prod):
            return None

        def _valid(name: str) -> bool:
            cn = canon(name)
            return bool(cn) and cn != "合计" and bool(is_prod(cn))

        return _valid
    except Exception:
        return None


def _should_forgive_extra_period(
    pred_row: Mapping[str, Any],
    pred_pn: str,
    period_key_fields: Sequence[str],
    tol: float,
    gt_periods_by_product: Mapping[str, set],
    gt_only_single_period: bool,
    gt_latest_report_end: Optional[datetime],
    pred_to_gt_aliases: Optional[Mapping[str, set]] = None,
) -> bool:
    """单向豁免：GT 漏收的历史报告期（含比较年独有产品）。"""
    if not pred_pn or not period_key_fields:
        return False
    pred_period = make_key(pred_row, period_key_fields, tol)
    matching_gt = set(_matching_gt_products(pred_pn, gt_periods_by_product))
    for alias in (pred_to_gt_aliases or {}).get(pred_pn, ()):
        matching_gt.add(alias)
        matching_gt.update(_matching_gt_products(alias, gt_periods_by_product))
    if matching_gt:
        if any(
            pred_period in gt_periods_by_product.get(gt_pn, ())
            for gt_pn in matching_gt
        ):
            return False
        # A. GT 已有匹配名称或显式别名，只是多了历史期。
        if any(gt_pn in gt_periods_by_product for gt_pn in matching_gt):
            return True
    # B. GT 仅收录单一报告期；合法产品的历史期（含当年停披露项）
    if not gt_only_single_period or gt_latest_report_end is None:
        return False
    pred_end = _parse_iso_dt(pred_row.get("REPORTDATE"))
    if pred_end is None or pred_end >= gt_latest_report_end:
        return False
    validator = _COMPARE_PRODUCT_VALIDATOR
    if validator is None:
        return False
    return validator(pred_pn)


_GT_AMOUNT_FIELDS = ("MBREVENUE", "MBCOST", "GROSS_PROFIT")


def _gt_amounts_covered_by_pred(
    exp_rows: Sequence[Mapping[str, Any]],
    pred_rows: Sequence[Mapping[str, Any]],
    tol: float,
) -> bool:
    """GT 每条 (MBREVENUE, MBCOST, GROSS_PROFIT) 都能在抽取里找到（按容差，多重集消耗）。"""
    if not exp_rows:
        return False
    pool: Counter = Counter()
    for r in pred_rows:
        amounts = tuple(to_float(r.get(f)) for f in _GT_AMOUNT_FIELDS)
        if any(a is None for a in amounts):
            continue
        pool[tuple(field_sig(a, tol) for a in amounts)] += 1
    for r in exp_rows:
        amounts = tuple(to_float(r.get(f)) for f in _GT_AMOUNT_FIELDS)
        if any(a is None for a in amounts):
            return False
        sig = tuple(field_sig(a, tol) for a in amounts)
        if pool[sig] <= 0:
            return False
        pool[sig] -= 1
    return True


def _should_forgive_gt_amount_subset(
    pipeline: Mapping[str, Any],
    exp_rows: Sequence[Mapping[str, Any]],
    pred_rows: Sequence[Mapping[str, Any]],
    tol: float,
) -> bool:
    """单表抽取且 GT 金额均被覆盖 → 整篇评分豁免（名称/次轴多抓不罚）。"""
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


def compare_one(
    predicted: Sequence[Mapping[str, Any]],
    expected: Sequence[Mapping[str, Any]],
    infocode: str,
    schema: Mapping[str, Any],
    pipeline: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    按 RELINFOCODE（infocode）逐公告对比：
    - 少抓 missing：GT 有、预测无（业务主键未出现）
    - 多抓 extra：预测有、GT 无
    - 抓错 value_wrong：业务主键对上，但金额/币种等字段不一致
    - 完全匹配 all_match：整行一致

    产品名：同报告期内先按名称（含包含关系）配对；对不上时若数值字段
    （收入/成本/币种/单位等）一致，仍视为匹配。

    报告期单向豁免（不双向）：
    应抓全报告期；若 GT 只收录最新期、预测多抓了历史期，不算 extra：
      A) 同产品名（或已由数值配对确认的别名）已在 GT 中出现，仅多了历史期；
      B) GT 仅单一报告期，且为合法产品的更早历史期（含比较年独有、当年停披露项）。
    GT 有而预测没有的报告期，仍算 missing。

    GT 金额子集豁免：
      pipeline.selected_count==1（定表来自单张表，非多表合并），且 GT 每条
      (MBREVENUE, MBCOST, GROSS_PROFIT) 都能在抽取中找到 → 整篇视为完全匹配（名称差异/次轴多抓不罚）。
    """
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
    missing_items: List[Dict[str, Any]] = []
    extra_items: List[Dict[str, Any]] = []
    wrong_items: List[Dict[str, Any]] = []
    field_ok: Counter = Counter()
    field_bad: Counter = Counter()

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
        exp_by_biz: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
        pred_by_biz: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = defaultdict(list)
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

    # 数值配对产生的产品名别名：历史期豁免时按别名回查 GT
    pred_to_gt_aliases: Dict[str, set] = defaultdict(set)
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

    # ---------- 单向豁免：仅「多抓历史报告期」----------
    # 口径：应抓全报告期；GT 有时只收录最新期。
    #   - A) 同产品名、报告期不在 GT 中 → 不算 extra
    #   - B) GT 仅单一报告期 + 合法产品更早历史期（含比较年独有产品）→ 不算 extra
    #   - GT 有而 pred 没有的报告期 → 仍算 missing（不双向豁免）
    #   - 不把豁免行计入 all_match，避免 all_match > db_count
    gt_periods_by_product: Dict[str, set] = defaultdict(set)
    gt_period_keys: set = set()
    gt_report_ends: List[datetime] = []
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
    forgiven_extra_items: List[Dict[str, Any]] = []
    _real_extra: List[Dict[str, Any]] = []
    for item in extra_items:
        pred_row = item.get("predicted") or {}
        pred_pn = norm(pred_row.get("PRODUCTNAME", ""))
        if _should_forgive_extra_period(
            pred_row,
            pred_pn,
            period_key_fields,
            tol,
            gt_periods_by_product,
            gt_only_single_period,
            gt_latest_report_end,
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
    forgiven_subset_items: List[Dict[str, Any]] = []
    # ---------- 整篇豁免：单表 + GT 金额均被抽取覆盖 ----------
    if (
        (missing > 0 or extra > 0 or value_wrong > 0)
        and _should_forgive_gt_amount_subset(pipe, exp_rows, pred_rows, tol)
    ):
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

    # ---------- 整篇豁免：选表来自 full_history（全部上期产品名命中）----------
    # 抽取为空 或 合计验证失败时不豁免 — 选表对了但提取质量不行，仍然是真实问题。
    forgiven_full_history = 0
    if (
        (missing > 0 or extra > 0 or value_wrong > 0)
        and pipe.get("from_full_history")
        and local_n > 0
        and pipe.get("total_validated", True)
    ):
        forgiven_full_history = 1
        # 同 forgiven_gt_amount_subset：整篇重置为完全匹配
        for item in missing_items:
            fi = dict(item)
            fi["root_cause"] = "forgiven_full_history"
            forgiven_subset_items.append(fi)
        for item in extra_items:
            fi = dict(item)
            fi["root_cause"] = "forgiven_full_history"
            forgiven_subset_items.append(fi)
        for item in wrong_items:
            fi = dict(item)
            fi["root_cause"] = "forgiven_full_history"
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

    # 豁免的历史期 / 金额子集 / full_history 多抓不参与 precision / comprehensive_hit 分母
    scored_local_n = max(local_n - forgiven_extra, 0)
    if forgiven_gt_amount_subset or forgiven_full_history:
        scored_local_n = db_n
    if missing == 0 and extra == 0 and value_wrong == 0:
        status = "完全匹配"
    else:
        parts: List[str] = []
        if missing:
            parts.append(f"少抓{missing}")
        if extra:
            parts.append(f"多抓{extra}")
        if value_wrong:
            parts.append(f"抓错{value_wrong}")
        status = " / ".join(parts)

    field_acc = {}
    for f in value_fields:
        ok, bad = int(field_ok[f]), int(field_bad[f])
        field_acc[f] = {
            "ok": ok,
            "mismatch": bad,
            "accuracy": ok / (ok + bad) if (ok + bad) else 0.0,
        }
    field_acc["__record__"] = {
        "ok": matched,
        "mismatch": value_wrong,
        "accuracy": matched / (matched + value_wrong) if (matched + value_wrong) else 0.0,
    }

    root_cause: Dict[str, int] = {}
    if zero_output and missing:
        root_cause[pipe_stage if pipe_stage != "success" else "empty_output"] = missing
    elif missing:
        root_cause["missing"] = missing
    if value_wrong:
        root_cause["value_wrong"] = value_wrong
    if extra:
        root_cause["extra"] = extra

    # 文档质量分类：区分自然匹配、豁免后匹配、有真实问题
    if status == "完全匹配":
        if forgiven_extra == 0 and forgiven_gt_amount_subset == 0 and forgiven_full_history == 0:
            doc_category = "完全匹配"
        else:
            doc_category = "豁免后匹配"
    else:
        doc_category = "需修复"

    return {
        "infocode": infocode,
        "relinfocode": infocode,
        "status": status,
        "doc_category": doc_category,
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
            "forgiven_full_history": forgiven_full_history,
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


def aggregate(docs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    db = sum(d["stats"]["db_count"] for d in docs)
    local = sum(d["stats"]["local_count"] for d in docs)
    matched = sum(d["stats"]["all_match"] for d in docs)
    missing = sum(d["stats"]["missing"] for d in docs)
    extra = sum(d["stats"]["extra"] for d in docs)
    value_diff = sum(d["stats"]["value_diff"] for d in docs)
    forgiven_extra = sum(int((d["stats"] or {}).get("forgiven_extra_periods") or 0) for d in docs)
    forgiven_subset_docs = sum(
        1 for d in docs if int((d.get("stats") or {}).get("forgiven_gt_amount_subset") or 0) > 0
    )
    forgiven_full_history_docs = sum(
        1 for d in docs if int((d.get("stats") or {}).get("forgiven_full_history") or 0) > 0
    )
    match_ok = sum(d["stats"]["match_no_value_diff"] for d in docs)
    roots: Counter = Counter()
    field_ok: Counter = Counter()
    field_bad: Counter = Counter()
    pipeline_stages: Counter = Counter()
    doc_categories: Counter = Counter()
    locate_ok_docs = 0
    locate_fail_codes: List[str] = []
    # 问题子类：按首要问题分类
    empty_output_docs = 0
    pure_missing_docs = 0
    pure_extra_docs = 0
    pure_value_wrong_docs = 0
    mixed_problem_docs = 0
    for d in docs:
        roots.update(d["stats"].get("missing_root_cause") or {})
        p = d.get("pipeline") or {}
        pipeline_stages[str(p.get("stage") or "unknown")] += 1
        if d.get("locate_ok"):
            locate_ok_docs += 1
        else:
            locate_fail_codes.append(str(d.get("infocode") or ""))
        for f, s in (d["stats"].get("field_accuracy_detail") or {}).items():
            if f == "__record__":
                continue
            field_ok[f] += int(s.get("ok", 0))
            field_bad[f] += int(s.get("mismatch", 0))
        # 文档分类
        cat = str(d.get("doc_category") or "需修复")
        doc_categories[cat] += 1
        if cat == "需修复":
            s = d.get("stats") or {}
            m, e, v = int(s.get("missing") or 0), int(s.get("extra") or 0), int(s.get("value_diff") or 0)
            if s.get("local_count", 0) == 0 or str(p.get("stage") or "") == "empty_output":
                empty_output_docs += 1
            elif m > 0 and e == 0 and v == 0:
                pure_missing_docs += 1
            elif m == 0 and e > 0 and v == 0:
                pure_extra_docs += 1
            elif m == 0 and e == 0 and v > 0:
                pure_value_wrong_docs += 1
            else:
                mixed_problem_docs += 1

    detail = {
        "__record__": {
            "ok": match_ok,
            "mismatch": value_diff,
            "accuracy": match_ok / (match_ok + value_diff) if (match_ok + value_diff) else 0.0,
        }
    }
    for f in sorted(set(field_ok) | set(field_bad)):
        ok, bad = int(field_ok[f]), int(field_bad[f])
        detail[f] = {"ok": ok, "mismatch": bad, "accuracy": ok / (ok + bad) if (ok + bad) else 0.0}

    # 有效抽取行数：排除已豁免的历史期行
    effective_local = local - forgiven_extra
    return {
        "doc_count": len(docs),
        "locate_ok_docs": locate_ok_docs,
        "locate_fail_docs": len(docs) - locate_ok_docs,
        "locate_fail_codes": sorted(locate_fail_codes),
        # 三层文档分类
        "natural_perfect_docs": int(doc_categories.get("完全匹配", 0)),
        "exempted_perfect_docs": int(doc_categories.get("豁免后匹配", 0)),
        "perfect_docs": int(doc_categories.get("完全匹配", 0)) + int(doc_categories.get("豁免后匹配", 0)),
        "problem_docs": int(doc_categories.get("需修复", 0)),
        # 问题子类（仅需修复文档）
        "empty_output_docs": empty_output_docs,
        "pure_missing_docs": pure_missing_docs,
        "pure_extra_docs": pure_extra_docs,
        "pure_value_wrong_docs": pure_value_wrong_docs,
        "mixed_problem_docs": mixed_problem_docs,
        "forgiven_docs": sum(
            1
            for d in docs
            if int((d.get("stats") or {}).get("forgiven_extra_periods") or 0) > 0
            or int((d.get("stats") or {}).get("forgiven_gt_amount_subset") or 0) > 0
            or int((d.get("stats") or {}).get("forgiven_full_history") or 0) > 0
        ),
        "forgiven_gt_amount_subset_docs": forgiven_subset_docs,
        "forgiven_full_history_docs": forgiven_full_history_docs,
        "db_count": db,
        "local_count": local,
        "effective_local": effective_local,
        "all_match": matched,
        "missing": missing,
        "extra": extra,
        "value_diff": value_diff,
        "forgiven_extra_periods": forgiven_extra,
        # 修正后的指标
        "recall": matched / db if db else 0.0,
        "precision": matched / effective_local if effective_local else 0.0,
        "field_accuracy": match_ok / (match_ok + value_diff) if (match_ok + value_diff) else 0.0,
        "comprehensive_hit": matched / max(db, effective_local, 1),
        "biz_hit": (matched + value_diff) / db if db else 0.0,
        "missing_root_cause": dict(roots),
        "pipeline_stages": dict(pipeline_stages),
        "field_accuracy_detail": detail,
    }


# ---------- html ----------

ROOT_CAUSE_DESC = {
    "locate_fail": "定位失败：未找到目标章节/cluster",
    "parse_fail": "解析失败：已定位但 extract 未产出 block",
    "select_fail": "成表失败：有 block 但 select 择优后为空",
    "format_fail": "格式化失败：已成表但输出行被过滤",
    "empty_output": "无输出：baseline 返回 0 行（阶段未上报）",
    "exception": "运行异常：extract_init 抛错或 status=failed",
    "unknown": "未知：未能判断失败阶段",
    "missing": "部分少抓：有输出但 GT 行未出现",
    "value_wrong": "抓错：产品行定位对了，字段值不一致",
    "extra": "多抓：预测有，GT 无",
    "forgiven_gt_amount_subset": "豁免：单表抽取且 GT 金额均被覆盖",
    "forgiven_full_history": "豁免：选表来自 full_history（全部上期产品名命中）",
}


PIPELINE_STAGE_DESC = {
    "success": "成功：定位→解析→成表→输出均完成",
    "locate_fail": "定位失败",
    "parse_fail": "解析失败",
    "select_fail": "成表失败",
    "format_fail": "格式化失败",
    "empty_output": "无输出",
    "exception": "运行异常",
    "unknown": "未知",
}

_STATUS_ROW_META = {
    "missing": ("少抓", "row-miss", "bad"),
    "extra": ("多抓", "row-extra", "warn"),
    "value_wrong": ("字段不一致", "row-wrong", "warn"),
}


def _root_cause_label(code: str) -> str:
    return ROOT_CAUSE_DESC.get(code, code)


def _pipeline_label(stage: str) -> str:
    label = PIPELINE_STAGE_DESC.get(stage, stage)
    return f"{stage} — {label}" if stage not in PIPELINE_STAGE_DESC else f"{stage}（{label}）"


def _source_pages_label(pipeline: Mapping[str, Any]) -> str:
    pages = pipeline.get("source_pages") if pipeline else None
    if not pages:
        return "—"
    if isinstance(pages, list):
        return ", ".join(str(p) for p in pages)
    return str(pages)


def _pct(x: float) -> str:
    return f"{float(x) * 100:.2f}%"


def _esc(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _badge(status: str) -> str:
    cls = "ok" if status == "完全匹配" else "bad"
    return f"<span class='badge {cls}' title='{_esc(status)}'>{_esc(status)}</span>"


def _badge_kind(label: str, kind: str) -> str:
    return f"<span class='badge {kind}'>{_esc(label)}</span>"


def _link(infocode: str, url: str, label: str = "") -> str:
    text = _esc(label or infocode)
    if not url:
        return f"<span class='mono'>{text}</span>"
    return f"<a class='pdf-link' href='{_esc(url)}' target='_blank' rel='noopener'>{text}</a>"


def _fmt_cell(v: Any) -> str:
    """报告展示：日期压成 YYYY-MM-DD，空值用 —。"""
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
    # 期间/ISO：按业务日历日展示（2023-12-31T16:00:00Z → 2024-01-01）
    cal = _business_calendar_date(s)
    if cal is not None and ("T" in s or "Z" in s or "+" in s[10:] or len(s) == 10):
        return cal
    return s


def _doc_has_problems(doc: Mapping[str, Any]) -> bool:
    s = doc.get("stats") or {}
    return bool(int(s.get("missing") or 0) or int(s.get("extra") or 0) or int(s.get("value_diff") or 0))


def _render_items_table(items: Sequence[Mapping[str, Any]], fields: Sequence[str], caption: str) -> str:
    """纯列表：只展示字段值，不做差异高亮/多少抓标记。"""
    heads = "".join(f"<th>{_esc(f)}</th>" for f in fields)
    body_rows: List[str] = []
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


def _render_doc_side_lists(doc: Mapping[str, Any], schema: Mapping[str, Any]) -> str:
    fields = resolve_fields(schema)
    # 展示顺序：产品名 / 期间 / 金额等
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
        f"{_render_items_table(ex_items, show_fields, '抽取')}"
        "</div>"
    )


def _render_problem_docs(
    docs: Sequence[Mapping[str, Any]],
    schema: Mapping[str, Any],
    url_of,
) -> str:
    cards: List[str] = []
    problem_docs = [d for d in docs if _doc_has_problems(d)]
    if not problem_docs:
        return "<div class='card'>全部公告完全匹配。</div>"

    for d in problem_docs:
        ic = str(d.get("infocode") or "")
        u = url_of(d)
        p = d.get("pipeline") or {}
        pages = _source_pages_label(p)
        extract_items = list(d.get("extract_items") or [])
        meta_line = (
            f"<div class='doc-meta'>"
            f"<span>抓取页：<strong>{_esc(pages)}</strong></span>"
            f"<span>抽取行：<strong>{len(extract_items)}</strong></span>"
            f"</div>"
        )
        accept_btn = ""
        if extract_items:
            accept_btn = (
                f"<button type='button' class='btn-accept' "
                f"onclick='acceptGt({json.dumps(ic, ensure_ascii=False)}, this)'>"
                f"采纳为 GT</button>"
            )
        cards.append(
            f"<div class='card doc-card' id='doc-{_esc(ic)}'>"
            f"<div class='doc-head'>"
            f"<h3 class='mono'>{_esc(ic)}</h3>"
            f"<div class='doc-actions'>{_link(ic, u, '打开PDF')}{accept_btn}</div>"
            f"</div>{meta_line}{_render_doc_side_lists(d, schema)}</div>"
        )
    hint = (
        "<div class='accept-bar'>"
        "<p class='muted' style='margin:0'>请通过本地服务打开本报告（跑批结束会自动启动）。"
        "核对右侧抽取后点「采纳为 GT」，会直接覆盖 "
        "<code>tasks/&lt;task&gt;/ground_truth.json</code> 中该公告整表。"
        "</p>"
        "</div>"
    )
    summary = (
        f"<p class='muted'>下列公告分别列出 GT 与抽取全量行（不做差异标记），共 "
        f"<strong>{len(problem_docs)}</strong> 篇。</p>{hint}"
    )
    return summary + "".join(cards)


def _render_fh_exempted_docs(
    docs: Sequence[Mapping[str, Any]],
    schema: Mapping[str, Any],
    url_of,
) -> str:
    """列出 Full History 豁免的文档及其抽取数据。"""
    fh_docs = [
        d for d in docs
        if int((d.get("stats") or {}).get("forgiven_full_history") or 0) > 0
    ]
    if not fh_docs:
        return ""

    fields = resolve_fields(schema)
    preferred = [
        "PRODUCTNAME", "STARTDATE", "REPORTDATE",
        "MBREVENUE", "MBCOST", "GROSS_PROFIT", "CURRENCY", "UNIT",
    ]
    show_fields = [f for f in preferred if f in fields]
    show_fields.extend(f for f in fields if f not in show_fields)

    cards: List[str] = []
    for d in fh_docs:
        ic = str(d.get("infocode") or "")
        u = url_of(d)
        p = d.get("pipeline") or {}
        pages = _source_pages_label(p)
        extract_items = list(d.get("extract_items") or [])
        cards.append(
            f"<div class='card doc-card' id='fh-{_esc(ic)}'>"
            f"<div class='doc-head'>"
            f"<h3 class='mono'>{_esc(ic)}</h3>"
            f"<div class='doc-actions'>{_link(ic, u, '打开PDF')}</div>"
            f"</div>"
            f"<div class='doc-meta'>"
            f"<span>抓取页：<strong>{_esc(pages)}</strong></span>"
            f"<span>抽取行：<strong>{len(extract_items)}</strong></span>"
            f"<span class='badge ok'>全量上期产品命中 → 自动豁免</span>"
            f"</div>"
            f"<div class='side-by-side'>"
            f"{_render_items_table(list(d.get('gt_items') or []), show_fields, 'GT')}"
            f"{_render_items_table(extract_items, show_fields, '抽取')}"
            f"</div></div>"
        )

    summary = (
        f"<p class='muted'>选表来自 full_history（全部上期产品名命中），"
        f"整体算完美匹配，共 <strong>{len(fh_docs)}</strong> 篇。</p>"
    )
    return summary + "".join(cards)


def render_html(meta: Mapping[str, Any], agg: Mapping[str, Any], docs: Sequence[Mapping[str, Any]], schema: Mapping[str, Any]) -> str:
    title = schema.get("title") or "PDF Baseline 跑批报告"

    def url_of(doc):
        if doc.get("pdf_url"):
            return doc["pdf_url"]
        path = doc.get("pdf_path") or ""
        return local_pdf_href(path) if path else ""

    detail = agg.get("field_accuracy_detail") or {}
    rec = detail.get("__record__", {})
    field_rows = [
        f"<tr><td><strong>记录级全字段</strong></td><td>{rec.get('ok',0)}</td>"
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
    ] or ["<tr><td colspan='3'>无缺失/抓错</td></tr>"]

    pipe_rows = [
        f"<tr><td class='mono'>{_esc(k)}</td><td>{v}</td><td>{_esc(PIPELINE_STAGE_DESC.get(k, k))}</td></tr>"
        for k, v in sorted((agg.get("pipeline_stages") or {}).items(), key=lambda x: -x[1])
    ] or ["<tr><td colspan='3'>无</td></tr>"]

    locate_fail_codes = sorted(str(c) for c in (agg.get("locate_fail_codes") or []))
    locate_fail_rows = "".join(
        f"<tr><td class='mono'>{_esc(code)}</td></tr>"
        for code in locate_fail_codes
    ) or "<tr><td class='muted'>无</td></tr>"
    locate_fail_body = (
        "<div class='card'><table><tr><th>公告</th></tr>"
        f"{locate_fail_rows}</table>"
        "<p class='muted'>判定：只看 GT 的 MBREVENUE/MBCOST 数值是否全部"
        "出现在 select_main_table 返回的选中行里，不看后续抽取结果。</p></div>"
    )

    problem_n = int(agg.get("problem_docs") or 0)
    fh_n = int(agg.get("forgiven_full_history_docs") or 0)
    task_name = str(meta.get("task") or "").strip()
    run_dir = str(meta.get("run_dir") or "").strip()
    compare_body = _render_problem_docs(docs, schema, url_of)
    fh_body = _render_fh_exempted_docs(docs, schema, url_of)

    # 问题子类统计行（按首要问题分类）
    problem_sub_lines = (
        f"<tr><td>抽取为空 (empty_output)</td><td>{agg.get('empty_output_docs',0)}</td>"
        f"<td>pipeline 定位/解析失败，无任何输出</td></tr>"
        f"<tr><td>纯抓错 (值不一致)</td><td>{agg.get('pure_value_wrong_docs',0)}</td>"
        f"<td>产品+期间定位正确，但 CURRENCY/GROSS_PROFIT 等字段值不一致</td></tr>"
        f"<tr><td>纯少抓 (漏产品行)</td><td>{agg.get('pure_missing_docs',0)}</td>"
        f"<td>抽取输出不完整，GT 有但抽取无</td></tr>"
        f"<tr><td>纯多抓 (多出非GT产品)</td><td>{agg.get('pure_extra_docs',0)}</td>"
        f"<td>抽取多出行，GT 中无对应产品</td></tr>"
        f"<tr><td>混合问题</td><td>{agg.get('mixed_problem_docs',0)}</td>"
        f"<td>同时存在少抓+多抓+抓错中的多种</td></tr>"
    )

    def section(name: str, body: str, open_: bool = True, section_id: str = "") -> str:
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
    alert('请用本地服务打开报告（跑批结束会自动启动），不要直接双击 HTML。');
    return;
  }}
  const task = window.__TASK__ || '';
  if (!task) {{ alert('报告缺少 task 名'); return; }}
  if (btn) {{ btn.disabled = true; btn.textContent = '采纳中…'; }}
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
    if (btn) {{ btn.textContent = '已采纳'; }}
    alert('已写入 GT：' + infocode + '（' + (data.row_count || 0) + ' 行）');
  }} catch (e) {{
    if (btn) {{ btn.disabled = false; btn.textContent = '采纳为 GT'; }}
    alert('采纳失败：' + (e && e.message ? e.message : e));
  }}
}}
</script>
</head><body>
<h1>{_esc(title)}</h1>
<p>批次 ID：<strong>{_esc(meta.get('batch_id'))}</strong> | 生成时间：{_esc(meta.get('generated_at'))} | PDF：{meta.get('pdf_count')} | 评估：{agg.get('doc_count')}</p>
<div class="kpi-grid card">
  <div class="kpi"><div>自然完全匹配</div><div class="value" style="color:#059669">{int(agg.get('natural_perfect_docs',0))}</div></div>
  <div class="kpi"><div>豁免后匹配</div><div class="value" style="color:#0891b2">{int(agg.get('exempted_perfect_docs',0))}</div></div>
  <div class="kpi"><div>需修复文档</div><div class="value" style="color:#dc2626">{int(agg.get('problem_docs',0))}</div></div>
  <div class="kpi"><div>定位率 Biz Hit</div><div class="value">{_pct(agg.get('biz_hit',0))}</div></div>
  <div class="kpi"><div>Recall (GT覆盖率)</div><div class="value">{_pct(agg.get('recall',0))}</div></div>
  <div class="kpi"><div>Precision (修正后)</div><div class="value">{_pct(agg.get('precision',0))}</div></div>
  <div class="kpi"><div>Full History 豁免</div><div class="value" style="color:#0891b2">{int(agg.get('forgiven_full_history_docs',0))}</div></div>
  <div class="kpi"><div>豁免历史期行数</div><div class="value" style="font-size:1.2rem">{int(agg.get('forgiven_extra_periods',0))}</div></div>
  <div class="kpi"><div>少抓 / 多抓 / 抓错</div><div class="value" style="font-size:1.2rem">{int(agg.get('missing',0))} / {int(agg.get('extra',0))} / {int(agg.get('value_diff',0))}</div></div>
</div>
<div class="card" style="margin-bottom:16px">
    <p style="margin:0 0 8px"><strong>说明：</strong></p>
    <ul style="margin:0;padding-left:20px;color:#4b5563;font-size:.86rem">
      <li><strong>自然完全匹配</strong>：GT 与抽取完全一致，无豁免，无需关注</li>
      <li><strong>豁免后匹配</strong>：因 GT 少标历史报告期 / 单表金额全覆盖 / full_history 选表而产生的差异被豁免，无需关注</li>
      <li><strong>需修复</strong>：豁免后仍存在少抓/多抓/抓错，需要排查抽取逻辑</li>
      <li><strong>定位率 Biz Hit</strong> = (完全匹配 + 抓错) / GT总行数 → 产品+期间定位的成功率（值不对但行定位对了也算）</li>
      <li><strong>Precision (修正后)</strong> = 完全匹配行 / (抽取总行 − 豁免历史期行) → 排除已知GT标注不足后的精确率</li>
    </ul>
  </div>
{section("字段准确率", f"<div class='card'><table><tr><th>字段</th><th>一致</th><th>不一致</th><th>准确率</th></tr>{''.join(field_rows)}</table></div>", open_=False)}
{section("抽取管道阶段（按公告）", f"<div class='card'><table><tr><th>阶段</th><th>公告数</th><th>说明</th></tr>{''.join(pipe_rows)}</table><p class='muted'>定位→解析→成表(select)→格式化；零输出时 missing 根因会继承该阶段。</p></div>", open_=False)}
{section(f"定位失败（GT数值未命中选中行 · {len(locate_fail_codes)} 篇）", locate_fail_body, open_=False, section_id="locate-fail-by-values")}
{section("差异根因（按 GT 行）", f"<div class='card'><table><tr><th>根因</th><th>行数</th><th>说明</th></tr>{''.join(root_rows)}</table></div>", open_=False)}
{section(f"需修复文档分类（按首要问题 · {problem_n} 篇）", f"<div class='card'><table><tr><th>首要问题</th><th>文档数</th><th>建议排查方向</th></tr>{problem_sub_lines}</table></div>", open_=True, section_id="problem-categories")}
{section(f"Full History 豁免（全部上期产品命中 · {fh_n} 篇）", fh_body, open_=True, section_id="fh-exempted")}
{section(f"逐公告对照（GT / 抽取并列 · {problem_n} 篇）", compare_body, open_=True, section_id="compare-section")}
</body></html>"""



# ---------- run ----------

_WORKER_BASELINE = None


def _init_worker(task_dir: str) -> None:
    """每个进程各自加载抽取模块（互不抢 GIL）。"""
    global _WORKER_BASELINE, _COMPARE_PRODUCT_VALIDATOR
    # Windows spawn workers re-import cold; neutralize optional heavy deps.
    import sys
    import types

    try:
        import numpy as _np

        if not hasattr(_np, "NaN"):
            _np.NaN = _np.nan
    except Exception:
        pass

    def _ensure(name, **attrs):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
        m = sys.modules[name]
        for k, v in attrs.items():
            setattr(m, k, v)
        return m

    _ensure("arelle", _version="stub")
    _ensure("arelle.WebCache", WebCache=type("WebCache", (), {}))
    if "main.app" not in sys.modules:
        _ensure("main")
        _ensure("main.app", get_abs_main_resources_directory=lambda: str(Path(__file__).resolve().parents[3]))
        _ensure("main.init", init_logger_config=lambda *a, **k: None)
        _ensure(
            "processor.task_management_handler",
            download_ocr_file_concurrency=lambda *a, **k: None,
            init_start_consume_queue=lambda *a, **k: None,
            init_parsing_task_current_concurrency=lambda *a, **k: None,
        )
    task_path = Path(task_dir)
    schema = load_schema(task_path)
    _WORKER_BASELINE = load_extractor(task_path, schema)
    _COMPARE_PRODUCT_VALIDATOR = _build_product_validator(task_path, schema)


def _run_one_job(
    job: Mapping[str, str],
    extract_configs: Mapping[str, Any],
) -> Dict[str, Any]:
    """执行单份公告 extract_init。"""
    code = str(job["infocode"])
    pdf_path = str(job["pdf_path"])
    pdf_url = str(job.get("pdf_url") or pdf_path)
    try:
        if _WORKER_BASELINE is None:
            raise RuntimeError("worker 未初始化 baseline")
        result = _WORKER_BASELINE.extract_init(
            pdf_path,
            code,
            "backtest",
            configs=dict(extract_configs),
            task_info_list=None,
        )
        if not isinstance(result, dict):
            raise TypeError("extract_init 必须返回 dict")
        return {
            "infocode": code,
            "pdf_path": pdf_path,
            "pdf_url": pdf_url,
            "result": result,
            "error": "",
            "traceback": "",
        }
    except Exception as exc:  # noqa: BLE001
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


def _chunk_jobs(jobs: Sequence[Dict[str, str]], n: int) -> List[List[Dict[str, str]]]:
    """先均分公告：轮询分到 n 份，空份丢掉。"""
    n = max(1, min(int(n), len(jobs)))
    chunks: List[List[Dict[str, str]]] = [[] for _ in range(n)]
    for i, job in enumerate(jobs):
        chunks[i % n].append(job)
    return [c for c in chunks if c]


def _run_job_chunk(
    worker_id: int,
    chunk: Sequence[Mapping[str, str]],
    extract_configs: Mapping[str, Any],
    schema: Mapping[str, Any],
    gt: Mapping[str, List[Dict[str, Any]]],
    inter_dir: str,
    run_dir: str,
) -> List[Dict[str, Any]]:
    """单个进程：只跑分给自己的公告，写各自中间文件。"""
    inter_path = Path(inter_dir)
    run_path = Path(run_dir)
    docs: List[Dict[str, Any]] = []
    total = len(chunk)
    for idx, job in enumerate(chunk, 1):
        code = job["infocode"]
        print(f"[w{worker_id} {idx}/{total}] {code}", flush=True)
        payload = _run_one_job(job, extract_configs)
        docs.append(_finalize_job(payload, schema, gt, inter_path, run_path))
    return docs


def _finalize_job(
    payload: Mapping[str, Any],
    schema: Mapping[str, Any],
    gt: Mapping[str, List[Dict[str, Any]]],
    inter_dir: Path,
    run_dir: Path,
) -> Dict[str, Any]:
    """写中间结果、对比、组装 per-doc 记录（按公告各写各的）。"""
    code = str(payload["infocode"])
    pdf_path = str(payload["pdf_path"])
    pdf_url = str(payload.get("pdf_url") or pdf_path)
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {
        "status": "failed",
        "infocode": code,
        "data": {},
        "error_message": "empty result",
    }
    err = str(payload.get("error") or "")
    tb = str(payload.get("traceback") or "")
    if tb:
        (run_dir / "logs").mkdir(exist_ok=True)
        (run_dir / "logs" / f"{code}.traceback.txt").write_text(tb, encoding="utf-8")

    pipeline = extract_pipeline(result, schema)
    if not pipeline:
        raise ValueError(f"{code} 抽取结果缺少 data.pipeline")
    if str(result.get("status") or "") == "failed":
        records = []
        err = err or str(result.get("error_message") or result.get("status"))
    elif result.get("status") == "success":
        records = extract_records(result, schema)
        err = ""
    else:
        records = extract_records(result, schema)
        err = err or str(result.get("error_message") or result.get("status") or "")

    write_json(inter_dir / f"{code}_extract.json", result)
    cmp = compare_one(records, gt.get(code, []), code, schema, pipeline=pipeline)
    from custom.service.HKCO_FN_PRODUCT_utils import (
        locate_ok_by_ratio,
        missing_gt_values_in_selected_lines,
    )
    selected_lines = pipeline.get("selected_lines") or []
    missing_loc_values = missing_gt_values_in_selected_lines(gt.get(code, []), selected_lines)
    first_line = str(selected_lines[0].get("text") or "") if selected_lines else ""
    cmp["locate_ok"] = (
        not missing_loc_values
        or locate_ok_by_ratio(gt.get(code, []), missing_loc_values)
        or "分部" in first_line
    )
    cmp["locate_missing_values"] = missing_loc_values
    cmp["selected_line_count"] = len(selected_lines)
    cmp["pdf_path"] = pdf_path
    cmp["pdf_url"] = pdf_url
    if "无法识别" in str(pipeline.get("message") or "") or "无法识别" in str(pipeline.get("stage_label") or ""):
        cmp["excluded"] = True
        cmp["doc_category"] = "无法识别"
        cmp["status"] = "无法识别"
    if err:
        cmp["error_message"] = err
    return cmp


def task_batch_root(task: str) -> Path:
    return BATCH_RUNS / task


def run_backtest(
    task: str,
    infocode: str = "",
    workers: int = DEFAULT_WORKERS,
    pdf_dir: str = "",
    cache_dir: str = "",
) -> Dict[str, Any]:
    task_dir = TASKS_DIR / task
    if not task_dir.is_dir():
        raise FileNotFoundError(task_dir)

    schema = load_schema(task_dir)
    # Command-line paths take precedence over schema defaults.
    for key, value in (
        ("pdf_dir", pdf_dir),
        ("cache_dir", cache_dir),
    ):
        if value:
            schema[key] = str(Path(value).expanduser().resolve())

    global _COMPARE_PRODUCT_VALIDATOR
    _COMPARE_PRODUCT_VALIDATOR = _build_product_validator(task_dir, schema)
    gt = load_gt(task_dir / "ground_truth.json")
    # 默认：GT 里全部公告都跑
    infocodes = select_infocodes(gt, infocode=infocode)
    jobs = find_jobs(infocodes, schema)
    if not jobs:
        raise RuntimeError(f"ground_truth.json 没有可跑的公告: task={task}")

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
        "run_dir": str(run_dir),
        "debug_dir": str(debug_dir),
        "debug_enabled": bool(schema.get("debug_enabled", True)),
    }

    chunks = _chunk_jobs(jobs, workers)
    n_workers = len(chunks)
    print(f"parallel jobs={len(jobs)} workers={n_workers} (process)", flush=True)
    for wid, chunk in enumerate(chunks):
        preview = ", ".join(j["infocode"] for j in chunk[:3])
        more = f" …+{len(chunk) - 3}" if len(chunk) > 3 else ""
        print(f"  w{wid}: {len(chunk)} -> {preview}{more}", flush=True)

    docs: List[Dict[str, Any]] = []
    inter_dir_s = str(inter_dir)
    run_dir_s = str(run_dir)
    task_dir_s = str(task_dir)
    if n_workers == 1:
        _init_worker(task_dir_s)
        docs.extend(
            _run_job_chunk(0, chunks[0], extract_configs, schema, gt, inter_dir_s, run_dir_s)
        )
    else:
        with ProcessPoolExecutor(
            max_workers=n_workers,
            initializer=_init_worker,
            initargs=(task_dir_s,),
        ) as pool:
            futures = [
                pool.submit(
                    _run_job_chunk,
                    wid,
                    chunk,
                    extract_configs,
                    schema,
                    gt,
                    inter_dir_s,
                    run_dir_s,
                )
                for wid, chunk in enumerate(chunks)
            ]
            for done, fut in enumerate(as_completed(futures), 1):
                docs.extend(fut.result())
                print(f"[main] {done}/{len(futures)} worker chunks done", flush=True)

    docs = [d for d in docs if not d.get("excluded")]
    docs.sort(key=lambda d: str(d.get("infocode") or ""))
    print(f"[main] all chunks done ({len(docs)} docs); writing metrics/report...", flush=True)

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


def accept_extract_as_gt(
    task_dir: Path,
    infocode: str,
    rows: Sequence[Mapping[str, Any]],
    schema: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """用抽取行覆盖 ground_truth.json 中该公告（整表替换）。"""
    code = norm(infocode)
    if not code:
        raise ValueError("infocode 为空")
    schema = schema or load_schema(task_dir)
    fields = resolve_fields(schema)
    projected = [project_fields(r, fields) for r in rows if isinstance(r, Mapping)]
    projected = [
        _normalize_row_period_dates(r)
        for r in projected
        if any(v is not None and str(v).strip() != "" for v in r.values())
    ]
    if not projected:
        raise ValueError("抽取为空，拒绝覆盖 GT")
    gt_path = task_dir / "ground_truth.json"
    gt = load_gt(gt_path)
    # 尽量保持原有 key 顺序：已有则原地替换，否则追加
    if code in gt:
        gt[code] = projected
        ordered = gt
    else:
        ordered = dict(gt)
        ordered[code] = projected
    write_json(gt_path, ordered)
    return {
        "infocode": code,
        "row_count": len(projected),
        "gt_path": str(gt_path.resolve()),
    }


def load_extract_rows_from_run(run_dir: Path, infocode: str, schema: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """从 batch intermediates 读抽取行。"""
    path = run_dir / "intermediates" / f"{infocode}_extract.json"
    if not path.exists():
        raise FileNotFoundError(f"找不到抽取中间结果: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"抽取文件格式错误: {path}")
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


def resolve_run_dir(task: str, run_dir: str = "", infocode: str = "") -> Path:
    """解析批次目录：显式 --run-dir，否则取含该公告抽取结果的最近一次跑批。"""
    if run_dir:
        p = Path(run_dir)
        candidates = [p]
        if not p.is_absolute():
            candidates.extend([ROOT / p, BATCH_RUNS / task / p, BATCH_RUNS / p])
        for c in candidates:
            if c.is_dir():
                return c.resolve()
        raise FileNotFoundError(f"找不到跑批目录: {run_dir}")

    base = BATCH_RUNS / task
    if not base.is_dir():
        raise FileNotFoundError(f"找不到批次根目录: {base}")
    runs = sorted([d for d in base.iterdir() if d.is_dir()], key=lambda d: d.name, reverse=True)
    code = norm(infocode)
    if code:
        for d in runs:
            if (d / "intermediates" / f"{code}_extract.json").exists():
                return d.resolve()
        raise FileNotFoundError(f"没有任何批次含抽取结果: {code}")
    if not runs:
        raise FileNotFoundError(f"批次目录为空: {base}")
    return runs[0].resolve()


def regen_report_html(run_dir: Path) -> Path:
    """根据已有 per_doc/summary 重写 report.html。"""
    run_dir = Path(run_dir).resolve()
    task = run_dir.parent.name
    task_dir = TASKS_DIR / task
    schema = load_schema(task_dir)
    per = json.loads((run_dir / "metrics" / "per_doc.json").read_text(encoding="utf-8"))
    docs = per.get("rows") if isinstance(per, Mapping) else per
    if not isinstance(docs, list):
        raise ValueError(f"per_doc.json 格式错误: {run_dir}")
    agg_path = run_dir / "metrics" / "summary.json"
    agg = aggregate(docs)  # 永远重新计算（支持统计逻辑升级）
    write_json(agg_path, agg)
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



def main() -> int:
    parser = argparse.ArgumentParser(description="PDF baseline 回测：默认跑 ground_truth.json 全部公告")
    parser.add_argument("--task", required=True, help="任务名，如 HKCO_FN_PRODUCT")
    parser.add_argument("--infocode", default="", help="只跑 / 只采纳某一个公告")
    parser.add_argument("--pdf-dir", default="", help="PDF 目录（覆盖 schema.json）")
    parser.add_argument("--cache-dir", default="", help="缓存目录（覆盖 schema.json）")
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"并行进程数（默认 {DEFAULT_WORKERS}）",
    )
    parser.add_argument(
        "--accept-gt",
        action="store_true",
        help="用跑批抽取结果覆盖 tasks/<task>/ground_truth.json 中对应公告（整表替换）",
    )
    parser.add_argument(
        "--run-dir",
        default="",
        help="--accept-gt 时指定批次目录；默认自动找最近一次",
    )
    args = parser.parse_args()

    if args.accept_gt:
        if not args.infocode:
            parser.error("--accept-gt 需要同时指定 --infocode")
        task_dir = TASKS_DIR / args.task
        if not task_dir.is_dir():
            parser.error(f"找不到 task 目录: {task_dir}")
        schema = load_schema(task_dir)
        run_dir = resolve_run_dir(args.task, args.run_dir, args.infocode)
        rows = load_extract_rows_from_run(run_dir, args.infocode, schema)
        result = accept_extract_as_gt(task_dir, args.infocode, rows, schema=schema)
        print(
            json.dumps(
                {"ok": True, "run_dir": str(run_dir), **result},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    result = run_backtest(
        args.task,
        infocode=args.infocode,
        workers=max(1, args.workers),
        pdf_dir=args.pdf_dir,
        cache_dir=args.cache_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(f"\nReport: {result['report_html']}")
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
