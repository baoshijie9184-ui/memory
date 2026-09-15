"""
LongMemEval 适配器 — 长对话记忆评测

数据集结构 (longmemeval_single.json / HuggingFace xiaowu0162/longmemeval 的 500 题全集):
  [{question_id, question_type, question, question_date, answer,
    answer_session_ids, haystack_dates, haystack_session_ids, haystack_sessions}]

官方主指标: GPT-style yes/no judge (evaluate_qa.py), 分 question_type 用不同 prompt.
报告 overall (micro) 以及 per-type; 论文也报告 6 类 task-averaged (macro).
"""

import json
import re
from datetime import datetime
from adapters.base import BaseAdapter
from metrics.official import longmemeval_judge_prompt, longmemeval_label_yes


class LongMemEvalAdapter(BaseAdapter):
    eval_protocol = "qa"
    primary_metric = "accuracy"

    def __init__(self, data_file: str, qa_limit: int = 0):
        self.data_file = data_file
        self.qa_limit = qa_limit

    def load_data(self) -> list[dict]:
        with open(self.data_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            data = [data]
        if self.qa_limit and self.qa_limit > 0:
            data = data[: self.qa_limit]
        return data

    def build_history(self, item: dict) -> list[dict]:
        sessions = item.get("haystack_sessions", [])
        dates = item.get("haystack_dates") or []
        messages = []
        for si, session in enumerate(sessions):
            if not isinstance(session, list):
                continue
            ts = _parse_haystack_date(dates[si] if si < len(dates) else "")
            for turn in session:
                role = turn.get("role", "user")
                content = turn.get("content", "")
                msg = {"role": role, "content": content}
                if ts:
                    msg["timestamp"] = ts
                messages.append(msg)
        return messages

    def get_queries(self, item: dict) -> list[dict]:
        qid = item.get("question_id", "")
        return [{
            "query": item.get("question", ""),
            "answer": item.get("answer", ""),
            "category": item.get("question_type", "unknown"),
            "question_id": qid,
            "question_date": item.get("question_date", ""),
            "question_type": item.get("question_type", "unknown"),
            "abstention": "_abs" in str(qid),
        }]

    def evaluate_answer(self, pred: str, gold: str, llm_client, **kwargs):
        query = kwargs.get("query") or {}
        question = kwargs.get("question") or query.get("query", "")
        qtype = query.get("question_type") or query.get("category") or "multi-session"
        abstention = bool(query.get("abstention")) or "_abs" in str(query.get("question_id", ""))
        prompt = longmemeval_judge_prompt(qtype, question, gold, pred, abstention=abstention)
        # judge 也关思考: Qwen3 思考型 + max_tokens=10 会把预算耗在思考上,
        # content 返回空串 -> label_yes 恒 False (与 LoCoMo 作答空串同根因)
        result = llm_client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=50, temperature=0.0, use_judge=True,
            disable_thinking=True,
        )
        ok = longmemeval_label_yes(result)
        return {"correct": ok, "score": 1.0 if ok else 0.0, "metric": "accuracy"}


def _parse_haystack_date(raw: str) -> str:
    m = re.search(r"(\d{4}/\d{2}/\d{2}).*?(\d{2}:\d{2})", raw or "")
    if not m:
        return ""
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y/%m/%d %H:%M").strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return ""
