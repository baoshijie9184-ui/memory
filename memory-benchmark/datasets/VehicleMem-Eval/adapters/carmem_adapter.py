"""
CarMem 适配器 — 车载语音助手偏好提取 / 检索 / 维护

官方协议 (COLING 2025 / arxiv 2501.09645):
  Extraction  : In-Schema — 预测的 Main/Sub/Detail 与 gold 一致 (论文 Table 3 F1)
  Retrieval   : 用 next_conversation_question 检索, gold 偏好是否进入 top-n (论文 Table 6)
  Maintenance : equal→Pass; negate→Update; different→Append(MP)/Update(SP)  (论文 Table 4)

different_attribute 不是问题, 是 different 题的新属性值, 不作为 query.
"""

import json
from adapters.base import BaseAdapter
from metrics.official import (
    carmem_extraction_score,
    carmem_retrieval_hit,
    carmem_maintenance_label,
    parse_maintenance_action,
    parse_pref_fields,
)

EXTRACTION_PROMPT = """Extract the user preference as exactly 4 fields separated by semicolons:
Main Category; Subcategory; Detail Category; Attribute
Only output that one line. If no in-schema preference is present, output NONE.

Context:
{context}
"""

MAINT_ACTION_PROMPT = """You maintain a structured in-car preference memory.
Existing stored preference: {existing}
Detail category type: {pref_type} (MP = multiple values allowed, SP = only one value allowed)
New user utterance: {utterance}

Choose exactly one maintenance function:
- Pass: the incoming preference already exists; do not insert again
- Update: replace the existing preference with the new one (use for negation or SP overwrite)
- Append: add a new additional preference (only valid for MP when it is a different extra value)

Reply with one word: Pass, Update, or Append.
"""


def _conv_to_messages(conv: list) -> list[dict]:
    messages = []
    for turn in conv or []:
        if not isinstance(turn, dict):
            continue
        if "USER" in turn:
            messages.append({"role": "user", "content": turn["USER"]})
        elif "ASSISTANT" in turn:
            messages.append({"role": "assistant", "content": turn["ASSISTANT"]})
    return messages


def _messages_text(messages: list) -> str:
    return "\n".join(f"{m.get('role', '')}: {m.get('content', '')}" for m in messages)


def _write_messages(mem, messages: list[dict], uid: str):
    if not mem:
        return
    i = 0
    while i < len(messages):
        cur = messages[i]
        nxt = messages[i + 1] if i + 1 < len(messages) else None
        ts = cur.get("timestamp") or (nxt.get("timestamp") if nxt else None)
        if cur.get("role") == "user" and nxt and nxt.get("role") == "assistant":
            mem.add_memory(cur.get("content", ""), nxt.get("content", ""), uid, timestamp=ts)
            i += 2
        elif cur.get("role") == "assistant":
            mem.add_memory("", cur.get("content", ""), uid, timestamp=ts)
            i += 1
        else:
            mem.add_memory(cur.get("content", ""), "", uid, timestamp=ts)
            i += 1


def _safe_delete(mem, uid: str):
    if mem and hasattr(mem, "delete_memoryos_user"):
        try:
            mem.delete_memoryos_user(uid)
        except Exception:
            pass


def _lookup_pref_type(item: dict, gold: str) -> str:
    g_fields = [_norm(x) for x in parse_pref_fields(gold)]
    for p in item.get("user_preferences") or []:
        if not isinstance(p, dict):
            continue
        cand = [
            p.get("Main Category", ""),
            p.get("Subcategory", ""),
            p.get("Detail Category", ""),
            p.get("Attributes", ""),
        ]
        if [_norm(x) for x in cand] == g_fields:
            return (p.get("Preference Type") or "SP").upper()
    return "SP"


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


class CarMemAdapter(BaseAdapter):
    eval_protocol = "carmem"
    primary_metric = "carmem"

    def __init__(self, data_file: str, user_limit: int = 0, pref_limit: int = 0):
        self.data_file = data_file
        self.user_limit = user_limit
        self.pref_limit = pref_limit

    def load_data(self) -> list[dict]:
        items = []
        with open(self.data_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    items.append(json.loads(line))
        if self.user_limit and self.user_limit > 0:
            items = items[: self.user_limit]
        return items

    def build_history(self, item: dict) -> list[dict]:
        messages = []
        for d in item.get("data", []):
            messages.extend(_conv_to_messages(d.get("extraction_conversation", [])))
        return messages

    def get_queries(self, item: dict) -> list[dict]:
        """Fallback QA list; official path uses run_official_eval."""
        queries = []
        prefs = item.get("data", [])
        if self.pref_limit and self.pref_limit > 0:
            prefs = prefs[: self.pref_limit]
        for d in prefs:
            pref = d.get("user_preference", "")
            queries.append({
                "query": "Extract the user preference as Main; Sub; Detail; Attribute",
                "answer": pref,
                "category": "extraction",
            })
            retr_q = d.get("next_conversation_question", "")
            if retr_q:
                queries.append({
                    "query": retr_q,
                    "answer": pref,
                    "category": "retrieval",
                })
            maint = d.get("maintenance_questions") or {}
            if isinstance(maint, dict):
                for key in ("question_equal_preference", "question_negate_preference",
                            "question_different_preference"):
                    qtext = maint.get(key)
                    if qtext:
                        queries.append({
                            "query": qtext,
                            "answer": pref,
                            "category": f"maintenance_{key.replace('question_', '')}",
                        })
        return queries

    def evaluate_answer(self, pred: str, gold: str, llm_client, **kwargs):
        query = kwargs.get("query") or {}
        cat = str(query.get("category") or kwargs.get("category") or "")
        if cat == "extraction":
            return carmem_extraction_score(pred, gold)
        if cat == "retrieval":
            hit = carmem_retrieval_hit(pred, gold)
            return {"correct": hit, "score": 1.0 if hit else 0.0, "metric": "retrieval_hit"}
        hit = carmem_retrieval_hit(pred, gold)
        return {"correct": hit, "score": 1.0 if hit else 0.0}

    def run_official_eval(self, item, mem, llm, metrics, user_id: str, format_ctx, retrieve_top_k: int = 5):
        """Per-preference: extract → retrieve → three isolated maintenance cases."""
        prefs = item.get("data") or []
        if self.pref_limit and self.pref_limit > 0:
            prefs = prefs[: self.pref_limit]

        for pi, d in enumerate(prefs):
            gold = d.get("user_preference", "")
            conv = d.get("extraction_conversation", [])
            conv_msgs = _conv_to_messages(conv)
            pref_type = _lookup_pref_type(item, gold)
            base_uid = f"{user_id}_p{pi}"

            _write_messages(mem, conv_msgs, base_uid)

            # --- Extraction (In-Schema) ---
            extract_ctx = self._context(mem, format_ctx, base_uid, "What preferences does this user have?", conv_msgs)
            pred_line = llm.chat(
                [{"role": "user", "content": EXTRACTION_PROMPT.format(context=extract_ctx)}],
                max_tokens=80, temperature=0.0, disable_thinking=True,
            )
            metrics.record_result(carmem_extraction_score(pred_line, gold), "extraction")

            # --- Retrieval (hit@k proxy) ---
            retr_q = d.get("next_conversation_question", "") or "What is this user's relevant preference?"
            retr_ctx = self._context(mem, format_ctx, base_uid, retr_q, conv_msgs)
            hit = carmem_retrieval_hit(retr_ctx, gold)
            metrics.record_result(
                {"correct": hit, "score": 1.0 if hit else 0.0, "metric": "retrieval_hit"},
                "retrieval",
            )

            # --- Maintenance: action (official) + state after writing utterance ---
            maint = d.get("maintenance_questions") or {}
            if not isinstance(maint, dict):
                maint = {}
            cases = [
                ("equal", maint.get("question_equal_preference"), gold),
                ("negate", maint.get("question_negate_preference"), gold),
                ("different", maint.get("question_different_preference"), maint.get("different_attribute", "")),
            ]
            for kind, utterance, _target in cases:
                if not utterance:
                    continue
                gold_action = carmem_maintenance_label(kind, pref_type)
                action_pred = llm.chat(
                    [{"role": "user", "content": MAINT_ACTION_PROMPT.format(
                        existing=gold, pref_type=pref_type, utterance=utterance,
                    )}],
                    max_tokens=10, temperature=0.0, disable_thinking=True,
                )
                pred_action = parse_maintenance_action(action_pred)
                action_ok = pred_action == gold_action

                m_uid = f"{base_uid}_{kind}"
                _write_messages(mem, conv_msgs, m_uid)
                _write_messages(mem, [{"role": "user", "content": utterance}], m_uid)
                state_ctx = self._context(
                    mem, format_ctx, m_uid,
                    "What is the user's current preference on this topic?",
                    conv_msgs + [{"role": "user", "content": utterance}],
                )
                orig_attr = parse_pref_fields(gold)[3]
                new_attr = str(maint.get("different_attribute") or "")
                state_pred = llm.chat(
                    [{"role": "user", "content": (
                        "From the memory/conversation below, report the user's CURRENT preference "
                        "attribute for this topic. If they dropped it, reply NONE. "
                        "If they switched, reply the new value. One short phrase only.\n\n"
                        f"{state_ctx}"
                    )}],
                    max_tokens=30, temperature=0.0, disable_thinking=True,
                )
                blob = (state_pred or "").lower()
                if kind == "equal":
                    state_ok = bool(orig_attr) and _norm(orig_attr) in blob
                elif kind == "negate":
                    state_ok = (not orig_attr) or (_norm(orig_attr) not in blob) or ("none" in blob)
                else:
                    state_ok = bool(new_attr) and _norm(new_attr) in blob
                    if pref_type == "SP" and orig_attr and _norm(orig_attr) != _norm(new_attr):
                        state_ok = state_ok and _norm(orig_attr) not in blob

                metrics.record_result(
                    {
                        "correct": action_ok,
                        "score": 1.0 if action_ok else 0.0,
                        "state_ok": bool(state_ok),
                        "metric": "maintenance_action",
                    },
                    f"maintenance_{kind}",
                )
                _safe_delete(mem, m_uid)

            _safe_delete(mem, base_uid)

    def _context(self, mem, format_ctx, uid: str, query: str, fallback_msgs: list) -> str:
        if mem:
            result = mem.retrieve_memory(query, uid, top_k=5)
            text = format_ctx(result) if format_ctx else str(result)
            return text or _messages_text(fallback_msgs)
        return _messages_text(fallback_msgs)
