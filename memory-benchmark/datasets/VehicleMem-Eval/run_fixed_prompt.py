"""Run the fixed Mem0 bridge with the vendored memory-benchmarks prompt.

This entrypoint changes behavior only inside its own Python process.  The
original ``run.py``, ``run_fixed.py``, adapters and evaluation engine remain
unchanged.
"""

import run_fixed as _base_runner

from adapters.locomo_prompts import get_answer_generation_prompt
from evalcore import eval_engine as _eval_engine


_original_qa_prompt = _eval_engine._qa_prompt
_original_chat = _eval_engine.CountingLLMClient.chat


def _qa_prompt_with_memory_benchmarks(
    dataset,
    retrieved_text,
    query,
    history=None,
    use_memory=True,
    extra=None,
):
    if dataset == "locomo":
        extra = extra or {}
        return get_answer_generation_prompt(
            question=query,
            memories=retrieved_text,
            reference_date=extra.get("reference_date"),
        )
    return _original_qa_prompt(
        dataset,
        retrieved_text,
        query,
        history=history,
        use_memory=use_memory,
        extra=extra,
    )


def _chat_with_final_answer(
    self,
    messages,
    max_tokens=2000,
    temperature=0.0,
    use_judge=False,
    disable_thinking=False,
    **kwargs,
):
    content = messages[-1].get("content", "") if messages else ""
    is_locomo_answer = "Follow these reasoning steps IN ORDER." in content
    if is_locomo_answer:
        max_tokens = max(max_tokens, 2000)

    response = _original_chat(
        self,
        messages,
        max_tokens=max_tokens,
        temperature=temperature,
        use_judge=use_judge,
        disable_thinking=disable_thinking,
        **kwargs,
    )
    if is_locomo_answer and "ANSWER:" in response:
        return response.rsplit("ANSWER:", 1)[-1].strip()
    return response


# Register only the process-local variants before delegating to run_fixed.main().
_eval_engine.ADAPTERS["locomo"] = (
    "adapters.locomo_prompt_adapter.LocomoPromptAdapter"
)
_eval_engine._qa_prompt = _qa_prompt_with_memory_benchmarks
_eval_engine.CountingLLMClient.chat = _chat_with_final_answer


if __name__ == "__main__":
    _base_runner.main()
