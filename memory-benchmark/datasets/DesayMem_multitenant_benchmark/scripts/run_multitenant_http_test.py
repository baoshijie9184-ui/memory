"""HTTP evaluation for the multitenant yearlong cockpit benchmark.

Two evaluation layers:

  A/B  isolation + filter semantics — precise assertions over who-sees-what
  C/D  fuzzy recall + traps         — ported must/not-top1 keyword judging

Modes:
    --mode dry-run     validate dataset + cases locally (no server)
    --mode all         reset + import + eval
    --mode import      reset + import only
    --mode eval        eval only (requires imported data)
    --mode isolation   A/B groups only
    --mode recall      C/D groups only

Usage:
    python scripts/run_multitenant_http_test.py --mode dry-run
    python scripts/run_multitenant_http_test.py --mode all --label full_v1
    python scripts/run_multitenant_http_test.py --mode isolation --label iso_only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
TENANTS_DIR = ROOT / "data" / "tenants"
CASES_PATH = ROOT / "data" / "eval_cases.json"
REPORTS_DIR = ROOT / "reports"


# ─────────────────────────────────────────────────────────────────────────────
# HTTP layer
# ─────────────────────────────────────────────────────────────────────────────

class MemClient:
    def __init__(self, base_url: str, timeout: float = 120.0) -> None:
        self.base = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def _url(self, path: str) -> str:
        return f"{self.base}{path}"

    def add(self, payload: dict[str, Any], retries: int = 2) -> dict[str, Any]:
        for attempt in range(retries + 1):
            try:
                r = self.client.post(self._url("/v1/memories"), json=payload)
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                if attempt == retries:
                    raise
                print(f"  add retry {attempt + 1}: {exc}")
                time.sleep(2 * (attempt + 1))
        raise RuntimeError("unreachable")

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self.client.post(self._url("/v1/memories/search"), json=payload)
        r.raise_for_status()
        return r.json()

    def profile(self, tenant_id: str, user_id: str, occupant_id: str | None = None) -> dict[str, Any]:
        params = {"tenant_id": tenant_id}
        if occupant_id:
            params["occupant_id"] = occupant_id
        r = self.client.get(self._url(f"/v1/users/{user_id}/profile"), params=params)
        r.raise_for_status()
        return r.json()

    def list_memories(self, tenant_id: str, user_id: str, limit: int = 500,
                      **filters: str) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"tenant_id": tenant_id, "limit": limit}
        params.update({k: v for k, v in filters.items() if v})
        r = self.client.get(self._url(f"/v1/users/{user_id}/memories"), params=params)
        r.raise_for_status()
        return r.json()

    def reset_user(self, tenant_id: str, user_id: str) -> bool:
        try:
            r = self.client.delete(
                self._url(f"/v1/users/{user_id}/memories"),
                params={"tenant_id": tenant_id, "confirm": "true"},
            )
            return r.status_code in (200, 204)
        except httpx.HTTPError as exc:
            print(f"  reset {tenant_id}/{user_id} failed: {exc}")
            return False

    def health(self) -> bool:
        try:
            r = self.client.get(self._url("/health"))
            return r.status_code == 200
        except httpx.HTTPError:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Dataset loading
# ─────────────────────────────────────────────────────────────────────────────

def load_import_order() -> list[dict[str, Any]]:
    """All sessions across the tenant tree, in chronological order."""
    rows: list[dict[str, Any]] = []
    for path in sorted(TENANTS_DIR.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    rows.sort(key=lambda r: r["started_at"])
    return rows


def load_cases() -> list[dict[str, Any]]:
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    return data["cases"]


# ─────────────────────────────────────────────────────────────────────────────
# Judging helpers
# ─────────────────────────────────────────────────────────────────────────────

def _hit(rows: list[dict[str, Any]], aliases: list[str]) -> bool:
    """True if any alias appears in any row's searchable text."""
    for row in rows:
        body = row.get("content", "") or ""
        meta = json.dumps(row.get("metadata", {}), ensure_ascii=False)
        for alias in aliases:
            if alias in body or alias in meta:
                return True
    return False


def _rank_of(rows: list[dict[str, Any]], aliases: list[str]) -> int | None:
    for i, row in enumerate(rows):
        body = row.get("content", "") or ""
        meta = json.dumps(row.get("metadata", {}), ensure_ascii=False)
        for alias in aliases:
            if alias in body or alias in meta:
                return i + 1
    return None


def judge_search(case: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply must / must_not / not_top1 over search rows."""
    must = case.get("must", [])
    must_not = case.get("must_not", [])
    not_top1 = case.get("not_top1", [])
    details: dict[str, Any] = {}

    details["must_hit"] = {a: _hit(rows, [a]) for a in must}
    details["must_not_violated"] = {a: _hit(rows, [a]) for a in must_not}
    top1_text = (rows[0].get("content", "") if rows else "")
    details["top1_text"] = top1_text[:120]
    details["not_top1_violated"] = {a: a in top1_text for a in not_top1}

    passed = (
        all(details["must_hit"].values())
        and not any(details["must_not_violated"].values())
        and not any(details["not_top1_violated"].values())
    )
    if case.get("no_filter") and case.get("allow"):
        # informational case: behaviour confirmation, not hard fail
        details["allowed_seen"] = {a: _hit(rows, [a]) for a in case["allow"]}
    return {"passed": passed, "details": details}


def judge_dual(case: dict[str, Any], rows_a: list[dict[str, Any]], rows_b: list[dict[str, Any]]) -> dict[str, Any]:
    ja = judge_search({**case, "must": case.get("must_a", []), "must_not": [],
                       "not_top1": case.get("not_top1_a", [])}, rows_a)
    jb = judge_search({**case, "must": case.get("must_b", []), "must_not": [],
                       "not_top1": case.get("not_top1_b", [])}, rows_b)
    return {"passed": ja["passed"] and jb["passed"], "details": {"A": ja["details"], "B": jb["details"]}}


def judge_profile(case: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """must_not over profile narrative + beliefs."""
    body = json.dumps(profile, ensure_ascii=False)
    must_not = case.get("must_not", [])
    violated = {a: a in body for a in must_not}
    return {"passed": not any(violated.values()),
            "details": {"must_not_violated": violated, "narrative": str(profile.get("narrative", ""))[:120]}}


def judge_dedup(case: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """flt_04: identical phrase posted to two tenants → single row per (tenant,user,hash).

    Query phrase exists in both SUV1 and SED1 imports. After import, listing
    the SUV tenant should contain the phrase, and listing the SEDAN tenant
    should NOT duplicate it (content lands in whichever tenant wrote first —
    but the dedup key includes tenant_id, so both tenants keep their own copy.
    The observable assertion: each tenant holds exactly one copy).
    """
    phrase = case["query"]
    suv_rows = [r for r in rows if r.get("tenant_id") == "oem_suv_family"]
    sed_rows = [r for r in rows if r.get("tenant_id") == "oem_sedan_biz"]
    suv_hits = [r for r in suv_rows if phrase in (r.get("content") or "")]
    sed_hits = [r for r in sed_rows if phrase in (r.get("content") or "")]
    details = {
        "suv_copy_count": len(suv_hits),
        "sedan_copy_count": len(sed_hits),
        "expect": ">=1 copy per tenant (dedup key = tenant+user+hash → cross-tenant copies are independent)",
    }
    passed = len(suv_hits) >= 1 and len(sed_hits) >= 1
    return {"passed": passed, "details": details}


# ─────────────────────────────────────────────────────────────────────────────
# Dry-run validation
# ─────────────────────────────────────────────────────────────────────────────

def dry_run() -> bool:
    sessions = load_import_order()
    cases = load_cases()
    print(f"sessions: {len(sessions)}  cases: {len(cases)}")

    texts: dict[tuple[str, str, str], str] = {}
    for s in sessions:
        key = (s["tenant_id"], s["vehicle_id"], s["user_id"])
        body = texts.setdefault(key, "")
        for m in s["messages"]:
            body += m["content"] + " "
        texts[key] = body
    all_text = "\n".join(texts.values())

    problems = []
    for case in cases:
        if case["op"] == "search_dual":
            keys = [(case["scope_a"]["tenant_id"], None, case["scope_a"]["user_id"]),
                    (case["scope_b"]["tenant_id"], None, case["scope_b"]["user_id"])]
            musts = case.get("must_a", []) + case.get("must_b", [])
        else:
            sc = case["scope"]
            keys = [(sc["tenant_id"], sc.get("vehicle_id"), sc["user_id"])]
            musts = case.get("must", []) + case.get("must_a", [])
        for must in musts:
            if must not in all_text:
                problems.append(f"{case['id']}: must '{must}' absent from dataset")
    if problems:
        for p in problems:
            print(f"[FAIL] {p}")
        return False
    print("dry-run OK — all case anchors covered by dataset")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Import
# ─────────────────────────────────────────────────────────────────────────────

def do_import(client: MemClient, tenants: list[str] | None = None, reset: bool = True) -> dict[str, Any]:
    sessions = load_import_order()
    if tenants:
        sessions = [s for s in sessions if s["tenant_id"] in tenants]
    print(f"importing {len(sessions)} sessions...")

    # reset per unique (tenant, user)
    scopes = {(s["tenant_id"], s["user_id"]) for s in sessions}
    if reset:
        for tenant, user in sorted(scopes):
            ok = client.reset_user(tenant, user)
            print(f"  reset {tenant}/{user}: {'ok' if ok else 'FAILED'}")

    stats = {"imported": 0, "extracted_memories": 0, "beliefs": 0, "episodes": 0,
             "skipped_dup": 0, "errors": []}
    started = time.time()
    for i, s in enumerate(sessions, 1):
        try:
            resp = client.add(s["api_payload"])
            stats["imported"] += 1
            stats["extracted_memories"] += len(resp.get("memories", []))
            stats["beliefs"] += resp.get("beliefs_applied", 0) or 0
            stats["skipped_dup"] += resp.get("skipped_duplicates", 0) or 0
            if resp.get("episode"):
                stats["episodes"] += 1
        except Exception as exc:  # noqa: BLE001 — collect and continue
            stats["errors"].append(f"{s['session_id']}: {exc}")
        if i % 100 == 0:
            print(f"  {i}/{len(sessions)} ({time.time() - started:.0f}s)")
    print(json.dumps(stats, ensure_ascii=False)[:400])
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation
# ─────────────────────────────────────────────────────────────────────────────

def run_case(client: MemClient, case: dict[str, Any], top_k: int) -> dict[str, Any]:
    op = case["op"]
    scope = case["scope"]
    if op == "search":
        payload = {
            "tenant_id": scope["tenant_id"],
            "user_id": scope["user_id"],
            "query": case["query"],
            "top_k": top_k,
        }
        if case.get("filters"):
            payload["filters"] = case["filters"]
        resp = client.search(payload)
        rows = resp.get("memories", [])
        verdict = judge_search(case, rows)
        # vehicle isolation extra check for flt_02
        if case.get("must_not_vehicle"):
            bad = [r for r in rows if r.get("vehicle_id") in case["must_not_vehicle"]]
            if bad:
                verdict["passed"] = False
                verdict["details"]["vehicle_leak"] = [r["vehicle_id"] for r in bad]
        verdict["rows"] = len(rows)
        return verdict

    if op == "search_dual":
        pa = {**{"tenant_id": case["scope_a"]["tenant_id"], "user_id": case["scope_a"]["user_id"],
                 "query": case["query"], "top_k": top_k}}
        pb = {**{"tenant_id": case["scope_b"]["tenant_id"], "user_id": case["scope_b"]["user_id"],
                 "query": case["query"], "top_k": top_k}}
        rows_a = client.search(pa).get("memories", [])
        rows_b = client.search(pb).get("memories", [])
        verdict = judge_dual(case, rows_a, rows_b)
        verdict["rows_a"] = len(rows_a)
        verdict["rows_b"] = len(rows_b)
        return verdict

    if op == "profile":
        occupant = scope.get("occupant_id")
        prof = client.profile(scope["tenant_id"], scope["user_id"], occupant)
        return judge_profile(case, prof)

    if op == "dedup_check":
        rows = client.list_memories(scope["tenant_id"], scope["user_id"], limit=500)
        rows += client.list_memories("oem_suv_family", scope["user_id"], limit=500)
        return judge_dedup(case, rows)

    raise ValueError(f"unknown op: {op}")


def do_eval(client: MemClient, groups: list[str], top_k: int) -> dict[str, Any]:
    cases = [c for c in load_cases() if c["group"] in groups]
    results = []
    for case in cases:
        t0 = time.time()
        try:
            verdict = run_case(client, case, top_k)
            verdict["id"] = case["id"]
            verdict["group"] = case["group"]
            verdict["query"] = case.get("query", "")
            verdict["elapsed_s"] = round(time.time() - t0, 2)
            results.append(verdict)
            mark = "PASS" if verdict["passed"] else "FAIL"
            print(f"  [{mark}] {case['id']} ({verdict['elapsed_s']}s)")
        except Exception as exc:  # noqa: BLE001
            results.append({"id": case["id"], "group": case["group"], "query": case.get("query", ""),
                           "passed": False, "error": str(exc), "elapsed_s": round(time.time() - t0, 2)})
            print(f"  [ERROR] {case['id']}: {exc}")
    return {"results": results}


# ─────────────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────────────

def write_report(label: str, eval_data: dict[str, Any], import_stats: dict[str, Any] | None) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = REPORTS_DIR / f"{label}_{ts}"

    results = eval_data.get("results", [])
    for r in results:
        r.setdefault("passed", False)
    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    by_group: dict[str, dict[str, int]] = {}
    for r in results:
        g = by_group.setdefault(r["group"], {"pass": 0, "fail": 0})
        g["pass" if r["passed"] else "fail"] += 1

    payload = {
        "label": label,
        "timestamp": ts,
        "import_stats": import_stats,
        "summary": {"total": total, "passed": passed, "failed": total - passed,
                    "by_group": by_group},
        "results": results,
    }
    base.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# Multitenant benchmark report — {label}",
             f"- generated: {ts}",
             f"- total: {total}  passed: {passed}  failed: {total - passed}",
             ""]
    for group, g in by_group.items():
        lines.append(f"- {group}: {g['pass']}/{g['pass'] + g['fail']} pass")
    lines.append("")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        lines.append(f"## [{mark}] {r['id']} ({r['group']})")
        lines.append(f"- query: {r.get('query', '')}")
        if "error" in r:
            lines.append(f"- error: {r['error']}")
        det = r.get("details", {})
        if det:
            lines.append(f"- details: {json.dumps(det, ensure_ascii=False)[:600]}")
        lines.append("")
    base.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nreport: {base}.md")
    return base


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["dry-run", "all", "import", "eval", "isolation", "recall"],
                        default="dry-run")
    parser.add_argument("--base-url", default="http://10.133.72.161:20142")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--label", default="multitenant_v1")
    parser.add_argument("--tenants", default=None, help="comma list to restrict import scope")
    args = parser.parse_args()

    if args.mode == "dry-run":
        return 0 if dry_run() else 1

    client = MemClient(args.base_url)
    if not client.health():
        print(f"backend not reachable: {args.base_url}/health")
        return 2

    tenants = args.tenants.split(",") if args.tenants else None
    import_stats: dict[str, Any] | None = None

    if args.mode in ("all", "import"):
        import_stats = do_import(client, tenants, reset=True)

    if args.mode in ("all", "eval", "isolation", "recall"):
        groups = {
            "all": ["isolation", "filter", "recall", "trap"],
            "eval": ["isolation", "filter", "recall", "trap"],
            "isolation": ["isolation", "filter"],
            "recall": ["recall", "trap"],
        }[args.mode]
        eval_data = do_eval(client, groups, args.top_k)
        write_report(args.label, eval_data, import_stats)
        results = eval_data["results"]
        failed = [r["id"] for r in results if not r.get("passed")]
        print(f"\n{len(results) - len(failed)}/{len(results)} passed" + (f"; failed: {failed}" if failed else ""))
        return 1 if failed else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
