"""Asynchronous Mem0 + LoCoMo runner using only project-local modules."""

import argparse
import asyncio
import faulthandler
import os
import sys
import time
import tracemalloc

print("[startup 1/5] importing evaluation dependencies...", flush=True)
import yaml
from openai import AsyncOpenAI

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adapters.locomo_prompt_adapter import LocomoPromptAdapter
from adapters.locomo_prompts import get_answer_generation_prompt
from evalcore.llm_client import CallStats, PhaseStats
from evalcore.memory_bridge_async_fixed import build_async_mem0
from evalcore.model_registry import load_model_config
from metrics.efficiency import EvalMetrics
from metrics.reporter import generate_report


class AsyncCountingLLMClient:
    """Concurrency-safe token accounting for asynchronous answer calls."""

    def __init__(self, api_base: str, api_key: str, model: str):
        self.model = model
        self.client = AsyncOpenAI(base_url=api_base, api_key=api_key)
        self.stats = {"qa": PhaseStats(phase="qa")}

    async def answer(self, prompt: str) -> str:
        start = time.monotonic()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
            temperature=0.0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        duration = time.monotonic() - start
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else prompt_tokens + completion_tokens
        self.stats["qa"].add(CallStats(
            phase="qa",
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
            total_tokens=total_tokens or 0,
            duration=duration,
        ))
        text = response.choices[0].message.content or ""
        return text.rsplit("ANSWER:", 1)[-1].strip() if "ANSWER:" in text else text.strip()

    async def close(self):
        await self.client.close()


async def run(args):
    root = os.path.dirname(os.path.abspath(__file__))
    print("[startup 2/5] loading model and dataset configuration...", flush=True)
    model_cfg = load_model_config(args.model_config)
    with open(os.path.join(root, "config", "datasets.yaml"), encoding="utf-8") as handle:
        dataset_cfg = yaml.safe_load(handle)["datasets"]["locomo"]

    qa_limit = 0 if args.full else dataset_cfg.get("qa_limit", 0)
    adapter = LocomoPromptAdapter(
        data_file=os.path.join(root, dataset_cfg["data_file"]),
        qa_limit=qa_limit,
    )
    data = adapter.load_data()
    if args.sample_limit:
        data = data[:args.sample_limit]

    print("[startup 3/5] initializing AsyncMemory and local Qdrant...", flush=True)
    # If third-party initialization blocks, print its Python stack automatically.
    faulthandler.dump_traceback_later(60, repeat=False)
    memory = build_async_mem0(model_cfg, root, args.write_concurrency)
    faulthandler.cancel_dump_traceback_later()
    print("[startup 4/5] initializing asynchronous answer client...", flush=True)
    answerer = AsyncCountingLLMClient(
        model_cfg.api_base,
        model_cfg.api_key,
        model_cfg.model,
    )
    metrics = EvalMetrics()
    metrics.primary_metric = "f1"
    metrics.raw_info = {}
    tracemalloc.start()

    print("[startup 5/5] initialization complete; starting evaluation.", flush=True)
    print(f"Samples: {len(data)} | ingest workers: {args.ingest_workers} | QA workers: {args.qa_workers}")

    ingest_semaphore = asyncio.Semaphore(max(1, args.ingest_workers))

    async def ingest_sample(index, item):
        async with ingest_semaphore:
            chunks = adapter.build_ingest_chunks(item)
            if args.build_limit:
                chunks = chunks[:args.build_limit]
            user_id = f"user_{index}"
            for chunk_index, chunk in enumerate(chunks, 1):
                await memory.add_messages(
                    chunk["messages"],
                    user_id=user_id,
                    timestamp=chunk["timestamp"],
                )
                print(
                    f"ingest sample {index + 1}/{len(data)}: "
                    f"{chunk_index}/{len(chunks)}",
                    flush=True,
                )

    await asyncio.gather(*(
        ingest_sample(index, item) for index, item in enumerate(data)
    ))

    qa_semaphore = asyncio.Semaphore(max(1, args.qa_workers))

    async def answer_question(index, query_index, query):
        async with qa_semaphore:
            user_id = f"user_{index}"
            memories = await memory.retrieve_memory(
                query["query"],
                user_id=user_id,
                top_k=args.top_k,
            )
            prompt = get_answer_generation_prompt(
                question=query["query"],
                memories=memories,
                reference_date=query.get("reference_date"),
            )
            prediction = await answerer.answer(prompt)
            result = adapter.evaluate_answer(
                prediction,
                query["answer"],
                None,
                question=query["query"],
                query=query,
            )
            metrics.record_result(result, query.get("category", "default"))
            print(
                f"QA sample {index + 1}/{len(data)} question {query_index + 1}: "
                f"F1={result['score']:.2%}",
                flush=True,
            )

    qa_jobs = []
    for index, item in enumerate(data):
        for query_index, query in enumerate(adapter.get_queries(item)):
            qa_jobs.append(answer_question(index, query_index, query))
    await asyncio.gather(*qa_jobs)

    metrics.merge_phase_stats(answerer.stats)
    behavior = memory.behavior_stats()
    if behavior:
        metrics.raw_info["memory_behavior"] = behavior
    metrics.record_peak_memory()
    tracemalloc.stop()

    await answerer.close()
    memory.close()

    output_dir = args.output_dir or os.path.join(root, "results")
    generate_report(metrics, "locomo", model_cfg.model, "mem0-async-fixed", output_dir)


def main():
    parser = argparse.ArgumentParser(description="Async Mem0 + LoCoMo Token-F1")
    parser.add_argument("--dataset", choices=["locomo"], default="locomo")
    parser.add_argument("--memory-system", choices=["mem0"], default="mem0")
    parser.add_argument("--model-config", default="memory-llm")
    parser.add_argument("--sample-limit", type=int, default=0)
    parser.add_argument("--build-limit", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--ingest-workers", type=int, default=2)
    parser.add_argument("--write-concurrency", type=int, default=1)
    parser.add_argument("--qa-workers", type=int, default=4)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
