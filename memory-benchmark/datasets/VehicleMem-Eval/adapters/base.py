"""
适配器基类 — 每个数据集实现此接口
"""

from abc import ABC, abstractmethod


def judge_is_correct(result: str) -> bool:
    """LLM judge output. '不正确' must not count as 正确."""
    text = (result or "").strip()
    low = text.lower()
    if any(tok in text for tok in ("不正确", "错误")):
        return False
    if "incorrect" in low or "wrong" in low:
        return False
    return "正确" in text or "correct" in low or low in ("yes", "y", "true")


class BaseAdapter(ABC):
    """数据集适配器基类"""

    eval_protocol: str = "qa"          # "qa" | "carmem"
    primary_metric: str = "accuracy"   # "f1" | "accuracy" | "tool_f1" | "carmem"

    @abstractmethod
    def load_data(self) -> list[dict]:
        """加载数据集, 返回样本列表"""
        ...

    @abstractmethod
    def build_history(self, item: dict) -> list[dict]:
        """构建记忆输入 — 返回 [{"role": "user", "content": "..."}] 格式"""
        ...

    @abstractmethod
    def get_queries(self, item: dict) -> list[dict]:
        """获取评测问题 — 返回 [{"query": "...", "answer": "...", "category": "..."}] 格式"""
        ...

    eval_protocol: str = "qa"          # "qa" | "carmem"
    primary_metric: str = "accuracy"   # "f1" | "accuracy" | "tool_f1" | "carmem"

    @abstractmethod
    def evaluate_answer(self, pred: str, gold: str, llm_client, **kwargs):
        """评分 — 返回 bool 或 {correct, score, ...}"""
        ...
