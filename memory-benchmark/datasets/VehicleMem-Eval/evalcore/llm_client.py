"""
统一 LLM 客户端 — 带 token/call 计数

支持 OpenAI-compatible API (DeepSeek/GPT/Qwen 等)
自动统计 prompt_tokens / completion_tokens / 调用次数
"""

import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Optional
from openai import OpenAI


@dataclass
class CallStats:
    """单次调用统计"""
    phase: str = ""          # memory_build / retrieval / qa / judge
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration: float = 0.0    # 秒


@dataclass
class PhaseStats:
    """阶段累计统计"""
    phase: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    runtime: float = 0.0     # 秒
    peak_memory_mb: float = 0.0

    def add(self, s: CallStats):
        self.prompt_tokens += s.prompt_tokens
        self.completion_tokens += s.completion_tokens
        self.total_tokens += s.total_tokens
        self.calls += 1
        self.runtime += s.duration


class CountingLLMClient:
    """带计数的 LLM 客户端 — 自动统计 token 和调用次数"""

    def __init__(self, api_base: str, api_key: str, model: str,
                 judge_api_base: str = None, judge_api_key: str = None,
                 judge_model: str = None):
        self.model = model
        self.client = OpenAI(base_url=api_base, api_key=api_key)

        # judge 模型(可独立配置,默认用主模型)
        jm = judge_model or model
        jb = judge_api_base or api_base
        jk = judge_api_key or api_key
        self.judge_model = jm
        self.judge_client = OpenAI(base_url=jb, api_key=jk)

        # 统计
        self._phases: dict[str, PhaseStats] = {}
        self._cur_phase = "default"

    def set_phase(self, phase: str):
        """设置当前阶段(memory_build / retrieval / qa / judge)"""
        self._cur_phase = phase
        if phase not in self._phases:
            self._phases[phase] = PhaseStats(phase=phase)

    def chat(self, messages: list[dict], max_tokens: int = 2000,
             temperature: float = 0.0, use_judge: bool = False,
             disable_thinking: bool = False,
             **kwargs) -> str:
        """调用 LLM — 自动统计 token 和耗时
        disable_thinking: Qwen3 思考型请求级关思考（extra_body.chat_template_kwargs），
        否则思考占用 max_tokens（如作答 200）导致 content 为空。"""
        client = self.judge_client if use_judge else self.client
        model = self.judge_model if use_judge else self.model

        if disable_thinking:
            # 与 LightMem search 同款开关; judge 刻意不关（思考利于判分）
            kwargs.setdefault("extra_body", {})["chat_template_kwargs"] = {
                "enable_thinking": False
            }

        t0 = time.time()
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )
        dt = time.time() - t0

        content = resp.choices[0].message.content or ""

        # 统计
        pt = ct = tt = 0
        if resp.usage:
            pt = resp.usage.prompt_tokens or 0
            ct = resp.usage.completion_tokens or 0
            tt = resp.usage.total_tokens or (pt + ct)

        phase = "judge" if use_judge else self._cur_phase
        if phase not in self._phases:
            self._phases[phase] = PhaseStats(phase=phase)
        self._phases[phase].add(CallStats(
            phase=phase, prompt_tokens=pt, completion_tokens=ct,
            total_tokens=tt, duration=dt,
        ))

        return content

    def embed(self, text: str, embed_base: str, embed_key: str,
              embed_model: str) -> list[float]:
        """调用 embedding — 统计到当前阶段"""
        ec = OpenAI(base_url=embed_base, api_key=embed_key)
        t0 = time.time()
        resp = ec.embeddings.create(model=embed_model, input=text)
        dt = time.time() - t0

        pt = 0
        if resp.usage:
            pt = resp.usage.prompt_tokens or 1
        # embedding 调用也算一次
        phase = self._cur_phase
        if phase not in self._phases:
            self._phases[phase] = PhaseStats(phase=phase)
        self._phases[phase].add(CallStats(
            phase=phase, prompt_tokens=pt, total_tokens=pt, duration=dt,
        ))

        return resp.data[0].embedding

    def get_stats(self) -> dict[str, PhaseStats]:
        return self._phases

    def reset_stats(self):
        self._phases = {}
        self._cur_phase = "default"
