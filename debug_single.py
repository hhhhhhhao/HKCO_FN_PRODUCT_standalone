"""调试单个 infocode 的 extract_init 流程"""
import os, sys
from pathlib import Path

# 将缺失的本地调试目录转换为清晰的异常信息。
_original_listdir = os.listdir
def _safe_listdir(path):
    if os.path.exists(path):
        return _original_listdir(path)
    raise FileNotFoundError(f"[patched] {path}")  # 抛异常让调用方跳过
os.listdir = _safe_listdir

sys.path.insert(0, os.path.dirname(__file__))

import json
from custom.service.EAPS_HKCO_FN_PRODUCT import extract_init

INFO_CODE = "AN202502281643617359"
PDF_PATH = str(Path(__file__).resolve().parent / "pdf" / f"{INFO_CODE}.pdf")

with open("all_selected.json") as f:
    all_sel = json.load(f)

item = all_sel.get(INFO_CODE, {})
print(f"Title: {item.get('title', 'N/A')}")
print(f"PDF: {PDF_PATH}")
print()

result = extract_init(
    PDF_PATH, INFO_CODE, "debug",
    configs={
        "cache_dir": "parse_cache",
        "force_reparse": False,
        "pipeline_debug": True,
    },
    task_info_list=None,
)

if isinstance(result, dict):
    data = result.get("data", {})
    pipe = data.get("pipeline", {}) if isinstance(data, dict) else {}
    records = data.get("records", []) if isinstance(data, dict) else []
    print(f"\nStatus: {result.get('status')}")
    print(f"Pipeline stage: {pipe.get('stage', 'N/A')}")
    print(f"Selected count: {pipe.get('selected_count', 0)}")
    print(f"Extracted records ({len(records)}):")
    for r in records[:20]:
        print(f"  {r}")
