"""Phased asynchronous Mem0 + LoCoMo runner.

The ingest and evaluation phases can run in separate processes while sharing a
run-isolated local Qdrant store. Both phases maintain resumable checkpoints.
"""

import argparse
import asyncio
import json
import os
import sys
import time
import tracemalloc
from datetime import datetime, timezone

import yaml
from openai import AsyncOpenAI

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adapters.locomo_prompt_adapter import LocomoPromptAdapter
from adapters.locomo_prompts import get_answer_generation_prompt
from evalcore.llm_client import CallStats, PhaseStats
from evalcore.memory_bridge_async_phased import build_phased_async_mem0
from evalcore.model_registry import load_model_config
from metrics.efficiency import EvalMetrics
from metrics.reporter import generate_report


def _atomic_json_write(path: str, value: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def _load_json(path: str, default: dict) -> dict:
    if not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _group_chunks(chunks: list[dict], chunk_size: int) -> list[dict]:
    """Combine adjacent turns without crossing a session timestamp boundary."""
    grouped = []
    messages = []
    timestamp = None

    def flush():
        nonlocal messages, timestamp
        if messages:
            grouped.append({"messages": messages, "timestamp": timestamp})
        messages = []
        timestamp = None

    for chunk in chunks:
        chunk_timestamp = chunk.get("timestamp")
        if messages and (chunk_timestamp != timestamp or len(messages) >= chunk_size):
            flush()
        if not messages:
            timestamp = chunk_timestamp
        messages.extend(chunk.get("messages", []))
        if len(messages) >= chunk_size:
            flush()
    flush()
    return grouped


class AsyncCountingAnswerer:
    def __init__(self, api_base: str, api_key: str, model: str, max_tokens: int):
        self.model = model
        self.max_tokens = max_tokens
        self.client = AsyncOpenAI(base_url=api_base, api_key=api_key)
    async def answer(self, prompt: str) -> tuple[str, dict]:
        started = time.monotonic()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=self.max_tokens,
            temperature=0.0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        duration = time.monotonic() - started
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else prompt_tokens + completion_tokens
        call_stats = CallStats(
            phase="qa",
            prompt_tokens=prompt_tokens or 0,
            completion_tokens=completion_tokens or 0,
            total_tokens=total_tokens or 0,
            duration=duration,
        )
        text = response.choices[0].message.content or ""
        prediction = text.rsplit("ANSWER:", 1)[-1].strip() if "ANSWER:" in text else text.strip()
        usage = {
            "prompt_tokens": call_stats.prompt_tokens,
            "completion_tokens": call_stats.completion_tokens,
            "total_tokens": call_stats.total_tokens,
            "runtime_s": call_stats.duration,
        }
        return prediction, usage

    async def close(self):
        await self.client.close()


def _load_dataset(eval_root: str, full: bool, sample_limit: int):
    with open(os.path.join(eval_root, "config", "datasets.yaml"), encoding="utf-8") as handle:
        dataset_cfg = yaml.safe_load(handle)["datasets"]["locomo"]
    adapter = LocomoPromptAdapter(
        data_file=os.path.join(eval_root, dataset_cfg["data_file"]),
        qa_limit=0 if full else dataset_cfg.get("qa_limit", 0),
    )
    data = adapter.load_data()
    if sample_limit:
        data = data[:sample_limit]
    return adapter, data


def _new_ingest_checkpoint(args, model_name: str, sample_count: int) -> dict:
    return {
        "version": 1,
        "dataset": "locomo",
        "run_id": args.run_id,
        "model_config": args.model_config,
        "model": model_name,
        "sample_count": sample_count,
        "chunk_size": args.chunk_size,
        "build_limit": args.build_limit,
        "samples": {},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _validate_ingest_checkpoint(
    checkpoint: dict,
    args,
    model_name: str,
    sample_count: int,
    *,
    validate_layout: bool = True,
):
    expected = {
        "dataset": "locomo",
        "run_id": args.run_id,
        "model_config": args.model_config,
        "model": model_name,
        "sample_count": sample_count,
    }
    if validate_layout:
        expected.update({
            "chunk_size": args.chunk_size,
            "build_limit": args.build_limit,
        })
    mismatches = [
        f"{key}: stored={checkpoint.get(key)!r}, requested={value!r}"
        for key, value in expected.items()
        if checkpoint.get(key) != value
    ]
    if mismatches:
        raise RuntimeError(
            "Ingest checkpoint options do not match. Use the original options or "
            "choose a new run_id/--fresh:\n" + "\n".join(mismatches)
        )


async def _run_ingest(args, memory, storage_root, adapter, data, model_name: str):
    checkpoint_path = os.path.join(storage_root, "ingest_checkpoint.json")
    checkpoint = _load_json(
        checkpoint_path,
        _new_ingest_checkpoint(args, model_name, len(data)),
    )
    _validate_ingest_checkpoint(checkpoint, args, model_name, len(data))
    checkpoint_lock = asyncio.Lock()
    sample_semaphore = asyncio.Semaphore(max(1, args.ingest_workers))
    phase_started = time.monotonic()

    async def save_checkpoint():
        checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json_write(checkpoint_path, checkpoint)

    async def ingest_sample(index: int, item: dict):
        async with sample_semaphore:
            raw_chunks = adapter.build_ingest_chunks(item)
            chunks = _group_chunks(raw_chunks, args.chunk_size)
            if args.build_limit:
                chunks = chunks[:args.build_limit]

            key = str(index)
            saved = checkpoint["samples"].get(key, {})
            next_chunk = int(saved.get("next_chunk", 0))
            if saved.get("complete") and next_chunk >= len(chunks):
                print(
                    f"ingest sample {index + 1}/{len(data)} already complete "
                    f"({len(chunks)} chunks)",
                    flush=True,
                )
                return

            user_id = f"locomo_{index}_{args.run_id}"
            sample_started = time.monotonic()
            print(
                f"ingest sample {index + 1}/{len(data)} resume={next_chunk} "
                f"total={len(chunks)} user_id={user_id}",
                flush=True,
            )

            for chunk_index in range(next_chunk, len(chunks)):
                chunk = chunks[chunk_index]
                await memory.add_messages(
                    chunk["messages"],
                    user_id=user_id,
                    timestamp=chunk["timestamp"],
                )
                async with checkpoint_lock:
                    checkpoint["samples"][key] = {
                        "user_id": user_id,
                        "next_chunk": chunk_index + 1,
                        "total_chunks": len(chunks),
                        "complete": chunk_index + 1 == len(chunks),
                        "runtime_s": round(time.monotonic() - sample_started, 3),
                    }
                    await save_checkpoint()
                completed = chunk_index + 1
                if completed == len(chunks) or completed % args.log_every == 0:
                    print(
                        f"ingest sample {index + 1}/{len(data)}: "
                        f"{completed}/{len(chunks)}",
                        flush=True,
                    )

    await asyncio.gather(*(ingest_sample(index, item) for index, item in enumerate(data)))
    checkpoint["complete"] = all(
        checkpoint["samples"].get(str(index), {}).get("complete", False)
        for index in range(len(data))
    )
    checkpoint["runtime_s"] = round(time.monotonic() - phase_started, 3)
    await save_checkpoint()
    print(f"Ingest complete: {checkpoint_path}", flush=True)


def _verify_ingest_complete(storage_root: str, args, model_name: str, data_count: int):
    checkpoint_path = os.path.join(storage_root, "ingest_checkpoint.json")
    checkpoint = _load_json(checkpoint_path, {})
    if not checkpoint:
        raise RuntimeError(f"Missing ingest checkpoint: {checkpoint_path}")
    _validate_ingest_checkpoint(
        checkpoint,
        args,
        model_name,
        data_count,
        validate_layout=False,
    )
    incomplete = [
        index
        for index in range(data_count)
        if not checkpoint.get("samples", {}).get(str(index), {}).get("complete", False)
    ]
    if incomplete:
        raise RuntimeError(
            f"Ingest is incomplete for sample indexes {incomplete}. "
            "Resume --phase ingest before evaluation."
        )


async def _run_evaluate(args, memory, storage_root, adapter, data, model_cfg):
    _verify_ingest_complete(storage_root, args, model_cfg.model, len(data))
    output_dir = args.output_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "results",
        f"mem0_async_phased_{args.run_id}",
    )
    os.makedirs(output_dir, exist_ok=True)
    qa_checkpoint_path = os.path.join(output_dir, f"qa_checkpoint_{args.run_id}.json")
    if args.fresh_evaluation and os.path.isfile(qa_checkpoint_path):
        os.remove(qa_checkpoint_path)
    qa_settings = {
        "version": 2,
        "dataset": "locomo",
        "run_id": args.run_id,
        "model_config": args.model_config,
        "model": model_cfg.model,
        "sample_count": len(data),
        "top_k": args.top_k,
        "full": args.full,
        "answer_max_tokens": args.answer_max_tokens,
    }
    qa_checkpoint = _load_json(
        qa_checkpoint_path,
        {**qa_settings, "results": {}},
    )
    qa_mismatches = [
        f"{key}: stored={qa_checkpoint.get(key)!r}, requested={value!r}"
        for key, value in qa_settings.items()
        if qa_checkpoint.get(key) != value
    ]
    if qa_mismatches:
        raise RuntimeError(
            "QA checkpoint options do not match. Restore the original options or "
            "use --fresh-evaluation:\n" + "\n".join(qa_mismatches)
        )
    checkpoint_lock = asyncio.Lock()

    answerer = AsyncCountingAnswerer(
        model_cfg.api_base,
        model_cfg.api_key,
        model_cfg.model,
        args.answer_max_tokens,
    )
    metrics = EvalMetrics()
    metrics.primary_metric = "f1"
    metrics.raw_info = {}

    for saved in qa_checkpoint.get("results", {}).values():
        metrics.record_result(saved["result"], saved["category"])

    qa_semaphore = asyncio.Semaphore(max(1, args.qa_workers))
    pending = []

    async def answer_question(sample_index: int, query_index: int, query: dict):
        key = f"{sample_index}:{query_index}"
        async with qa_semaphore:
            user_id = f"locomo_{sample_index}_{args.run_id}"
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
            prediction, usage = await answerer.answer(prompt)
            result = adapter.evaluate_answer(
                prediction,
                query["answer"],
                None,
                question=query["query"],
                query=query,
            )
            category = query.get("category", "default")
            metrics.record_result(result, category)
            async with checkpoint_lock:
                qa_checkpoint["results"][key] = {
                    "sample_index": sample_index,
                    "query_index": query_index,
                    "question": query["query"],
                    "gold": query["answer"],
                    "prediction": prediction,
                    "category": category,
                    "result": result,
                    "usage": usage,
                }
                _atomic_json_write(qa_checkpoint_path, qa_checkpoint)
            print(
                f"QA sample {sample_index + 1}/{len(data)} "
                f"question {query_index + 1}: F1={result['score']:.2%}",
                flush=True,
            )

    for sample_index, item in enumerate(data):
        for query_index, query in enumerate(adapter.get_queries(item)):
            key = f"{sample_index}:{query_index}"
            if key not in qa_checkpoint.get("results", {}):
                pending.append(answer_question(sample_index, query_index, query))

    print(
        f"QA checkpoint: completed={metrics.total} pending={len(pending)}",
        flush=True,
    )
    try:
        await asyncio.gather(*pending)
    finally:
        await answerer.close()

    qa_stats = PhaseStats(phase="qa")
    for saved in qa_checkpoint.get("results", {}).values():
        usage = saved.get("usage", {})
        qa_stats.add(CallStats(
            phase="qa",
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
            duration=float(usage.get("runtime_s", 0.0)),
        ))
    metrics.merge_phase_stats({"qa": qa_stats})
    behavior = memory.behavior_stats()
    if behavior:
        metrics.raw_info["memory_behavior"] = behavior
    metrics.record_peak_memory()
    generate_report(
        metrics,
        "locomo",
        model_cfg.model,
        "mem0-async-phased",
        output_dir,
    )


async def run(args):
    eval_root = os.path.dirname(os.path.abspath(__file__))
    model_cfg = load_model_config(args.model_config)
    adapter, data = _load_dataset(eval_root, args.full, args.sample_limit)
    require_existing = args.phase == "evaluate"
    memory, storage_root = build_phased_async_mem0(
        model_cfg,
        eval_root,
        args.run_id,
        args.write_concurrency,
        fresh=args.fresh,
        require_existing=require_existing,
    )
    print(
        f"Phase={args.phase} run_id={args.run_id} samples={len(data)} "
        f"chunk_size={args.chunk_size} store={storage_root}",
        flush=True,
    )

    tracemalloc.start()
    try:
        if args.phase in ("ingest", "all"):
            await _run_ingest(args, memory, storage_root, adapter, data, model_cfg.model)
        if args.phase in ("evaluate", "all"):
            await _run_evaluate(args, memory, storage_root, adapter, data, model_cfg)
    finally:
        tracemalloc.stop()
        memory.close()


def main():
    parser = argparse.ArgumentParser(description="Phased async Mem0 + LoCoMo Token-F1")
    parser.add_argument("--phase", choices=["ingest", "evaluate", "all"], required=True)
    parser.add_argument("--run-id", required=True, help="Stable ID shared by ingest and evaluate")
    parser.add_argument("--dataset", choices=["locomo"], default="locomo")
    parser.add_argument("--memory-system", choices=["mem0"], default="mem0")
    parser.add_argument("--model-config", default="memory-llm")
    parser.add_argument("--sample-limit", type=int, default=0)
    parser.add_argument("--build-limit", type=int, default=0)
    parser.add_argument("--chunk-size", type=int, default=1, choices=range(1, 17))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--ingest-workers", type=int, default=1)
    parser.add_argument("--write-concurrency", type=int, default=1)
    parser.add_argument("--qa-workers", type=int, default=4)
    parser.add_argument("--answer-max-tokens", type=int, default=2000)
    parser.add_argument("--log-every", type=int, default=10)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--fresh", action="store_true", help="Delete only this run_id store before ingest")
    parser.add_argument("--fresh-evaluation", action="store_true")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    if args.phase == "evaluate" and args.fresh:
        parser.error("--fresh cannot be used with --phase evaluate")
    if args.log_every < 1:
        parser.error("--log-every must be at least 1")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
