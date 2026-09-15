"""
评测引擎 — 编排 add → search → judge → metrics

支持多记忆系统（注册表见 evalcore/memory_bridges.py，改造记录见 docs/VehicleMem-Eval多系统接入改造记录.md）:
  none / mem0 / (逐步追加: lightmem / structmem / memoryos / desaymem)
  1. "none" — 不用记忆系统, 直接把历史拼到 prompt 里(baseline)
  2. 其他 — 各系统 bridge，统一窄接口：add_memory / retrieve_memory
"""

import os
import sys
import yaml
import tracemalloc

try:
    from tqdm import tqdm
except ImportError:  # 未装 tqdm 时静默降级为无进度条
    def tqdm(iterable=None, **kwargs):
        return iterable if iterable is not None else _NullBar()

    class _NullBar:
        def update(self, *a): pass
        def close(self): pass
        def set_postfix_str(self, *a): pass

from evalcore.llm_client import CountingLLMClient
from evalcore.model_registry import load_model_config
from evalcore.memory_bridges import build_memory_system as _build_from_registry, MEMORY_SYSTEMS
from metrics.efficiency import EvalMetrics
from metrics.reporter import generate_report


ADAPTERS = {
    "locomo": "adapters.locomo_adapter.LocomoAdapter",
    "longmemeval": "adapters.longmemeval_adapter.LongMemEvalAdapter",
    "carmem": "adapters.carmem_adapter.CarMemAdapter",
    "vehiclemembench": "adapters.vehiclemembench_adapter.VehicleMemBenchAdapter",
    "desaymem": "adapters.desaymem_adapter.DesayMemAdapter",
}



def _load_adapter(dataset: str, dataset_config: dict):
    """动态加载适配器"""
    parts = ADAPTERS[dataset].split(".")
    module_path = ".".join(parts[:-1])
    class_name = parts[-1]

    import importlib
    mod = importlib.import_module(module_path)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if dataset == "locomo":
        cls = getattr(mod, class_name)
        data_file = os.path.join(root, dataset_config["data_file"])
        return cls(data_file=data_file, qa_limit=dataset_config.get("qa_limit", 0))
    elif dataset == "longmemeval":
        cls = getattr(mod, class_name)
        data_file = os.path.join(root, dataset_config["data_file"])
        return cls(data_file=data_file, qa_limit=dataset_config.get("qa_limit", 0))
    elif dataset == "carmem":
        cls = getattr(mod, class_name)
        data_file = os.path.join(root, dataset_config["data_file"])
        return cls(
            data_file=data_file,
            user_limit=dataset_config.get("user_limit", 0),
            pref_limit=dataset_config.get("pref_limit", 0),
        )
    elif dataset == "vehiclemembench":
        cls = getattr(mod, class_name)
        history_dir = os.path.join(root, dataset_config["history_dir"])
        qa_dir = os.path.join(root, dataset_config["qa_dir"])
        return cls(history_dir=history_dir, qa_dir=qa_dir,
                   file_range=dataset_config.get("file_range"))
    elif dataset == "desaymem":
        cls = getattr(mod, class_name)
        return cls(case_limit=dataset_config.get("case_limit", 0))
    raise ValueError(f"未知数据集: {dataset}")


def _resolve_memory_root(eval_root: str) -> str:
    parent = os.path.dirname(eval_root)
    for name in ("DesayMem", "VehicleMem"):
        path = os.path.join(parent, name)
        if os.path.isdir(path):
            return path
    return os.path.join(parent, "DesayMem")


def _build_memory_system(name: str, model_cfg, llm_client: CountingLLMClient):
    """构建记忆系统实例 — 委托 evalcore/memory_bridges.py 注册表（多系统统一赛场）。
    旧 desaymem 硬编码逻辑已迁入 memory_bridges.build_memory_system。"""
    return _build_from_registry(name, model_cfg, llm_client)


def _iter_qa_pairs(history: list[dict]):
    i = 0
    while i < len(history):
        cur = history[i]
        nxt = history[i + 1] if i + 1 < len(history) else None
        ts = cur.get("timestamp") or (nxt.get("timestamp") if nxt else None)
        if cur.get("role") == "user" and nxt and nxt.get("role") == "assistant":
            yield cur.get("content", ""), nxt.get("content", ""), ts
            i += 2
        elif cur.get("role") == "assistant":
            yield "", cur.get("content", ""), ts
            i += 1
        else:
            yield cur.get("content", ""), "", ts
            i += 1


def _vehiclemembench_tools_text() -> str:
    # 官方 111 个车控工具 schema（来自 MINE-USTC/VehicleMemBench
    # evaluation/functions_schema.json），作答时注入完整工具清单。
    import json as _json
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "datasets", "vehiclemembench", "functions_schema.json",
    )
    lines = []
    with open(path, encoding="utf-8") as f:
        for e in _json.load(f):
            props = e.get("parameters", {}).get("properties", {})
            sig = ", ".join(f'{k}=<{v.get("type", "?")}>' for k, v in props.items())
            lines.append(f"- {e['name']}({sig}): {e.get('description', '')}")
    return "\n".join(lines)


def _format_retrieval_context(result) -> str:
    # 多系统赛场：bridge 可能返回 (a) DesayMem 结构化 dict（带 retrieved_scene 等键）
    # 或 (b) 通用系统（mem0/LightMem/MemoryOS 等）的纯文本字符串。
    # 契约：字符串直接作为检索上下文；dict 走原 DesayMem 结构化格式化。
    if isinstance(result, str):
        return result
    parts = []
    scene = result.get("retrieved_scene") or {}
    if scene:
        bits = [f"{k}={v}" for k, v in scene.items() if v]
        if bits:
            parts.append("场景: " + ", ".join(bits))

    top = result.get("retrieved_top_preferences") or {}
    if isinstance(top, dict) and top:
        parts.append("当前应采用的偏好:")
        for etype, p in top.items():
            if not isinstance(p, dict):
                continue
            who = p.get("person", "")
            who_s = f" @{who}" if who and who != "default" else ""
            conds = p.get("conditions") or []
            cond_s = f" [{','.join(conds)}]" if conds else ""
            parts.append(f"- {p.get('entity_type', etype)}{who_s}: {p.get('value', '')}{cond_s}")

    conflicts = result.get("retrieved_household_conflicts") or result.get("retrieved_conflicts") or []
    if conflicts:
        parts.append("多人偏好冲突:")
        for c in conflicts[:8]:
            primary = c.get("primary") or {}
            secondary = c.get("secondary") or {}
            parts.append(
                f"- {c.get('entity_type', '')}: "
                f"{primary.get('person', c.get('person', ''))}={primary.get('value', '')} "
                f"vs {secondary.get('person', '')}={secondary.get('value', '')}"
            )

    prefs = result.get("retrieved_preferences") or []
    if prefs and not top:
        parts.append("已落地偏好:")
        for p in prefs[:12]:
            who = p.get("person", "")
            who_s = f" @{who}" if who and who != "default" else ""
            parts.append(f"- {p.get('entity_type', '')}{who_s}: {p.get('value', '')}")

    profile = result.get("user_profile") or ""
    if profile and str(profile).lower() not in ("none", ""):
        parts.append("用户画像:\n" + str(profile)[:400])

    for kn in (result.get("retrieved_user_knowledge") or [])[:5]:
        parts.append(f"知识: {kn.get('knowledge', kn)}")

    pages = result.get("retrieved_pages") or []
    if pages:
        parts.append("相关记忆:")
        for p in pages[:5]:
            body = p.get("compressed_text") or p.get("user_input") or p.get("memory") or ""
            agent = p.get("agent_response") or ""
            ts = p.get("timestamp") or ""
            event = p.get("event") or {}
            facts = event.get("facts") or []
            line = f"[{ts}] {body[:400]}"
            if agent:
                line += f" | {agent[:200]}"
            if facts:
                line += f" | events: {'; '.join(str(f) for f in facts[:4])}"
            parts.append(line)

    for s in (result.get("retrieved_skills") or [])[:3]:
        parts.append(f"技能: {s.get('name', '')} {s.get('description', '')}")

    return "\n".join(parts)


def _apply_full_mode(ds_config: dict) -> dict:
    """Drop smoke limits so scoring covers the official full split."""
    cfg = dict(ds_config)
    cfg["qa_limit"] = 0
    cfg["user_limit"] = 0
    cfg["pref_limit"] = 0
    cfg["file_range"] = None
    return cfg


def _qa_prompt(dataset: str, retrieved_text: str, query: str, history=None,
               use_memory=True, extra=None) -> str:
    extra = extra or {}
    if not use_memory:
        hist = history or []
        if dataset not in ("locomo", "longmemeval"):
            hist = hist[-100:]
        hist_text = "\n".join(m.get("content", "") for m in hist)
        if dataset == "locomo":
            return (
                "Answer the question using only the conversation history. "
                "If the history does not contain the answer, reply exactly: No information available.\n\n"
                f"Conversation:\n{hist_text}\n\nQuestion: {query}\n\nAnswer:"
            )
        qdate = extra.get("question_date", "")
        date_line = f"Question date: {qdate}\n" if qdate else ""
        return (
            "根据以下对话历史回答问题。\n\n"
            f"对话历史:\n{hist_text}\n\n{date_line}问题: {query}\n\n答案:"
        )
    if dataset == "vehiclemembench":
        tools_text = _vehiclemembench_tools_text()
        return (
            "You are an intelligent in-car AI assistant responsible for fulfilling "
            "user requests by calling the vehicle system API. Analyze the current "
            "situation and perform the appropriate in-car operations.\n\n"
            f"**User Preference Memory:**\n{retrieved_text}\n\n"
            f"**Available Tools:**\n{tools_text}\n\n"
            "**Current Scenario:**\n"
            f"{query}\n\n"
            "**Instructions:**\n"
            "1. Choose the correct tool from the available tools above.\n"
            "2. Output ONLY the tool call(s), one per line, format: "
            'carcontrol_module_action(arg="value")\n'
            "3. When the memory does not support a specific value, perform the "
            "minimal required action.\n"
            "4. Do not explain.\n\nTool call:"
        )
    if dataset == "locomo":
        # LoCoMo 官方 baseline 作答模板（LightMem ANSWER_PROMPT 同源）:
        # 时间推理指令是关键——记忆里多为相对时间("last year"), 需按记忆时间戳
        # 换算绝对日期; 无时间参照则无法作答, 要求输出 no information available。
        speakers = extra.get("speakers") or []
        spk_line = ""
        if speakers:
            spk_line = (
                "The memories below belong to a conversation between "
                f"{speakers[0]} and {speakers[1]}.\n"
            )
        return (
            "You are an intelligent memory assistant tasked with answering "
            "from conversation memories.\n\n"
            "# INSTRUCTIONS:\n"
            "1. Answer using only the memories below.\n"
            "2. Pay special attention to the timestamps in the memories.\n"
            "3. If there is a question about time references (like 'last year', "
            "'two months ago'), calculate the actual date based on the memory "
            "timestamp. For example, if a memory from 4 May 2022 mentions 'went "
            "to India last year', then the trip occurred in 2021.\n"
            "4. Always convert relative time references to specific dates, months, "
            "or years, and ignore the relative reference while answering.\n"
            "5. If the memories do not contain the answer, reply exactly: "
            "no information available.\n"
            "6. The answer should be less than 5-6 words.\n\n"
            f"{spk_line}Memories:\n{retrieved_text}\n\n"
            f"Question: {query}\n\nAnswer:"
        )
    if dataset == "longmemeval":
        qdate = extra.get("question_date", "")
        date_line = f"Question date: {qdate}\n" if qdate else ""
        return (
            "Answer the question using the memories below. "
            "If it cannot be answered, say you don't have enough information.\n\n"
            f"Memories:\n{retrieved_text}\n\n{date_line}Question: {query}\n\nAnswer:"
        )
    return (
        "根据以下记忆、偏好和画像回答问题。若记忆不足请明确说不知道。\n\n"
        f"记忆:\n{retrieved_text}\n\n问题: {query}\n\n答案:"
    )


def run_evaluation(
    dataset: str,
    memory_system: str,
    model_config_name: str,
    sample_limit: int = 0,
    output_dir: str = None,
    full: bool = False,
    build_limit: int = 0,
):
    """执行评测"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    model_cfg = load_model_config(model_config_name)
    with open(os.path.join(root, "config", "datasets.yaml"), "r", encoding="utf-8") as f:
        ds_configs = yaml.safe_load(f)
    ds_config = ds_configs["datasets"][dataset]
    if full:
        ds_config = _apply_full_mode(ds_config)

    adapter = _load_adapter(dataset, ds_config)
    data = adapter.load_data()

    if sample_limit and sample_limit > 0:
        data = data[:sample_limit]

    llm = CountingLLMClient(
        api_base=model_cfg.api_base,
        api_key=model_cfg.api_key,
        model=model_cfg.model,
        judge_api_base=model_cfg.judge_api_base,
        judge_api_key=model_cfg.judge_api_key,
        judge_model=model_cfg.judge_model,
    )

    mem = _build_memory_system(memory_system, model_cfg, llm)

    metrics = EvalMetrics()
    metrics.primary_metric = getattr(adapter, "primary_metric", "accuracy")
    tracemalloc.start()

    mode = "official-full" if full else "smoke"
    print(f"\n{'='*60}")
    print(f"开始评测: {dataset} | {memory_system} | {model_config_name} | {mode}")
    print(f"样本数: {len(data)} | 主指标: {metrics.primary_metric}")
    print(f"{'='*60}\n")

    for idx, item in enumerate(data):
        llm.set_phase("memory_build")
        uid = f"user_{idx}"

        if getattr(adapter, "eval_protocol", "qa") in ("carmem", "desaymem"):
            llm.set_phase("qa")
            adapter.run_official_eval(
                item, mem=mem, llm=llm, metrics=metrics, user_id=uid,
                format_ctx=_format_retrieval_context,
            )
            print(
                f"  样本 {idx+1}/{len(data)} 完成 | "
                f"ACC={metrics.accuracy:.2%} F1/score={metrics.mean_score:.2%} "
                f"({metrics.correct}/{metrics.total})"
            )
            continue

        history = adapter.build_history(item)

        if mem:
            if dataset == "vehiclemembench":
                path = item.get("history_path")
                if path and hasattr(mem, "ingest_history"):
                    mem.ingest_history(path, uid)
                elif item.get("history") and hasattr(mem, "ingest_history_text"):
                    mem.ingest_history_text(item["history"], uid)
            pairs = list(_iter_qa_pairs(history))
            if build_limit:
                pairs = pairs[:build_limit]  # 冒烟提速: 只 ingest 前 build_limit 轮
            build_bar = tqdm(
                pairs, desc=f"  样本{idx+1}/{len(data)} ingest",
                unit="轮", dynamic_ncols=True, mininterval=5.0,
            )
            for user_input, agent_response, ts in build_bar:
                mem.add_memory(
                    user_input=user_input,
                    agent_response=agent_response,
                    user_id=uid,
                    timestamp=ts,
                )
            build_bar.close()

        queries = adapter.get_queries(item)
        qa_bar = tqdm(
            enumerate(queries), total=len(queries),
            desc=f"  样本{idx+1}/{len(data)} QA",
            unit="题", dynamic_ncols=True, mininterval=5.0,
        )
        for qi, q in qa_bar:
            llm.set_phase("retrieval")
            retrieved_text = ""
            if mem:
                result = mem.retrieve_memory(q["query"], uid, top_k=5)
                retrieved_text = _format_retrieval_context(result)

            llm.set_phase("qa")
            qa_prompt = _qa_prompt(
                dataset, retrieved_text, q["query"],
                history=history, use_memory=bool(mem), extra=q,
            )
            pred = llm.chat(
                [{"role": "user", "content": qa_prompt}],
                max_tokens=200, temperature=0.0, disable_thinking=True,
            )

            llm.set_phase("judge")
            result = adapter.evaluate_answer(
                pred, q["answer"], llm, question=q["query"], query=q,
            )
            metrics.record_result(result, q.get("category", "default"))

            qa_bar.set_postfix_str(
                f"ACC={metrics.accuracy:.2%} score={metrics.mean_score:.2%}"
            )

        qa_bar.close()
        print(
            f"  样本 {idx+1}/{len(data)} 完成 | "
            f"ACC={metrics.accuracy:.2%} score={metrics.mean_score:.2%} "
            f"({metrics.correct}/{metrics.total})"
        )

        if mem and hasattr(mem, "delete_memoryos_user"):
            try:
                mem.delete_memoryos_user(uid)
            except Exception:
                pass

    metrics.merge_phase_stats(llm.get_stats())
    metrics.record_peak_memory()
    tracemalloc.stop()

    if output_dir is None:
        output_dir = os.path.join(root, "results")
    generate_report(metrics, dataset, model_cfg.model, memory_system, output_dir)

    return metrics
