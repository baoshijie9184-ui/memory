"""
VehicleMemBench 适配器 — 车载多用户长期记忆 + 车控工具调用

数据集结构:
  history/history_N.txt: 多用户历史聊天 [timestamp] Name: text
  qa_data/qa_N.json: {related_to_vehicle_preference: [{gold_memory, reasoning_type, query, new_answer}]}

官方主指标 (VehicleMemBench/evaluation):
  在 VehicleWorld 里执行预测工具, 比较最终环境状态 exact match + field/value P/R/F1.
本适配器在不拉起仿真器时, 用官方 score_tool_calls:
  key = (name, json.dumps(args, sort_keys=True)) → P/R/F1
  exact_match 代理: 预测工具集合 == gold new_answer 集合 (F1==1).
按 reasoning_type 分组报告.
"""

import os
import re
import json
import glob
from adapters.base import BaseAdapter
from metrics.official import parse_answer_to_tools, parse_pred_tools, score_tool_calls, tool_exact_match


class VehicleMemBenchAdapter(BaseAdapter):
    eval_protocol = "qa"
    primary_metric = "tool_f1"

    def __init__(self, history_dir: str, qa_dir: str, file_range: str = None):
        self.history_dir = history_dir
        self.qa_dir = qa_dir
        self.file_range = self._parse_range(file_range)

    def _parse_range(self, r: str) -> list[int]:
        if not r:
            return []
        selected = set()
        for part in r.split(","):
            t = part.strip()
            if "-" in t:
                a, b = t.split("-", 1)
                selected.update(range(int(a), int(b) + 1))
            elif t:
                selected.add(int(t))
        return sorted(selected)

    def load_data(self) -> list[dict]:
        """加载所有 qa 文件, 每个文件一个样本"""
        qa_files = sorted(glob.glob(os.path.join(self.qa_dir, "qa_*.json")))
        samples = []
        for f in qa_files:
            m = re.search(r"qa_(\d+)\.json$", f)
            if not m:
                continue
            num = int(m.group(1))
            if self.file_range and num not in self.file_range:
                continue
            with open(f, "r", encoding="utf-8") as h:
                qa_data = json.load(h)
            history_file = os.path.join(self.history_dir, f"history_{num}.txt")
            history_text = ""
            if os.path.exists(history_file):
                with open(history_file, "r", encoding="utf-8") as h:
                    history_text = h.read()
            samples.append({
                "file_num": num,
                "history": history_text,
                "history_path": history_file if os.path.exists(history_file) else "",
                "qa_data": qa_data,
            })
        return samples

    def build_history(self, item: dict) -> list[dict]:
        """history.txt 是多用户聊天, 逐行转消息"""
        lines = item.get("history", "").strip().split("\n")
        messages = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = re.match(r"\[(.*?)\]\s*(.+?):\s*(.*)", line)
            if m:
                ts, name, text = m.groups()
                msg = {"role": "user", "content": f"[{name}] {text}"}
                if ts:
                    stamp = ts.strip()
                    if len(stamp) == 16:
                        stamp += ":00"
                    msg["timestamp"] = stamp
                messages.append(msg)
        return messages

    def get_queries(self, item: dict) -> list[dict]:
        events = item.get("qa_data", {}).get("related_to_vehicle_preference", [])
        out = []
        for e in events:
            if not e.get("query"):
                continue
            gold_list = e.get("new_answer") or []
            if not isinstance(gold_list, list):
                gold_list = [gold_list]
            out.append({
                "query": e.get("query", ""),
                "answer": "; ".join(str(x) for x in gold_list),
                "category": e.get("reasoning_type", "unknown"),
                "gold_memory": e.get("gold_memory", ""),
                "tool_golds": gold_list,
            })
        return out

    def evaluate_answer(self, pred: str, gold: str, llm_client, **kwargs):
        query = kwargs.get("query") or {}
        gold_list = query.get("tool_golds")
        if not gold_list:
            gold_list = [p.strip() for p in str(gold or "").split(";") if p.strip()]
        gold_calls = parse_answer_to_tools(gold_list)
        pred_calls = parse_pred_tools(pred)
        scored = score_tool_calls(pred_calls, gold_calls)
        exact = tool_exact_match(scored)
        return {
            "correct": exact,
            "score": scored["f1"],
            "metric": "tool_f1",
            "precision": scored["precision"],
            "recall": scored["recall"],
            "exact_match": exact,
        }
