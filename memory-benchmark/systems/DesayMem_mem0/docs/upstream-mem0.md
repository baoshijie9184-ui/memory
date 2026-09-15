# Upstream Mem0

## Official repository

https://github.com/mem0ai/mem0

## Migration snapshot

| Field | Value |
| --- | --- |
| Migration date | 2026-08-31 |
| Local source | `D:\agent memory\code\mem0` |
| Upstream remote | `https://github.com/mem0ai/mem0.git` |
| Upstream branch | `main` |
| Upstream commit SHA | `4fa483907704735ba0bec030e3c946ee1614b50e` |
| Upstream tag describe | `ts-v3.1.6-27-g4fa48390` |
| Python package version | `mem0ai==2.0.18` (`pyproject.toml`) |
| License | Apache License 2.0 |

The commit message at HEAD was:

> fix(ci): make the vouch check speak, unblock list updates, widen the docs exemption (#6974)

## Current migration scope

DesayMem_mem0 migrated the **OSS memory core** needed for a privately
deployable add / search / list / delete loop:

- ADD-only conversation extraction (`ADDITIVE_EXTRACTION_PROMPT`)
- Procedural memory (`PROCEDURAL_MEMORY_SYSTEM_PROMPT`, `memory_type=procedural_memory`)
- Last-k session messages and ADD/DELETE history (SQLite `history.db`)
- Entity extraction, entity store, memory linking, and search boost
- Nine-step hybrid search (semantic + BM25 + entity boost)
- `infer=false` raw-message ingest
- JSON parse / code-fence stripping
- MD5 content-hash dedup
- OpenAI-compatible LLM and embedding client patterns
- pgvector cosine search score conversion (`max(0, 1 - distance)`)
- Structured error classes (subset)
- `user_id` scoped add / search / get_all / delete / delete_all orchestration

The public namespace is `desaymem`. Runtime does **not** import `mem0` or
`mem0ai`, and does **not** call Mem0 Platform / `MemoryClient`.

## Not migrated

- `mem0.client` / `MemoryClient` / hosted platform API
- Graph memory, Neo4j, Memgraph, Neptune
- MCP, LangChain, CrewAI, and other framework integrations
- Frontend dashboard / cookbooks
- Vector stores other than the pgvector *idea* (Qdrant, FAISS, Milvus, ...)
- LLM / embedding providers other than OpenAI-compatible HTTP
- Rerankers
- Graph memory (Neo4j / Memgraph) — not in Mem0 default MemoryConfig
- spaCy as a hard dependency (optional if installed)
- Telemetry (PostHog), first-run notices, decay / temporal platform features
- Cloud-vendor deploy templates

## Known differences vs Mem0 OSS 2.0.18

1. **ADD-only is preserved.** v1 does **not** implement UPDATE, conflict
   replacement, or time evolution. Mem0 OSS v2.0.18 itself already uses
   additive extraction rather than the older FACT_RETRIEVAL + UPDATE prompt.
2. Isolation is `tenant_id + user_id` with cockpit metadata columns, not
   Mem0 `agent_id` / `run_id` JSON payload fields.
3. Table schema is `memory_items` with first-class columns. Mem0 pgvector
   uses `id / vector / payload JSONB`.
4. Last-k and history use Mem0's SQLite third store (`history` + `messages`).
   `tenant_id`/`user_id` columns are added so `delete_all` can isolate without
   parsing `session_scope`. Postgres `session_messages` from migration 002 is
   not written by the default path.
5. Entity store lives in PostgreSQL `memory_entities` (Mem0 uses a second vector collection). Linking and search boost follow Mem0 Phase 7 / `_compute_entity_boosts`. spaCy is optional; default extraction is regex/heuristic so Chinese cockpit text works without extra models.
6. Search is Mem0's nine-step hybrid: semantic + BM25 + entity boost. BM25
   only reranks semantic candidates. Lemma fallback keeps CJK tokens such as
   `空调` searchable under PostgreSQL `simple` FTS.
7. Result envelopes are DesayMem-owned (`memories`, `extracted`,
   `skipped_duplicates`) instead of Mem0 `{"results": [...]}`.
8. LLM and embedding clients are async.
9. Schema is applied by SQL migration / Postgres init, never on first API
   request. `memory_type=procedural_memory` uses Mem0's procedural prompt.

## How to sync upstream later

1. Update the local clone: `git -C "D:\agent memory\code\mem0" fetch && git log -1`.
2. Diff at least:
   - `mem0/memory/main.py` (`_add_to_vector_store`, `search`, `delete`)
   - `mem0/configs/prompts.py` (`ADDITIVE_EXTRACTION_PROMPT`)
   - `mem0/memory/utils.py`
   - `mem0/llms/openai.py`, `mem0/embeddings/openai.py`
   - `mem0/vector_stores/pgvector.py`
3. Port behavior into `desaymem.*`; do not add `mem0ai` as a dependency.
4. Update this file (commit SHA, version, date) and `docs/source-mapping.md`.
5. Re-run `pytest` and `python scripts/compare_with_upstream.py`.
