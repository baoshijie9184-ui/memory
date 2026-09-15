# Source mapping

Mem0 OSS snapshot: commit `4fa483907704735ba0bec030e3c946ee1614b50e`, version `2.0.18`, Apache-2.0.

| DesayMem file | Mem0 upstream source | Treatment | Main changes |
| --- | --- | --- | --- |
| `desaymem/extraction/prompts.py` | `mem0/configs/prompts.py` | Migrated | ADD-only + procedural system prompt; dropped FACT_RETRIEVAL / UPDATE prompts |
| `desaymem/extraction/parser.py` | `mem0/memory/utils.py` | Migrated | Kept `parse_messages`, `remove_code_blocks`, `extract_json`; dropped vision/telemetry/Cypher |
| `desaymem/extraction/deduplicator.py` | `mem0/memory/main.py` Phase 5 | Migrated / extracted | MD5 hash skip vs existing + in-batch hashes |
| `desaymem/extraction/extractor.py` | `mem0/memory/main.py` `_add_to_vector_store` Phases 0–2 | Migrated / refactored | Async `complete`; integer id anti-hallucination map; last-k from session store |
| `desaymem/extraction/entities.py` | `mem0/utils/entity_extraction.py` | Migrated / simplified | Same `(type, text)` shape; regex/CJK heuristics; spaCy optional |
| `desaymem/extraction/entity_linker.py` | `mem0/memory/main.py` Phase 7 + `_compute_entity_boosts` | Migrated / extracted | Upsert, unlink on delete, search boost |
| `desaymem/core/enums.py` | `mem0/configs/enums.py` | Migrated | `MemoryType` including `procedural_memory` |
| `desaymem/core/session.py` | `mem0/memory/main.py` `_build_session_scope` | Migrated | tenant/user/occupant/session instead of user/agent/run |
| `desaymem/core/memory.py` | `mem0/memory/main.py` `AsyncMemory` | Migrated / rewritten | `DesayMemory`; 7-phase add; history ADD/DELETE; last-k via SQLite |
| `desaymem/core/exceptions.py` | `mem0/exceptions.py` | Migrated / subset | Dropped platform quota/rate-limit/client exceptions |
| `desaymem/core/models.py` | `mem0/configs/base.py` `MemoryItem` | Rewritten | Cockpit fields + `memory_type` |
| `desaymem/core/config.py` | `mem0/configs/base.py` `MemoryConfig` | Rewritten | Env-var Settings; last-k / entity thresholds |
| `desaymem/providers/llm/base.py` | `mem0/llms/base.py` | Rewritten | Async Protocol `complete` |
| `desaymem/providers/llm/openai_compatible.py` | `mem0/llms/openai.py` | Migrated / refactored | AsyncOpenAI; dropped OpenRouter/tools/store |
| `desaymem/providers/embedding/base.py` | `mem0/embeddings/base.py` | Rewritten | Async Protocol `embed(texts)` |
| `desaymem/providers/embedding/openai_compatible.py` | `mem0/embeddings/openai.py` | Migrated / refactored | Async batch + hard fail on dim mismatch |
| `desaymem/stores/base.py` | `mem0/vector_stores/base.py` | Rewritten | Async store with tenant/user methods + entity/message protocols |
| `desaymem/stores/pgvector.py` | `mem0/vector_stores/pgvector.py` | Migrated / refactored | Own `memory_items` schema; cosine `<=>`; no payload JSONB table; no CREATE on request |
| `desaymem/stores/sqlite_history.py` | `mem0/memory/storage.py` `SQLiteManager` | Migrated | Same `history` + `messages` tables; extra tenant/user on messages |
| `desaymem/stores/messages.py` | n/a (superseded) | Kept unused | Postgres last-k leftover; default path does not dual-write |
| `desaymem/stores/entities.py` | Mem0 `{collection}_entities` vector collection | Migrated | First-class `memory_entities` table |
| `desaymem/retrieval/scoring.py` | `mem0/utils/scoring.py` + pgvector score convert | Migrated | `max(0, 1-distance)` + BM25 sigmoid + `score_and_rank` |
| `desaymem/retrieval/lemmatization.py` | `mem0/utils/lemmatization.py` | Migrated / extended | spaCy optional; ASCII + CJK unigram/bigram fallback |
| `desaymem/retrieval/retriever.py` | `mem0/memory/main.py` `_search_vector_store` | Migrated | Nine-step hybrid: lemma → embed → semantic → BM25 → entity → rank |
| `desaymem/api/*` | n/a (Mem0 OSS has no this FastAPI surface) | Original | DesayMem REST |
| `desaymem/services/memory_service.py` | n/a | Original | API facade |
| `desaymem/stores/memory.py` | n/a | Original | In-memory store for tests |
| `desaymem/layers/*` | n/a | Original | L2 episode continuation, L3 belief distillation, semantic rerank |
| `desaymem/stores/profile.py` | n/a | Original | `profile_beliefs` + snapshot store |
| `migrations/001_initial.sql` | n/a (not Mem0 tables) | Original | DesayMem memory_items schema |
| `migrations/002_session_entities.sql` | n/a | Original | memory_type + session_messages + memory_entities |
| `migrations/003_bm25.sql` | Mem0 `payload->>'text_lemmatized'` | Original | `memory_items.text_lemmatized` + `simple` FTS GIN |
| `migrations/004_layers.sql` | n/a | Original | `profile_beliefs` + `user_profile_snapshots` |

Directly copied or rewritten Mem0 files keep Apache-2.0 copyright headers.
