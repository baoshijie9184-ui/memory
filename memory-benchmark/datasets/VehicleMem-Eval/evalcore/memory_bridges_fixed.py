"""
记忆系统 bridge 注册表 — 多系统统一赛场

每个 builder 返回的对象满足 eval 引擎的窄接口契约（见改造记录文档 §1.2）:
    add_memory(user_input=, agent_response=, user_id=, timestamp=)
    retrieve_memory(query, user_id, top_k=5) -> str
    ingest_history(path, uid) / ingest_history_text(text, uid)   # 可选
    delete_memoryos_user(uid)                                     # 可选

设计决策（对应改造记录 §1.3）:
    D1 所有系统只出检索文本，作答 LLM 由框架统一
    D2 进程内 import；重依赖系统（LightMem）走 qdrant 直读复用已有库
    D3 每次固定版运行重建独立存储，样本间按 user_id 隔离且测完保留
    D4 mem0 用官方库（systems/mem0 本地可编辑安装）
"""

import os
import sys


# ---------------------------------------------------------------------------
# 公共工具
# ---------------------------------------------------------------------------

def _per_user_dir(base: str, user_id: str) -> str:
    """每用户独立存储目录（D3 库隔离）。base 由 builder 决定。"""
    d = os.path.join(base, user_id)
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# mem0（官方库，OpenAI 兼容 LLM + OpenAI 兼容 embedding + Qdrant 本地模式）
# ---------------------------------------------------------------------------

class Mem0Bridge:
    """mem0 官方 Memory 的窄接口包装。

    - add: messages 构造 [user, assistant] 消息对；infer=True（mem0 内部 LLM
      抽取事实并自主决策 add/update/delete——这是 mem0 的核心机制，保持默认）
    - search: filters={"user_id"} 作用域隔离，返回 memory 文本列表
    - delete_memoryos_user: no-op，对齐官方 LoCoMo runner 的保留行为
    """

    def __init__(self, model_cfg, storage_root: str):
        from mem0 import Memory
        from .llm_proxy import ensure_proxy, proxy_llm_base, proxy_embed_base

        # llm/embedder 走本地记账代理 → 真实服务：mem0 内部的抽取/决策/向量化
        # 调用（不经 CountingLLMClient）也被 phase 记账（2026-09-15 成本完整性改造）
        ensure_proxy()
        _llm_base = proxy_llm_base()
        _embed_base = proxy_embed_base()
        self._history_db_path = os.path.join(storage_root, "history_fixed.db")

        # from_config 接收 dict（main.py:733 做 MemoryConfig(**config_dict)），
        # 直接传 MemoryConfig 实例会报 "argument after ** must be a mapping"
        cfg = {
            # 固定版使用独立历史库，不读写原版 history.db。
            "history_db_path": self._history_db_path,
            "llm": {
                "provider": "openai",
                "config": {
                    "model": model_cfg.model,
                    "api_key": model_cfg.api_key,
                    "openai_base_url": _llm_base,
                },
            },
            "embedder": {
                "provider": "openai",
                "config": {
                    "model": model_cfg.embedding_model,
                    "api_key": model_cfg.embedding_key,
                    "openai_base_url": _embed_base,
                    "embedding_dims": model_cfg.embedding_dim,
                },
            },
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "eval_mem0",   # 同 run 内所有用户共库，user_id 过滤隔离
                    "path": storage_root,              # qdrant 本地模式（无 server）
                    "on_disk": True,
                    "embedding_model_dims": model_cfg.embedding_dim,
                },
            },
        }
        self._m = Memory.from_config(cfg)
        self._storage_root = storage_root

    # ---- 窄接口 ----
    def add_memory(self, user_input, agent_response=None, user_id=None, timestamp=None):
        messages = []
        if user_input:
            messages.append({"role": "user", "content": user_input})
        if agent_response:
            messages.append({"role": "assistant", "content": agent_response})
        if not messages:
            return
        self._m.add(messages, user_id=user_id, infer=True)

    def retrieve_memory(self, query, user_id=None, top_k=5):
        res = self._m.search(query, filters={"user_id": user_id}, top_k=top_k)
        # search 返回 {"results": [{"memory": ..., "score": ...}]} 或列表
        items = res.get("results", res) if isinstance(res, dict) else res
        return "\n".join(f"- {it.get('memory', '')}" for it in items if it.get("memory"))

    def delete_memoryos_user(self, uid):
        # Match the official LoCoMo runner: samples are isolated by user_id and
        # remain available for inspection until the next fixed run rebuilds the store.
        return None

    def _raw_event_counts(self) -> dict:
        """读取本次评测专属 history_fixed.db 中的原始事件计数。"""
        if not os.path.isfile(self._history_db_path):
            return {}
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self._history_db_path)) as conn:
            return {
                event: count
                for event, count in conn.execute(
                    "SELECT event, COUNT(*) FROM history GROUP BY event"
                )
                if event
            }

    def behavior_stats(self) -> dict:
        """统计本次运行中由 mem0 自身产生的 ADD/UPDATE/DELETE。"""
        try:
            events = self._raw_event_counts()
            if not events:
                return {}
            out = {"memory_events": events}
            adds = events.get("ADD", 0)
            dels = events.get("DELETE", 0)
            if adds + dels:
                out["delete_ratio"] = round(dels / (adds + dels), 4)
            return out
        except Exception:
            return {}


# ---------------------------------------------------------------------------
# LightMem（qdrant 直读复用已有库 — D2 设计决策）
# ---------------------------------------------------------------------------

_LIGHTMEM_QDRANT_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))),
    "data", "lightmem", "qdrant_post_update",
)


class LightMemBridge:
    """qdrant 直读 bridge（单层 entries 检索）— 注册名 structmem-nosummary。

    - add_memory: 零成本 no-op。LoCoMo 全量库由 add_locomo.py 离线跑好
      （extraction_mode=event + summary），bridge 只负责检索复用（D2）。
      user_id 必须映射到已有 collection（如 user_0 -> conv-26, 见 _alias）。
    - retrieve_memory: sqlite 直读 points（payload 含 memory/speaker/time），
      query 用 OpenAI 兼容 embedding API（vLLM bge-m3，与 mem0 同服务）算
      cosine top-k。不加载 LightMem 重依赖（llmlingua/cuda/spacy）。
    - delete_memoryos_user: no-op（复用的库不能删，只清理运行态）
    """

    def __init__(self, model_cfg, qdrant_root=_LIGHTMEM_QDRANT_ROOT, alias=None):
        import sqlite3
        self._sqlite3 = sqlite3
        self._root = qdrant_root
        # user_id -> collection_name 映射（默认约定: user_0 -> 第一个排序后的 conv-*）
        self._alias = dict(alias or {})
        self._dim = model_cfg.embedding_dim or 1024
        # OpenAI 兼容 embedding 客户端（bge-m3 @ vLLM）
        # 走本地记账代理 → 真实 embedding 服务：retrieval 的 query 向量化
        # 也计入 phase 账（2026-09-15 成本完整性改造，与 Mem0Bridge 同口径）
        from openai import OpenAI
        from .llm_proxy import ensure_proxy, proxy_embed_base
        ensure_proxy()
        self._emb = OpenAI(
            base_url=proxy_embed_base(), api_key=model_cfg.embedding_key,
        )
        self._emb_model = model_cfg.embedding_model

    def _collection_for(self, user_id: str) -> str:
        if user_id in self._alias:
            return self._alias[user_id]
        # carmem 等协议用复合 uid（user_0_p0_user_equal）:
        # 剥后缀后命中 alias, 保证同一用户的子库都指向同一 collection
        parts = (user_id or "").split("_")
        for cut in range(len(parts) - 1, 0, -1):
            base = "_".join(parts[:cut])
            if base in self._alias:
                return self._alias[base]
        if self._alias:
            # 冒烟只建了部分用户的库: 未建库的用户回退到第一个已有库
            return next(iter(sorted(self._alias.values())))
        # 约定: 全量库 collection 为 conv-26/30/41...；按目录序给 user_N 映射
        convs = sorted(
            d for d in os.listdir(self._root)
            if d.startswith("conv-") and not d.endswith("_summary")
        ) if os.path.isdir(self._root) else []
        if not convs:
            # 新数据集库命名兜底: 找 {dataset}_user_N 目录
            cands = sorted(
                d for d in os.listdir(self._root)
                if d.startswith(user_id) and not d.endswith("_summary")
            ) if os.path.isdir(self._root) else []
            if cands:
                return cands[0]
            raise ValueError(f"LightMem 全量库不存在: {self._root}（先跑 add_locomo.py）")
        idx = int(user_id.split("_")[-1]) if user_id.rsplit("_", 1)[-1].isdigit() else 0
        return convs[idx % len(convs)]

    def _load_points(self, collection: str):
        import pickle
        db = os.path.join(self._root, collection, "collection", collection,
                          "storage.sqlite")
        if not os.path.isfile(db):
            return []  # 库未建（微量冒烟只建 user_0），降级为空结果
        con = self._sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            pts = []
            for (blob,) in con.execute("select point from points"):
                pt = pickle.loads(blob)
                pts.append({"id": pt.id, "payload": pt.payload,
                            "vector": pt.vector})
            return pts
        finally:
            con.close()

    # ---- 窄接口 ----
    needs_fresh_store = False  # 离线预建库评测只读不写；库是几小时建的全量产物，严禁清理

    def add_memory(self, user_input, agent_response=None, user_id=None, timestamp=None):
        # D2: 复用离线全量库, ingest 是 no-op（build 成本由 LightMem 自身实验承担）
        return None

    def retrieve_memory(self, query, user_id=None, top_k=5):
        import math
        collection = self._collection_for(user_id)
        pts = self._load_points(collection)
        # query embedding
        qv = self._emb.embeddings.create(
            input=[query], model=self._emb_model, encoding_format="float",
        ).data[0].embedding
        # cosine top-k
        def cos(a, b):
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            if not na or not nb:
                return 0.0
            return sum(x * y for x, y in zip(a, b)) / (na * nb)
        scored = sorted(
            ((cos(qv, p["vector"]), p["payload"]) for p in pts if p.get("vector")),
            key=lambda t: -t[0],
        )[:top_k]
        lines = []
        for _, pl in scored:
            mem = pl.get("memory") or pl.get("original_memory") or pl.get("compressed_memory") or ""
            ts = pl.get("time_stamp") or ""
            speaker = pl.get("speaker_name") or ""
            if mem:
                lines.append(f"- [{ts} {speaker}] {mem}")
        return "\n".join(lines)

    def delete_memoryos_user(self, uid):
        # 复用的全量库不能删
        return None


def build_lightmem(model_cfg, llm_client):
    root, prefix = _lightmem_env_opts()
    if root:
        alias = None
        if prefix:
            # uid -> {prefix}{uid}（如 user_N -> carmem_user_N / usr_father -> desaymem_usr_father），
            # 只映射库中实际存在的 collection
            if os.path.isdir(root):
                alias = {
                    d[len(prefix):]: d
                    for d in os.listdir(root)
                    if d.startswith(prefix) and not d.endswith("_summary")
                }
        return LightMemBridge(model_cfg, qdrant_root=root, alias=alias)
    return LightMemBridge(model_cfg)


# ---------------------------------------------------------------------------
# StructMem（LightMem 事件抽取+摘要模式; qdrant 直读, entries+summaries 双层检索）
# ---------------------------------------------------------------------------


class StructMemBridge(LightMemBridge):
    """StructMem = LightMem extraction_mode=event + summary 层（StructMem.md）。

    继承 LightMemBridge 的 qdrant 直读机制，retrieve 额外合并 {_summary}
    collection 的跨事件摘要（payload.summary），按相似度统一排序。
    """

    def retrieve_memory(self, query, user_id=None, top_k=5):
        import math
        collection = self._collection_for(user_id)
        entries = self._load_points(collection)
        summaries = self._load_points(collection + "_summary")
        qv = self._emb.embeddings.create(
            input=[query], model=self._emb_model, encoding_format="float",
        ).data[0].embedding

        def cos(a, b):
            na = math.sqrt(sum(x * x for x in a))
            nb = math.sqrt(sum(x * x for x in b))
            if not na or not nb:
                return 0.0
            return sum(x * y for x, y in zip(a, b)) / (na * nb)

        cands = []
        for p in entries:
            mem = (p["payload"].get("memory")
                   or p["payload"].get("original_memory") or "")
            if mem:
                cands.append((cos(qv, p.get("vector") or []),
                              f"- [{p['payload'].get('time_stamp', '')} "
                              f"{p['payload'].get('speaker_name', '')}] {mem}"))
        for p in summaries:
            s = p["payload"].get("summary", "")
            if s:
                tr = p["payload"].get("time_range", "")
                cands.append((cos(qv, p.get("vector") or []),
                              f"- [summary {tr}] {s.strip()}"))
        cands.sort(key=lambda t: -t[0])
        return "\n".join(line for _, line in cands[:top_k])


def _lightmem_env_opts():
    """环境变量切换 structmem 库（新数据集微量库用）:
    LIGHTMEM_LIB_ROOT: qdrant_post_update 库根目录
    LIGHTMEM_COLLECTION_PREFIX: collection 前缀（如 vehiclemembench）,
      user_N -> {prefix}_user_N; 不设则默认 LoCoMo 全量库。"""
    root = os.environ.get("LIGHTMEM_LIB_ROOT")
    prefix = os.environ.get("LIGHTMEM_COLLECTION_PREFIX")
    return root, prefix


def build_structmem(model_cfg, llm_client):
    root, prefix = _lightmem_env_opts()
    if root:
        alias = None
        if prefix:
            # uid -> {prefix}{uid}（如 user_N -> carmem_user_N / usr_father -> desaymem_usr_father），
            # 只映射库中实际存在的 collection
            if os.path.isdir(root):
                alias = {
                    d[len(prefix):]: d
                    for d in os.listdir(root)
                    if d.startswith(prefix) and not d.endswith("_summary")
                }
        return StructMemBridge(model_cfg, qdrant_root=root, alias=alias)
    return StructMemBridge(model_cfg)


# ---------------------------------------------------------------------------
# 注册表（run.py 的 --memory-system choices 从这里取）
# ---------------------------------------------------------------------------

def build_mem0(model_cfg, llm_client, storage_root=None):
    """固定版使用独立 Qdrant 目录和 history_fixed.db，不影响原版数据库。"""
    if storage_root is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        storage_root = os.path.join(root, "results", "mem_data", f"mem0_fixed_{model_cfg.name}")
    # 评测前清残留（必须在 from_config 之前！之后删目录会让已打开的 sqlite 句柄
    # 指向已删除文件 → 所有写入报 readonly database）。上次中断的 user_x 半截记忆
    # 会污染本次检索，所以每次评测都从干净库开始。
    import shutil
    shutil.rmtree(storage_root, ignore_errors=True)
    os.makedirs(storage_root, exist_ok=True)
    print(f"[mem0-fixed] 已重建独立评测库（不影响原 mem0 库）: {storage_root}")
    return Mem0Bridge(model_cfg, storage_root)


BRIDGES = {
    "none": lambda model_cfg, llm_client: None,
    "mem0": lambda model_cfg, llm_client: build_mem0(model_cfg, llm_client),
    # 注意: 当前全量库是 StructMem 模式建的(event+summary)。"structmem-nosummary"
    # 是消融入口(同库仅 entries 层); 真 lightmem(flat 模式)需另建库后替换。
    "structmem": lambda model_cfg, llm_client: build_structmem(model_cfg, llm_client),
    "structmem-nosummary": lambda model_cfg, llm_client: build_lightmem(model_cfg, llm_client),
    # 后续按改造记录逐步追加: memoryos / desaymem
}

MEMORY_SYSTEMS = list(BRIDGES.keys()) + ["vehiclemem"]   # vehiclemem = desaymem 别名，向后兼容


def build_memory_system(name: str, model_cfg, llm_client):
    """统一入口：eval_engine 调这里替代原 _build_memory_system。"""
    if name == "desaymem" or name == "vehiclemem":
        # 原 DesayMem edge 逻辑（本机暂缺 edge_memory，保留原报错语义，见改造记录 §3）
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        mem_path = os.path.join(os.path.dirname(root), "DesayMem")
        if not os.path.isdir(mem_path):
            raise ValueError(
                f"desaymem 需要 {mem_path} 目录（本机未部署）。"
                f"当前可用: {sorted(BRIDGES.keys())}"
            )
        while mem_path in sys.path:
            sys.path.remove(mem_path)
        sys.path.insert(0, mem_path)
        from edge.edge_memory import EdgeMemory
        data_dir = os.path.join(root, "results", "mem_data", f"desaymem_{model_cfg.name}")
        config = {
            "mode": "edge", "user_id": "eval_user", "data_dir": data_dir,
            "llm_api_key": model_cfg.api_key, "llm_base_url": model_cfg.api_base,
            "llm_model": model_cfg.model,
            "embed_base_url": model_cfg.embedding_base, "embed_model": model_cfg.embedding_model,
            "embed_api_key": model_cfg.embedding_key, "embedding_dim": model_cfg.embedding_dim,
            "short_term_capacity": 4, "mid_term_heat_threshold": 3.0,
            "mid_term_similarity_threshold": 0.6, "long_term_knowledge_capacity": 200,
            "retrieval_queue_capacity": 7, "car_data_capacity": 200,
            "compress_rate": 0.5, "enable_skill_distill": False,
        }
        return EdgeMemory(config)
    if name not in BRIDGES:
        raise ValueError(f"未知记忆系统: {name}. 可用: {sorted(BRIDGES.keys()) + ['desaymem']}")
    return BRIDGES[name](model_cfg, llm_client)
