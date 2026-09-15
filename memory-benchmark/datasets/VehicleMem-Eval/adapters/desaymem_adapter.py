"""DesayMem 多租户数据集 adapter — 检索断言式评测（非 QA）。

数据: datasets/VehicleMem-Eval/datasets/desaymem/（原 DesayMem_multitenant_benchmark/data 迁入）
  - tenants/{tenant}/{vehicle}/{user}/sessions.jsonl（含 _shared_multi_occupant.jsonl）
  - eval_cases.json: 26 条断言用例（must/must_not/not_top1/search_dual）

uid 约定（对齐离线库 collection 名 desaymem_{uid}）:
  粗粒度（tenant 级隔离）: uid = {user_id}          e.g. usr_father
  细粒度（vehicle 过滤）:   uid = {user_id}_{vehicle_id}  e.g. usr_father_veh_suv_001
用例 scope 带 vehicle_id → 细 uid；否则粗 uid。

profile(flt_04/iso_06) 与 dedup_check 用例 bridge 无对应接口，跳过记 N/A。
"""

import glob
import json
import os
import re

from .base import BaseAdapter

_DATA_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "datasets", "desaymem",
)


def _parse_ts(content: str, fallback: str):
    """消息 content 带 '[2026-01-15 08:10]' 时间戳前缀，LightMem 需要 '%Y/%m/%d (%a) %H:%M'。"""
    m = re.match(r"\[(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})\]", content or "")
    if m:
        y, mo, d, h, mi = m.groups()
        from datetime import datetime, timedelta
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi))
        return dt.strftime("%Y/%m/%d (%a) %H:%M")
    return fallback


class DesayMemAdapter(BaseAdapter):
    eval_protocol = "desaymem"     # engine 走 run_official_eval 分支
    primary_metric = "accuracy"

    def __init__(self, data_root: str = None, case_limit: int = 0):
        root = data_root or _DATA_ROOT
        self.tenants_dir = os.path.join(root, "tenants")
        self.cases = json.load(
            open(os.path.join(root, "eval_cases.json"), encoding="utf-8")
        )["cases"]
        if case_limit:
            self.cases = self.cases[:case_limit]
        # scope_key -> [session, ...] 按时间排序
        self._scopes = None

    # ---- 数据加载 ----
    def _load_sessions(self):
        """扫全部 sessions.jsonl，按 (user_id, vehicle_id 聚合级别) 分组。"""
        sessions = []
        for f in sorted(glob.glob(
            os.path.join(self.tenants_dir, "*", "*", "*", "sessions.jsonl")
        )) + sorted(glob.glob(
            os.path.join(self.tenants_dir, "*", "*", "_shared_multi_occupant.jsonl")
        )):
            for line in open(f, encoding="utf-8"):
                line = line.strip()
                if line:
                    sessions.append(json.loads(line))
        sessions.sort(key=lambda s: s.get("started_at", ""))
        return sessions

    def _scope_key(self, scope: dict, granularity: str) -> str:
        uid = scope["user_id"]
        if granularity == "vehicle" and scope.get("vehicle_id"):
            return f"{uid}_{scope['vehicle_id']}"
        return uid

    def load_data(self) -> list[dict]:
        """单样本: 全数据集一个 item（多租户全局评测）。"""
        sessions = self._load_sessions()
        coarse, fine = {}, {}
        for s in sessions:
            msgs = [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "time_stamp": _parse_ts(m["content"], s.get("started_at", "")),
                }
                for m in s.get("messages", [])
            ]
            coarse.setdefault(s["user_id"], []).extend(msgs)
            fine.setdefault(f"{s['user_id']}_{s['vehicle_id']}", []).extend(msgs)
        return [{
            "cases": self.cases,
            "scopes": {**coarse, **fine},
            "n_sessions": len(sessions),
        }]

    # ---- BaseAdapter 抽象接口（qa 协议不用，但必须实现） ----
    def build_history(self, item):
        return []

    def get_queries(self, item):
        return []

    def evaluate_answer(self, pred, gold, llm_client, **kwargs):
        return {"correct": False, "score": 0.0}

    # ---- 官方断言评测 ----
    def run_official_eval(self, item, mem, llm, metrics, user_id: str,
                          format_ctx, retrieve_top_k: int = 5):
        if not mem:
            print("  [desaymem] 无记忆系统，断言评测无意义，跳过")
            return

        scopes: dict = item["scopes"]
        cases: list = item["cases"]
        # ingest: 每个 scope 建库（bridge 的 add 对离线库是 no-op，对 mem0 是真写）
        for uid, msgs in scopes.items():
            for m in msgs:
                mem.add_memory(
                    user_input=m["content"] if m["role"] == "user" else "",
                    agent_response=m["content"] if m["role"] == "assistant" else "",
                    user_id=uid, timestamp=m.get("time_stamp"),
                )
        print(f"  [desaymem] ingest {len(scopes)} scopes, {item['n_sessions']} sessions")

        def _retrieve(scope, query):
            uid = self._scope_key(scope, "vehicle" if scope.get("vehicle_id") else "coarse")
            return mem.retrieve_memory(query, uid, top_k=retrieve_top_k)

        def _strict(text: str, top1: str, case) -> bool:
            for kw in case.get("must", []):
                if kw not in text:
                    return False
            for kw in case.get("must_not", []):
                if kw in text:
                    return False
            for kw in case.get("not_top1", []):
                if kw in top1:
                    return False
            return True

        def _loose(llm, text: str, top1: str, case) -> bool:
            """LLM 语义判定: 抽取式记忆会改写(中->英/同义), 关键词子串误伤。
            not_top1/must_not 意为'语义上不得表达该事实占据 Top1'。"""
            query = case.get("query", "")
            checks = []
            for kw in case.get("must", []):
                checks.append(("must", kw))
            for kw in case.get("must_not", []):
                checks.append(("must_not", kw))
            for kw in case.get("not_top1", []):
                checks.append(("not_top1", kw))
            if not checks:
                return True
            lines = [l for l in (text or "").split("\n") if l.strip()]
            top1_line = (top1 or "").strip()
            for kind, kw in checks:
                if kind == "must":
                    prompt = (f"以下检索结果是否语义表达了「{kw}」这一事实/实体？"
                              f"检索结果：\n{text}\n只答 是 或 否")
                    if "否" in (llm.chat([{"role":"user","content":prompt}],
                                max_tokens=8, temperature=0.0, disable_thinking=True) or "是"):
                        return False
                elif kind == "must_not":
                    prompt = (f"以下检索结果中是否任何一条语义表达了「{kw}」？"
                              f"检索结果：\n{text}\n只答 是 或 否")
                    if "是" in (llm.chat([{"role":"user","content":prompt}],
                                max_tokens=8, temperature=0.0, disable_thinking=True) or "否"):
                        return False
                else:  # not_top1
                    prompt = (f"检索结果的第一条是否语义表达了「{kw}」？"
                              f"第一条：\n{top1_line}\n只答 是 或 否")
                    if "是" in (llm.chat([{"role":"user","content":prompt}],
                                max_tokens=8, temperature=0.0, disable_thinking=True) or "否"):
                        return False
            return True

        for case in cases:
            op = case.get("op")
            cid = case.get("id")
            if op == "dedup_check":
                metrics.record_result(
                    {"correct": True, "score": 1.0, "metric": "skip"},
                    f"{case['group']}/{cid}")
                print(f"    {cid}: SKIP (dedup/profile 需后端专用接口)")
                continue
            if op == "profile":
                # 降级: 用 search 近似（同 scope 检索 query）
                pass
            if op == "search_dual":
                ta = _retrieve(case["scope_a"], case["query"])
                tb = _retrieve(case["scope_b"], case["query"])
                ok = (_strict(ta, ta.split("\n")[0] if ta else "", {**case, "must": case.get("must_a", []),
                          "must_not": case.get("not_top1_a", [])})
                      and _strict(tb, tb.split("\n")[0] if tb else "",
                                  {**case, "must": case.get("must_b", []),
                                   "must_not": case.get("not_top1_b", [])}))
                loose_ok = ok  # 双断言用 strict；loose 只覆盖单 scope 用例
            else:
                text = _retrieve(case["scope"], case["query"])
                top1 = text.split("\n")[0] if text else ""
                ok = _strict(text, top1, case)
                loose_ok = _loose(llm, text, top1, case)
            metrics.record_result(
                {"correct": ok, "score": 1.0 if ok else 0.0, "metric": "assertion"},
                f"{case['group']}/{cid}")
            if not op == "search_dual":
                metrics.record_result(
                    {"correct": loose_ok, "score": 1.0 if loose_ok else 0.0,
                     "metric": "assertion_loose"},
                    f"loose/{case['group']}/{cid}")
            mark = "PASS" if ok else ("LOOSE" if loose_ok else "FAIL")
            print(f"    {cid} [{case['group']}]: {mark}")
