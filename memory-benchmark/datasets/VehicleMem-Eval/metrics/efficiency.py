"""
效率指标 — token / LLM calls / runtime / memory peak
"""

import time
import tracemalloc
from dataclasses import dataclass, field


@dataclass
class EvalMetrics:
    """完整评测指标"""
    # 准确率 / 官方分数
    accuracy: float = 0.0
    mean_score: float = 0.0
    correct: int = 0
    total: int = 0
    score_sum: float = 0.0
    primary_metric: str = "accuracy"
    by_category: dict = field(default_factory=dict)   # {category: {acc, correct, total, score_sum}}
    extras_sum: dict = field(default_factory=dict)
    extras_count: dict = field(default_factory=dict)

    # 效率
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_calls: int = 0
    total_runtime: float = 0.0
    peak_memory_mb: float = 0.0
    by_phase: dict = field(default_factory=dict)      # {phase: PhaseStats summary}

    def merge_phase_stats(self, phase_stats: dict):
        for phase, ps in phase_stats.items():
            if phase not in self.by_phase:
                self.by_phase[phase] = {
                    "prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0, "calls": 0, "runtime": 0.0,
                }
            s = self.by_phase[phase]
            s["prompt_tokens"] += ps.prompt_tokens
            s["completion_tokens"] += ps.completion_tokens
            s["total_tokens"] += ps.total_tokens
            s["calls"] += ps.calls
            s["runtime"] += ps.runtime

            self.total_prompt_tokens += ps.prompt_tokens
            self.total_completion_tokens += ps.completion_tokens
            self.total_tokens += ps.total_tokens
            self.total_calls += ps.calls
            self.total_runtime += ps.runtime

    def record_peak_memory(self):
        _, peak = tracemalloc.get_traced_memory()
        self.peak_memory_mb = peak / 1024 / 1024

    def record_accuracy(self, correct: bool, category: str = "default"):
        self.record_result({"correct": bool(correct), "score": 1.0 if correct else 0.0}, category)

    def record_result(self, result, category: str = "default"):
        """Accept bool or {correct, score, ...numeric extras}."""
        if isinstance(result, bool):
            result = {"correct": result, "score": 1.0 if result else 0.0}
        elif not isinstance(result, dict):
            result = {"correct": bool(result), "score": 1.0 if result else 0.0}

        score = float(result.get("score", 1.0 if result.get("correct") else 0.0))
        correct = bool(result.get("correct", score >= 0.999))

        self.total += 1
        self.score_sum += score
        if correct:
            self.correct += 1
        self.accuracy = self.correct / self.total if self.total else 0.0
        self.mean_score = self.score_sum / self.total if self.total else 0.0

        if category not in self.by_category:
            self.by_category[category] = {"correct": 0, "total": 0, "score_sum": 0.0}
        bucket = self.by_category[category]
        bucket["total"] += 1
        bucket["score_sum"] += score
        if correct:
            bucket["correct"] += 1

        skip = {"correct", "score", "metric", "tp", "fp", "fn"}
        for key, val in result.items():
            if key in skip or isinstance(val, bool):
                if isinstance(val, bool) and key not in skip:
                    self.extras_sum[key] = self.extras_sum.get(key, 0.0) + (1.0 if val else 0.0)
                    self.extras_count[key] = self.extras_count.get(key, 0) + 1
                continue
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                self.extras_sum[key] = self.extras_sum.get(key, 0.0) + float(val)
                self.extras_count[key] = self.extras_count.get(key, 0) + 1

    def get_category_acc(self, category: str) -> float:
        c = self.by_category.get(category)
        return c["correct"] / c["total"] if c and c["total"] > 0 else 0.0

    def get_category_score(self, category: str) -> float:
        c = self.by_category.get(category)
        return c["score_sum"] / c["total"] if c and c["total"] > 0 else 0.0

    def extra_mean(self, key: str) -> float:
        n = self.extras_count.get(key, 0)
        return self.extras_sum.get(key, 0.0) / n if n else 0.0

    def macro_accuracy(self) -> float:
        if not self.by_category:
            return 0.0
        accs = [self.get_category_acc(k) for k in self.by_category]
        return sum(accs) / len(accs)

    def to_dict(self) -> dict:
        extras = {
            k: round(self.extra_mean(k), 4)
            for k in self.extras_sum
        }
        return {
            "primary_metric": self.primary_metric,
            "accuracy": round(self.accuracy, 4),
            "mean_score": round(self.mean_score, 4),
            "macro_accuracy": round(self.macro_accuracy(), 4),
            "correct": self.correct,
            "total": self.total,
            "by_category": {
                k: {
                    "accuracy": round(v["correct"] / v["total"], 4) if v["total"] > 0 else 0,
                    "mean_score": round(v["score_sum"] / v["total"], 4) if v["total"] > 0 else 0,
                    "correct": v["correct"],
                    "total": v["total"],
                }
                for k, v in self.by_category.items()
            },
            "extras": extras,
            "efficiency": {
                "total_prompt_tokens": self.total_prompt_tokens,
                "total_completion_tokens": self.total_completion_tokens,
                "total_tokens": self.total_tokens,
                "total_calls": self.total_calls,
                "total_runtime_s": round(self.total_runtime, 2),
                "peak_memory_mb": round(self.peak_memory_mb, 2),
                "by_phase": {
                    k: {
                        "prompt_tokens": v["prompt_tokens"],
                        "completion_tokens": v["completion_tokens"],
                        "total_tokens": v["total_tokens"],
                        "calls": v["calls"],
                        "runtime_s": round(v["runtime"], 2),
                    }
                    for k, v in self.by_phase.items()
                },
            },
        }
