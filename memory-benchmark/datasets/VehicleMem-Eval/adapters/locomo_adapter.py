"""
Locomo 适配器 — LoCoMo 长对话记忆评测

数据集结构 (locomo10.json):
  [{sample_id, conversation: {speaker_a, speaker_b, session_1, ...}, qa: [{question, answer, evidence, category}]}]

JSON category 字段 (与论文列举顺序不同, 以官方 evaluation.py 为准):
  1 = multi-hop   — 逗号切分子答案, mean(max F1)
  2 = temporal    — token F1
  3 = open-domain — gold 取 ';' 前第一段, token F1
  4 = single-hop  — token F1
  5 = adversarial — 预测含 "no information available" / "not mentioned" 得 1, 否则 0

官方主指标: Token-level F1 (Porter stem + normalize_answer), 不是 LLM-as-judge.
"""

import json
from datetime import datetime
from adapters.base import BaseAdapter
from metrics.official import locomo_qa_score

CATEGORY_MAP = {
    1: "multi_hop",
    2: "temporal",
    3: "open",
    4: "single_hop",
    5: "adversarial",
}


def _parse_session_time(raw: str) -> str:
    text = (raw or "").strip()
    for fmt in ("%I:%M %p on %d %B, %Y", "%I:%M %p on %d %b, %Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return ""


class LocomoAdapter(BaseAdapter):
    eval_protocol = "qa"
    primary_metric = "f1"

    def __init__(self, data_file: str, qa_limit: int = 0):
        self.data_file = data_file
        self.qa_limit = qa_limit

    def load_data(self) -> list[dict]:
        with open(self.data_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def build_history(self, item: dict) -> list[dict]:
        """把 locomo 对话转为消息列表"""
        conv = item.get("conversation", {})
        speaker_a = conv.get("speaker_a", "A")
        speaker_b = conv.get("speaker_b", "B")
        messages = []
        for i in range(1, 36):
            session_key = f"session_{i}"
            if session_key not in conv:
                break
            turns = conv[session_key]
            if not isinstance(turns, list):
                continue
            ts = _parse_session_time(conv.get(f"{session_key}_date_time", ""))
            for turn in turns:
                speaker = turn.get("speaker", "")
                text = turn.get("text", "")
                role = "user" if speaker == speaker_a else "assistant"
                msg = {"role": role, "content": f"[{speaker}] {text}"}
                if ts:
                    msg["timestamp"] = ts
                messages.append(msg)
        return messages

    def get_queries(self, item: dict) -> list[dict]:
        qas = item.get("qa", [])
        if self.qa_limit and self.qa_limit > 0:
            qas = qas[: self.qa_limit]
        conv = item.get("conversation", {})
        speakers = [conv.get("speaker_a", "A"), conv.get("speaker_b", "B")]
        out = []
        for qa in qas:
            cat = int(qa.get("category", 4) or 4)
            out.append({
                "query": qa["question"],
                "answer": qa.get("answer", ""),
                "category": CATEGORY_MAP.get(cat, "unknown"),
                "category_id": cat,
                "evidence": qa.get("evidence", []),
                "speakers": speakers,   # 官方 prompt 需要对话双方名字
            })
        return out

    def evaluate_answer(self, pred: str, gold: str, llm_client, **kwargs):
        query = kwargs.get("query") or {}
        cat = int(query.get("category_id") or kwargs.get("category_id") or 4)
        f1 = locomo_qa_score(pred, gold, cat)
        if cat == 5:
            correct = f1 >= 1.0
        else:
            correct = f1 >= 0.5
        return {"correct": correct, "score": f1, "metric": "f1"}
