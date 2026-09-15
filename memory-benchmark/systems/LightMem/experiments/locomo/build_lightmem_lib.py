"""为 structmem(LightMem) 在 vehiclemembench/longmemeval/carmem 建微量库。

与 add_locomo.py 同管线（LightMemory.add_memory -> summarize -> offline update），
但输入来自 VehicleMem-Eval adapter 的 build_history 输出（与 mem0 评测吃的数据
完全一致），collection 命名 {dataset}_user_{idx}，落库到独立目录（不混 LoCoMo 库）。

用法（微量验证）:
  python3 build_lightmem_lib.py --dataset vehiclemembench --sample-idx 0 --turn-limit 200
  python3 build_lightmem_lib.py --dataset longmemeval --sample-idx 0 --turn-limit 550
  python3 build_lightmem_lib.py --dataset carmem --sample-idx 0 --turn-limit 0(全部)

评测时 bridge 走 alias 映射 user_0 -> {dataset}_user_0（见 evalcore/memory_bridges.py
--lightmem-root 换根目录 + alias）。
"""
import argparse
import datetime
import json
import os
import shutil
import sqlite3
import sys
import time

# VehicleMem-Eval adapters（sys.path 指向其根目录）
EVAL_ROOT = "/data/pengshuang/memory-benchmark/datasets/VehicleMem-Eval"
sys.path.insert(0, EVAL_ROOT)

# LightMem 包（沿用 add_locomo.py 的 import 环境）
sys.path.insert(0, "/data/pengshuang/memory-benchmark/systems/LightMem/src")
sys.path.insert(0, "/data/pengshuang/memory-benchmark/systems/LightMem/experiments/locomo")

from lightmem.memory.lightmem import LightMemory
from prompts import (
    METADATA_GENERATE_PROMPT_locomo,
    LoCoMo_Event_Binding_factual,
    LoCoMo_Event_Binding_relational,
)

# 可用环境变量覆盖（run_full.sh 传入）: LIGHTMEM_API_KEY / LIGHTMEM_API_BASE / LIGHTMEM_LLM_MODEL
API_KEY = os.environ.get("LIGHTMEM_API_KEY", "boluoboluomi")
API_BASE_URL = os.environ.get("LIGHTMEM_API_BASE", "http://127.0.0.1:20140/v1")
LLM_MODEL = os.environ.get("LIGHTMEM_LLM_MODEL", "memory-llm")
LLMLINGUA_MODEL_PATH = "/data/pengshuang/desaymem/models/llmlingua-2-bert-base-multilingual-cased-meetingbank"
EMBEDDING_MODEL_PATH = "/data/pengshuang/desaymem/models/bge-m3"
EMBEDDING_MODEL_DIMS = 1024

# 新数据集的库目录（与 LoCoMo 全量库分开）
LIB_ROOT = "/data/pengshuang/memory-benchmark/data/lightmem/qdrant_new_datasets"
QDRANT_PRE_UPDATE_DIR = os.path.join(LIB_ROOT, "qdrant_pre_update")
QDRANT_POST_UPDATE_DIR = os.path.join(LIB_ROOT, "qdrant_post_update")

ADAPTERS = {
    "vehiclemembench": ("adapters.vehiclemembench_adapter", "VehicleMemBenchAdapter",
                        dict(history_dir=f"{EVAL_ROOT}/datasets/vehiclemembench/history",
                             qa_dir=f"{EVAL_ROOT}/datasets/vehiclemembench/qa_data")),
    "longmemeval": ("adapters.longmemeval_adapter", "LongMemEvalAdapter",
                    dict(data_file=f"{EVAL_ROOT}/datasets/longmemeval/longmemeval_s_cleaned.json")),
    "carmem": ("adapters.carmem_adapter", "CarMemAdapter",
               dict(data_file=f"{EVAL_ROOT}/datasets/carmem/dataset.jsonl")),
    "desaymem": ("adapters.desaymem_adapter", "DesayMemAdapter", dict()),
}


def load_adapter(dataset, sample_idx):
    mod_name, cls_name, kwargs = ADAPTERS[dataset]
    import importlib
    cls = getattr(importlib.import_module(mod_name), cls_name)
    a = cls(**kwargs)
    data = a.load_data()
    return a, data[sample_idx]


def history_to_turns(history):
    """build_history 的 [{role,content,timestamp?}] -> [(user,assistant,ts)] 轮次。
    没有 assistant 的（vmb/carmem 单边对话）补空串, 与 LoCoMo add_locomo 同构。"""
    turns = []
    ts = ""
    pending_user = None
    for m in history:
        role = m.get("role", "user")
        content = m.get("content", "") or ""
        t = m.get("timestamp") or m.get("time_stamp") or ""
        if t:
            ts = str(t)
        if role == "user":
            if pending_user is not None:
                turns.append((pending_user, "", ts))
            pending_user = content
        else:
            if pending_user is None:
                pending_user = ""
            turns.append((pending_user, content, ts))
            pending_user = None
    if pending_user is not None:
        turns.append((pending_user, "", ts))
    # 数据集无时间戳时（carmem）回填合成时间戳，LightMem 要求非空
    base = datetime.datetime(2025, 1, 1)
    for i, t in enumerate(turns):
        if not t[2]:
            turns[i] = (t[0], t[1], (base + datetime.timedelta(minutes=i)).strftime("%Y/%m/%d (%a) %H:%M"))
    return turns


def load_lightmem(collection_name, args, base_dir=QDRANT_POST_UPDATE_DIR):
    config = {
        "pre_compress": True,
        "pre_compressor": {
            "model_name": "llmlingua-2",
            "configs": {
                "llmlingua_config": {
                    "model_name": LLMLINGUA_MODEL_PATH,
                    "device_map": "cuda",
                    "use_llmlingua2": True,
                },
                "compress_config": {
                    "instruction": "", "rate": 0.6, "target_token": -1
                },
            }
        },
        "topic_segment": True,
        "precomp_topic_shared": True,
        "topic_segmenter": {"model_name": "llmlingua-2"},
        "messages_use": "user_only",
        "metadata_generate": True,
        "text_summary": True,
        "memory_manager": {
            "model_name": "openai",
            "configs": {
                "model": LLM_MODEL,
                "api_key": API_KEY,
                "max_tokens": 8192,
                "openai_base_url": API_BASE_URL,
            },
        },
        "extract_threshold": 0.1,
        "index_strategy": "embedding",
        "text_embedder": {
            "model_name": "huggingface",
            "configs": {
                "model": EMBEDDING_MODEL_PATH,
                "embedding_dims": EMBEDDING_MODEL_DIMS,
                "model_kwargs": {"device": "cuda"},
            },
        },
        "retrieve_strategy": "embedding",
        "embedding_retriever": {
            "model_name": "qdrant",
            "configs": {
                "collection_name": collection_name,
                "embedding_model_dims": EMBEDDING_MODEL_DIMS,
                "path": f"{base_dir}/{collection_name}",
                "on_disk": True,
            },
        },
        "summary_retriever": {
            "model_name": "qdrant",
            "configs": {
                "collection_name": f"{collection_name}_summary",
                "embedding_model_dims": EMBEDDING_MODEL_DIMS,
                "path": f"{base_dir}/{collection_name}_summary",
                "on_disk": True,
            }
        },
        "update": "offline",
        "logging": {"level": "INFO", "file_enabled": False, "log_dir": "/tmp"},
        "extraction_mode": args.extraction_mode,
    }
    return LightMemory.from_config(config)


def collection_entry_count(collection_name, base_dir):
    db = os.path.join(base_dir, collection_name, "collection", collection_name, "storage.sqlite")
    if not os.path.exists(db):
        return 0
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("SELECT count(*) FROM points").fetchone()[0])
    except Exception:
        return -1
    finally:
        conn.close()



def build_desaymem_libraries(args):
    """desaymem: 每个 scope 一个 collection（usr_X + usr_X_veh_Y 细粒度）。"""
    adapter = load_adapter("desaymem", 0)[0]
    item = adapter.load_data()[0]
    scopes = item["scopes"]
    prompt_arg = (
        {"factual": LoCoMo_Event_Binding_factual, "relational": LoCoMo_Event_Binding_relational}
        if args.extraction_mode == "event" else METADATA_GENERATE_PROMPT_locomo
    )
    os.makedirs(QDRANT_PRE_UPDATE_DIR, exist_ok=True)
    os.makedirs(QDRANT_POST_UPDATE_DIR, exist_ok=True)
    only = {x for x in args.only_scopes.split(",") if x}
    total = len(scopes)
    for si, (uid, msgs) in enumerate(sorted(scopes.items())):
        if only and uid not in only:
            continue
        collection = f"desaymem_{uid}"
        if args.skip_existing and os.path.isdir(
                os.path.join(QDRANT_POST_UPDATE_DIR, collection, "collection", collection)):
            print(f"[desaymem {si+1}/{total}] {uid}: 已存在，跳过")
            continue
        turns = []
        for m in msgs:
            c = m["content"]
            t = m.get("time_stamp", "")
            if m["role"] == "user":
                turns.append([c, "", t])
            elif turns and not turns[-1][1]:
                turns[-1][1] = c
            else:
                turns.append(["", c, t])
        if args.turn_limit and args.turn_limit > 0:
            turns = turns[: args.turn_limit]
        print(f"[desaymem {si+1}/{total}] {uid}: {len(msgs)} msgs -> collection {collection}")
        lightmem = load_lightmem(collection, args)
        t0 = time.time()
        for i, (u, a, ts) in enumerate(turns):
            mlist = []
            if u:
                mlist.append({"role": "user", "content": u, "speaker_id": "speaker_a",
                              "speaker_name": uid, "time_stamp": ts})
            if a:
                mlist.append({"role": "assistant", "content": a, "speaker_id": "speaker_a",
                              "speaker_name": uid, "time_stamp": ts})
            if not mlist:
                continue
            is_last = i == len(turns) - 1
            lightmem.add_memory(messages=mlist, METADATA_GENERATE_PROMPT=prompt_arg,
                                force_segment=is_last, force_extract=is_last)
        print(f"  done ({time.time()-t0:.0f}s), entries="
              f"{collection_entry_count(collection, QDRANT_POST_UPDATE_DIR)}")
        src = f"{QDRANT_POST_UPDATE_DIR}/{collection}"
        dst = f"{QDRANT_PRE_UPDATE_DIR}/{collection}"
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True, choices=list(ADAPTERS))
    ap.add_argument("--sample-idx", type=int, default=0)
    ap.add_argument("--turn-limit", type=int, default=200,
                    help="0 = 全部轮次; 微量验证用截断")
    ap.add_argument("--extraction-mode", default="event", choices=["flat", "event"])
    ap.add_argument("--enable-summary", action="store_true")
    ap.add_argument("--summary-time-window", type=int, default=3600)
    ap.add_argument("--summary-top-k-seeds", type=int, default=15)
    ap.add_argument("--skip-existing", action="store_true",
                    help="post_update 下已有同名 collection 则跳过（断点续跑）")
    ap.add_argument("--only-scopes", default="",
                    help="只建指定 scope（逗号分隔），如 usr_father_veh_sed_001")
    args = ap.parse_args()

    os.makedirs(QDRANT_PRE_UPDATE_DIR, exist_ok=True)
    os.makedirs(QDRANT_POST_UPDATE_DIR, exist_ok=True)

    if args.dataset == "desaymem":
        build_desaymem_libraries(args)
        return

    adapter, item = load_adapter(args.dataset, args.sample_idx)
    history = adapter.build_history(item)
    turns = history_to_turns(history)
    if args.turn_limit and args.turn_limit > 0:
        turns = turns[: args.turn_limit]
    collection = f"{args.dataset}_user_{args.sample_idx}"
    print(f"[{args.dataset}] sample {args.sample_idx}: {len(history)} msgs -> {len(turns)} turns "
          f"-> collection {collection}")

    prompt_arg = (
        {"factual": LoCoMo_Event_Binding_factual, "relational": LoCoMo_Event_Binding_relational}
        if args.extraction_mode == "event" else METADATA_GENERATE_PROMPT_locomo
    )

    lightmem = load_lightmem(collection, args)
    t0 = time.time()
    for i, (u, a, ts) in enumerate(turns):
        msgs = [
            {"role": "user", "content": u, "speaker_id": "speaker_a", "speaker_name": "user",
             "time_stamp": ts},
            {"role": "assistant", "content": a, "speaker_id": "speaker_a",
             "speaker_name": "user", "time_stamp": ts},
        ]
        is_last = i == len(turns) - 1
        lightmem.add_memory(
            messages=msgs, METADATA_GENERATE_PROMPT=prompt_arg,
            force_segment=is_last, force_extract=is_last,
        )
        if (i + 1) % 20 == 0:
            print(f"  add_memory {i+1}/{len(turns)} ({time.time()-t0:.0f}s)")
    print(f"add_memory 完成 ({time.time()-t0:.0f}s), "
          f"entries={collection_entry_count(collection, QDRANT_POST_UPDATE_DIR)}")

    # 备份 pre_update（与 add_locomo Phase 2 一致）
    src = f"{QDRANT_POST_UPDATE_DIR}/{collection}"
    dst = f"{QDRANT_PRE_UPDATE_DIR}/{collection}"
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    if args.enable_summary:
        print("生成 summary ...")
        lm2 = load_lightmem(collection, args, base_dir=QDRANT_PRE_UPDATE_DIR)
        lm2.summarize(
            retrieval_scope="global",
            time_window=args.summary_time_window,
            top_k_seeds=args.summary_top_k_seeds,
        )
        print(f"summary 完成, summary entries="
              f"{collection_entry_count(collection + '_summary', QDRANT_POST_UPDATE_DIR)}")

    print("done:", collection)


if __name__ == "__main__":
    main()
