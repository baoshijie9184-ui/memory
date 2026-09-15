"""Unit tests for multitenant eval judging logic (no server required)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "run_mt", ROOT / "scripts" / "run_multitenant_http_test.py")
run_mt = importlib.util.module_from_spec(spec)
sys.modules["run_mt"] = run_mt
spec.loader.exec_module(run_mt)


def _rows(*contents: str, vehicle_id: str = "veh_suv_001") -> list[dict]:
    return [{"content": c, "vehicle_id": vehicle_id, "metadata": {}} for c in contents]


# ── judge_search ────────────────────────────────────────────────────────────

def test_must_hit_pass():
    case = {"must": ["童梦森林"], "must_not": [], "not_top1": []}
    rows = _rows("用户多次前往童梦森林亲子餐厅", "用户收藏童趣岛")
    v = run_mt.judge_search(case, rows)
    assert v["passed"] is True


def test_must_missing_fails():
    case = {"must": ["西冷"], "must_not": [], "not_top1": []}
    rows = _rows("用户喜欢粤菜")
    v = run_mt.judge_search(case, rows)
    assert v["passed"] is False


def test_must_not_violation_fails():
    case = {"must": [], "must_not": ["拿铁"], "not_top1": []}
    rows = _rows("用户偏好少糖拿铁")
    v = run_mt.judge_search(case, rows)
    assert v["passed"] is False


def test_not_top1_only_fails():
    """Trap in Top1 position must fail even if present lower in the list."""
    case = {"must": [], "must_not": [], "not_top1": ["25度"]}
    rows = _rows("偶发空调25度", "通勤空调23度")
    v = run_mt.judge_search(case, rows)
    assert v["passed"] is False
    assert v["details"]["top1_text"].startswith("偶发空调25度")


def test_not_top1_lower_rank_passes():
    case = {"must": ["23度"], "must_not": [], "not_top1": ["25"]}
    rows = _rows("通勤空调23度风量2档", "有一次调到25度")
    v = run_mt.judge_search(case, rows)
    assert v["passed"] is True


def test_must_checked_in_metadata_too():
    case = {"must": ['"temperature_c": 23'], "must_not": [], "not_top1": []}
    rows = [{"content": "通勤空调设置", "vehicle_id": "v1",
             "metadata": {"operations": [{"parameters": {"temperature_c": 23}}]}}]
    v = run_mt.judge_search(case, rows)
    assert v["passed"] is True


# ── judge_dual (iso_05 same query different users) ─────────────────────────

def test_dual_distinct_answers():
    case = {"must_a": ["童梦森林"], "not_top1_a": ["麦田"],
            "must_b": ["麦田"], "not_top1_b": ["童梦森林"]}
    rows_a = _rows("经常去童梦森林亲子餐厅")
    rows_b = _rows("经常去麦田亲子餐厅")
    v = run_mt.judge_dual(case, rows_a, rows_b)
    assert v["passed"] is True


def test_dual_same_answer_fails():
    """If B returns A's restaurant, isolation is broken."""
    case = {"must_a": ["童梦森林"], "not_top1_a": ["麦田"],
            "must_b": ["麦田"], "not_top1_b": ["童梦森林"]}
    rows_a = _rows("经常去童梦森林亲子餐厅")
    rows_b = _rows("经常去童梦森林亲子餐厅")  # leak!
    v = run_mt.judge_dual(case, rows_a, rows_b)
    assert v["passed"] is False


# ── judge_profile ───────────────────────────────────────────────────────────

def test_profile_no_leak():
    case = {"must_not": ["粤剧"]}
    prof = {"narrative": "用户通勤偏好：空调23度", "beliefs": []}
    v = run_mt.judge_profile(case, prof)
    assert v["passed"] is True


def test_profile_leak_fails():
    case = {"must_not": ["粤剧"]}
    prof = {"narrative": "用户喜欢粤剧音量12", "beliefs": []}
    v = run_mt.judge_profile(case, prof)
    assert v["passed"] is False


# ── judge_dedup (flt_04) ────────────────────────────────────────────────────

def test_dedup_each_tenant_one_copy():
    case = {"query": "去公司，空调和音乐照平时通勤习惯来"}
    rows = [
        {"content": "去公司，空调和音乐照平时通勤习惯来", "tenant_id": "oem_suv_family"},
        {"content": "去公司，空调和音乐照平时通勤习惯来", "tenant_id": "oem_sedan_biz"},
    ]
    v = run_mt.judge_dedup(case, rows)
    assert v["passed"] is True
    assert v["details"]["suv_copy_count"] == 1
    assert v["details"]["sedan_copy_count"] == 1


def test_dedup_missing_tenant_copy_fails():
    case = {"query": "去公司，空调和音乐照平时通勤习惯来"}
    rows = [{"content": "去公司，空调和音乐照平时通勤习惯来", "tenant_id": "oem_suv_family"}]
    v = run_mt.judge_dedup(case, rows)
    assert v["passed"] is False


# ── dataset dry-run sanity ──────────────────────────────────────────────────

def test_dataset_cases_loaded():
    cases = run_mt.load_cases()
    ids = [c["id"] for c in cases]
    assert "iso_05" in ids and "flt_04" in ids and "trap_01" in ids
    groups = {c["group"] for c in cases}
    assert groups == {"isolation", "filter", "recall", "trap"}


def test_import_order_sorted_and_complete():
    rows = run_mt.load_import_order()
    assert len(rows) > 700
    starts = [r["started_at"] for r in rows]
    assert starts == sorted(starts)
    # every row carries a full api_payload
    assert all("api_payload" in r and "occupant_id" in r["api_payload"] for r in rows)


def test_dry_run_passes():
    assert run_mt.dry_run() is True
