# Baseline design

DesayMem_mem0 is the first privately deployable cloud memory baseline for
cockpit agents. It is a **source migration** of Mem0 OSS 2.0.18 core, not a
wrapper around `mem0ai` or Mem0 Cloud.

## Closed loop

```
dialogue
 -> Phase 0 last-k (SQLite messages)
 -> Phase 1 existing memories (pgvector top 10)
 -> Phase 2 ADD-only extraction (or procedural / infer=false)
 -> Phase 3-5 embed + lemma + MD5 dedup
 -> Phase 6 persist memory_items + history ADD
 -> Phase 7 entity extract + link (memory_entities)
 -> nine-step search: lemma / embed / semantic / BM25 / entity / rank
 -> return memories
```

Three stores follow Mem0 OSS default `MemoryConfig` (no graph):

1. Vector store — `memory_items` (PostgreSQL + pgvector, or in-memory in tests)
2. SQLite `history.db` — `history` + last-k `messages`
3. Entity store — `memory_entities` on the same Postgres backend

## Why ADD-only

The selected upstream commit already uses `ADDITIVE_EXTRACTION_PROMPT`.
The LLM may only emit new facts. Duplicate facts are dropped by MD5 hash.
v1 does **not** implement preference conflict replacement, memory rewrite,
decay, or temporal reasoning. Those remain future work.

## Cockpit metadata (stored, lightly filtered)

```json
{
  "tenant_id": "default",
  "user_id": "user_001",
  "vehicle_id": "vehicle_001",
  "occupant_id": "primary",
  "session_id": "session_001",
  "scene": "driving",
  "source": "conversation"
}
```

Profile taxonomy, Event, Cross-event, Skill distillation, proactive service,
and edge-cloud sync were **out of scope** for v1. L2 episode summaries and L3
profile beliefs are now in `desaymem.layers`; see [docs/layers.md](layers.md).

## Providers

- LLM: OpenAI-compatible Chat Completions (Qwen / DeepSeek / vLLM / OpenAI)
- Embedding: OpenAI-compatible embeddings (including BGE-M3 HTTP services)
- Vector store: PostgreSQL 16 + pgvector
- History / last-k: SQLite `HISTORY_DB_PATH` (default `history.db`)
- Default `EMBEDDING_DIMS=1024`

Changing the embedding dimension requires a matching `VECTOR(n)` type in
`migrations/001_initial.sql` on a fresh database.
