"""DesayMemory — core orchestration for add / search / list / delete.

Call chain migrated from mem0.memory.main.AsyncMemory (OSS v2.0.18, commit 4fa48390):

  add (infer=True, default semantic):
    parse messages
    -> load last-k session messages
    -> embed concatenated conversation
    -> search existing memories (top_k=10) for LLM context
    -> ADD-only LLM extraction
    -> MD5 hash dedup
    -> batch embed extracted texts
    -> insert into vector store
    -> entity extract / upsert / link
    -> save current messages into last-k store

  add (memory_type=procedural_memory):
    LLM summarises the conversation with PROCEDURAL_MEMORY_SYSTEM_PROMPT
    -> embed summary -> insert with memory_type=procedural_memory

  add (infer=False):
    store each non-system message as a raw memory (no LLM)

  search (nine steps):
    lemma + entities -> embed -> semantic over-fetch -> keyword/BM25
    -> entity boost -> candidates from semantic only -> score_and_rank
    -> format

Isolation is tenant_id + user_id. Graph memory, telemetry, and Mem0
hosted-cloud APIs are not included.

Three stores follow Mem0 OSS (in-memory backends are tests/fakes only):
  1. vector store — PostgreSQL `memory_items` (pgvector)
  2. SQLite `history.db` — `history` + last-k `messages`
  3. entity store — PostgreSQL `memory_entities` (same Postgres, second table)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from desaymem.core.config import Settings
from desaymem.core.enums import MemoryType
from desaymem.core.exceptions import ConfigurationError, LLMError, ValidationError
from desaymem.core.logging import get_logger
from desaymem.core.models import MemoryScope, ProfileView, SearchFilters
from desaymem.core.result import (
    AddResult,
    DeleteAllResult,
    DeleteResult,
    HistoryResult,
    ListResult,
    SearchResult,
)
from desaymem.core.session import build_session_scope
from desaymem.extraction.deduplicator import drop_duplicate_texts, memory_content_hash
from desaymem.extraction.entity_linker import EntityLinker
from desaymem.extraction.extractor import MemoryExtractor
from desaymem.extraction.parser import parse_messages, remove_code_blocks
from desaymem.extraction.prompts import PROCEDURAL_MEMORY_SYSTEM_PROMPT
from desaymem.layers.distiller import ProfileDistiller
from desaymem.layers.episodes import EpisodeBuilder, episode_similarity
from desaymem.layers.reranker import SemanticReranker
from desaymem.providers.embedding.base import EmbeddingProvider
from desaymem.providers.llm.base import LLMProvider
from desaymem.retrieval.lemmatization import lemmatize_for_bm25
from desaymem.retrieval.retriever import MemoryRetriever, stored_to_item
from desaymem.stores.base import EntityStore, ProfileStore, SessionMessageStore, StoredMemory, VectorStore
from desaymem.stores.memory import InMemoryEntityStore
from desaymem.stores.profile import InMemoryProfileStore
from desaymem.stores.sqlite_history import SQLiteHistoryStore

logger = get_logger(__name__)

_SCOPE_META_KEYS = {
    "tenant_id",
    "user_id",
    "vehicle_id",
    "occupant_id",
    "session_id",
    "scene",
    "source",
    "memory_type",
}


def _normalize_messages(messages: list[dict] | dict | str) -> list[dict]:
    if isinstance(messages, str):
        return [{"role": "user", "content": messages}]
    if isinstance(messages, dict):
        return [messages]
    if not isinstance(messages, list):
        raise ValidationError(
            "messages must be str, dict, or list[dict]",
            error_code="VAL_MESSAGES",
        )
    normalized: list[dict] = []
    for item in messages:
        if not isinstance(item, dict) or item.get("role") is None or item.get("content") is None:
            raise ValidationError(
                "each message must be a dict with role and content",
                error_code="VAL_MESSAGE_ITEM",
            )
        row = {"role": str(item["role"]), "content": str(item["content"])}
        if item.get("name"):
            row["name"] = str(item["name"])
        normalized.append(row)
    if not normalized:
        raise ValidationError("messages must not be empty", error_code="VAL_MESSAGES_EMPTY")
    return normalized


def _scope_from_kwargs(
    user_id: str,
    metadata: dict[str, Any] | None = None,
    tenant_id: str = "default",
    **cockpit: Any,
) -> MemoryScope:
    meta = metadata or {}
    return MemoryScope(
        tenant_id=meta.get("tenant_id") or tenant_id or "default",
        user_id=user_id,
        vehicle_id=cockpit.get("vehicle_id") or meta.get("vehicle_id") or "",
        occupant_id=cockpit.get("occupant_id") or meta.get("occupant_id") or "primary",
        session_id=cockpit.get("session_id") or meta.get("session_id") or "",
        scene=cockpit.get("scene") or meta.get("scene") or "",
        source=cockpit.get("source") or meta.get("source") or "conversation",
    )


def _extra_metadata(extra_metadata: dict[str, Any] | None) -> dict[str, Any]:
    return {key: value for key, value in (extra_metadata or {}).items() if key not in _SCOPE_META_KEYS}


def _absolute_observation_time(value: Any) -> str:
    """Return an absolute ISO-8601 timestamp; never persist relative time text."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError(
                "occurred_at must be an absolute ISO-8601 timestamp",
                error_code="VAL_OCCURRED_AT",
                details={"provided": value},
            ) from exc
    else:
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _episode_time_bounds(
    rows: list[StoredMemory],
    explicit_occurred_at: str | None,
) -> tuple[str, str]:
    if explicit_occurred_at:
        point = _absolute_observation_time(explicit_occurred_at)
        return point, point
    points: list[datetime] = []
    for row in rows:
        raw = (row.metadata or {}).get("occurred_at") or row.created_at
        if raw is None:
            continue
        point = datetime.fromisoformat(_absolute_observation_time(raw))
        points.append(point)
    if not points:
        point = _absolute_observation_time(None)
        return point, point
    return min(points).isoformat(), max(points).isoformat()


def _validate_memory_type(memory_type: str | None) -> str | None:
    if memory_type is None:
        return None
    if memory_type != MemoryType.PROCEDURAL.value:
        raise ValidationError(
            f"Invalid 'memory_type'. Please pass {MemoryType.PROCEDURAL.value} to create procedural memories.",
            error_code="VAL_MEMORY_TYPE",
            details={"provided_type": memory_type, "valid_type": MemoryType.PROCEDURAL.value},
            suggestion=f"Use '{MemoryType.PROCEDURAL.value}' to create procedural memories.",
        )
    return memory_type


def _persistent_history_path(path: str | None) -> str:
    resolved = (path or "history.db").strip() or "history.db"
    if resolved == ":memory:":
        raise ConfigurationError(
            "HISTORY_DB_PATH=:memory: is not allowed for the persistent factory",
            error_code="CFG_HISTORY",
            suggestion="Set HISTORY_DB_PATH to a file such as history.db",
        )
    return resolved


def _default_entity_store(store: VectorStore, embedding_dims: int) -> EntityStore:
    from desaymem.stores.entities import PgEntityStore
    from desaymem.stores.pgvector import PgVectorStore

    if isinstance(store, PgVectorStore):
        return PgEntityStore(store)
    return InMemoryEntityStore(embedding_dims=embedding_dims)


def _default_profile_store(store: VectorStore, embedding_dims: int) -> ProfileStore:
    from desaymem.stores.pgvector import PgVectorStore
    from desaymem.stores.profile import PgProfileStore

    if isinstance(store, PgVectorStore):
        return PgProfileStore(store)
    return InMemoryProfileStore(embedding_dims=embedding_dims)


def _redact_dsn(dsn: str) -> str:
    if "://" not in dsn or "@" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    creds, host = rest.split("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


class DesayMemory:
    def __init__(
        self,
        *,
        llm: LLMProvider,
        embedding: EmbeddingProvider,
        store: VectorStore,
        settings: Settings | None = None,
        message_store: SessionMessageStore | None = None,
        entity_store: EntityStore | None = None,
        history_store: SQLiteHistoryStore | None = None,
        profile_store: ProfileStore | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.llm = llm
        self.embedding = embedding
        self.store = store
        dims = getattr(embedding, "embedding_dims", self.settings.embedding_dims)
        # Mem0: self.db = SQLiteManager(history_db_path); messages + history share it.
        self.db = history_store or message_store or SQLiteHistoryStore(
            self.settings.history_db_path or "history.db"
        )
        self.messages = self.db
        self.entities = entity_store or _default_entity_store(store, dims)
        self.profiles = profile_store or _default_profile_store(store, dims)
        self.extractor = MemoryExtractor(
            llm,
            custom_instructions=self.settings.custom_instructions,
            use_input_language=self.settings.use_input_language,
        )
        self.linker = EntityLinker(
            self.entities,
            embedding,
            match_threshold=self.settings.entity_match_threshold,
            boost_min_similarity=self.settings.entity_boost_min_similarity,
        )
        self.retriever = MemoryRetriever(
            store,
            embedding,
            threshold=self.settings.search_threshold,
            linker=self.linker if self.settings.enable_entity_store else None,
        )
        self.episode_builder = EpisodeBuilder(llm)
        self.distiller = ProfileDistiller(
            llm,
            embedding,
            self.profiles,
            match_threshold=self.settings.profile_match_threshold,
        )
        self.reranker = SemanticReranker(llm)
        self.embedding_model = getattr(embedding, "model", self.settings.embedding_model)
        self.embedding_dims = dims

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        llm: LLMProvider | None = None,
        embedding: EmbeddingProvider | None = None,
        store: VectorStore | None = None,
    ) -> "DesayMemory":
        from desaymem.providers.embedding.openai_compatible import OpenAICompatibleEmbedding
        from desaymem.providers.llm.openai_compatible import OpenAICompatibleLLM
        from desaymem.stores.memory import InMemoryVectorStore
        from desaymem.stores.pgvector import PgVectorStore
        from desaymem.stores.sqlite_history import SQLiteHistoryStore

        history_path = _persistent_history_path(settings.history_db_path)
        if store is not None and isinstance(store, InMemoryVectorStore):
            raise ConfigurationError(
                "from_settings does not use in-memory stores",
                error_code="CFG_STORE",
                suggestion="Use PostgreSQL + pgvector for memories and entities",
            )
        llm = llm or OpenAICompatibleLLM(
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url or None,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            top_p=settings.llm_top_p,
        )
        embedding = embedding or OpenAICompatibleEmbedding(
            model=settings.embedding_model,
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url or None,
            embedding_dims=settings.embedding_dims,
        )
        store = store or PgVectorStore(
            settings.postgres_dsn,
            embedding_dims=settings.embedding_dims,
            min_size=settings.postgres_pool_min,
            max_size=settings.postgres_pool_max,
        )
        return cls(
            llm=llm,
            embedding=embedding,
            store=store,
            settings=settings,
            history_store=SQLiteHistoryStore(history_path),
            entity_store=_default_entity_store(store, settings.embedding_dims),
            profile_store=_default_profile_store(store, settings.embedding_dims),
        )

    def backend_info(self) -> dict[str, str]:
        dsn = getattr(self.store, "_dsn", "") or ""
        history = getattr(self.db, "db_path", type(self.db).__name__)
        return {
            "memories": f"{type(self.store).__name__}{f' dsn={_redact_dsn(dsn)}' if dsn else ''}",
            "entities": type(self.entities).__name__,
            "profile": type(self.profiles).__name__,
            "history": f"{type(self.db).__name__} path={history}",
        }

    async def add(
        self,
        messages: list[dict] | dict | str,
        user_id: str,
        metadata: dict | None = None,
        *,
        tenant_id: str = "default",
        vehicle_id: str = "",
        occupant_id: str = "primary",
        session_id: str = "",
        scene: str = "",
        source: str = "conversation",
        infer: bool = True,
        memory_type: str | None = None,
        prompt: str | None = None,
    ) -> list[dict]:
        scope = _scope_from_kwargs(
            user_id,
            metadata,
            tenant_id=tenant_id,
            vehicle_id=vehicle_id,
            occupant_id=occupant_id,
            session_id=session_id,
            scene=scene,
            source=source,
        )
        result = await self.add_result(
            messages,
            scope,
            extra_metadata=metadata,
            infer=infer,
            memory_type=memory_type,
            prompt=prompt,
        )
        return [item.to_public_dict() for item in result.memories]

    async def add_result(
        self,
        messages: list[dict] | dict | str,
        scope: MemoryScope,
        extra_metadata: dict[str, Any] | None = None,
        *,
        infer: bool = True,
        memory_type: str | None = None,
        prompt: str | None = None,
    ) -> AddResult:
        normalized = _normalize_messages(messages)
        memory_type = _validate_memory_type(memory_type)
        extra = _extra_metadata(extra_metadata)
        if memory_type == MemoryType.PROCEDURAL.value:
            return await self._create_procedural_memory(normalized, scope, extra, prompt=prompt)
        if not infer:
            return await self._add_raw_messages(normalized, scope, extra)

        # === Mem0 V3 seven-phase add (Phase 0–7) + save messages ===
        # Phase 0: Context gathering
        parsed = parse_messages(normalized)
        session_scope = build_session_scope(scope)
        last_messages = await self.messages.get_last_messages(
            session_scope,
            limit=self.settings.last_k_messages,
        )
        profile_summary = ""
        if self.settings.enable_profile:
            profile_summary = await self.profiles.get_snapshot(
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )

        # Phase 1: Existing memory retrieval
        neighbors = await self.retriever.neighbors_for_extraction(
            parsed,
            scope,
            top_k=self.settings.search_existing_top_k,
        )
        existing_for_llm = [{"id": row.id, "text": row.content} for row in neighbors]

        # Phase 2: LLM extraction (single call)
        extracted = await self.extractor.extract(
            normalized,
            existing_memories=existing_for_llm,
            last_k_messages=last_messages,
            custom_instructions=prompt,
            summary=profile_summary or None,
        )
        if not extracted:
            await self.messages.save_messages(
                normalized,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                session_scope=session_scope,
                limit=self.settings.last_k_messages,
            )
            return AddResult(memories=[], skipped_duplicates=0, extracted=0)

        # Phase 3: Batch embed extracted texts
        existing_hashes = await self.store.existing_hashes(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
        )
        for row in neighbors:
            if row.content_hash:
                existing_hashes.add(row.content_hash)

        # Phase 4 + Phase 5: CPU processing + hash dedup
        unique_items, skipped = drop_duplicate_texts(extracted, existing_hashes)
        if not unique_items:
            await self.messages.save_messages(
                normalized,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                session_scope=session_scope,
                limit=self.settings.last_k_messages,
            )
            logger.info("No new memories after extraction/dedup")
            return AddResult(memories=[], skipped_duplicates=skipped, extracted=len(extracted))

        # Phase 6: Batch persist (vector store + history)
        stored = await self._persist_texts(
            unique_items,
            scope,
            extra,
            memory_type=MemoryType.SEMANTIC.value,
        )

        # Phase 7: Batch entity linking
        if self.settings.enable_entity_store:
            await self.linker.link_memories(stored, scope)

        await self.messages.save_messages(
            normalized,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            session_scope=session_scope,
            limit=self.settings.last_k_messages,
        )
        memories = [stored_to_item(row, event="ADD") for row in stored]
        episode, beliefs_applied = await self._evolve_layers(normalized, stored, scope, extra)
        logger.info("Stored %s memories for a user", len(memories))
        return AddResult(
            memories=memories,
            skipped_duplicates=skipped,
            extracted=len(extracted),
            episode=episode,
            beliefs_applied=beliefs_applied,
        )

    async def _add_raw_messages(
        self,
        messages: list[dict],
        scope: MemoryScope,
        extra: dict[str, Any],
    ) -> AddResult:
        items: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") == "system":
                continue
            text = str(message.get("content") or "")
            if not text:
                continue
            meta = dict(extra)
            meta["role"] = message["role"]
            if message.get("name"):
                meta["actor_id"] = message["name"]
            items.append({"text": text, "content_hash": memory_content_hash(text), "metadata": meta})
        if not items:
            return AddResult(memories=[], skipped_duplicates=0, extracted=0)
        stored = await self._persist_texts(items, scope, extra, memory_type=MemoryType.SEMANTIC.value)
        if self.settings.enable_entity_store:
            await self.linker.link_memories(stored, scope)
        await self.messages.save_messages(
            messages,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            session_scope=build_session_scope(scope),
            limit=self.settings.last_k_messages,
        )
        episode, beliefs_applied = await self._evolve_layers(messages, stored, scope, extra)
        return AddResult(
            memories=[stored_to_item(row, event="ADD") for row in stored],
            extracted=len(stored),
            episode=episode,
            beliefs_applied=beliefs_applied,
        )

    async def _create_procedural_memory(
        self,
        messages: list[dict],
        scope: MemoryScope,
        extra: dict[str, Any],
        *,
        prompt: str | None = None,
    ) -> AddResult:
        logger.info("Creating procedural memory")
        parsed_messages = [
            {"role": "system", "content": prompt or PROCEDURAL_MEMORY_SYSTEM_PROMPT},
            *messages,
            {"role": "user", "content": "Create procedural memory of the above conversation."},
        ]
        try:
            procedural_memory = await self.llm.complete(parsed_messages)
            procedural_memory = remove_code_blocks(procedural_memory)
        except LLMError:
            raise
        except Exception as exc:
            logger.error("Error generating procedural memory summary")
            raise LLMError(f"Error generating procedural memory summary: {exc}") from exc
        if not procedural_memory:
            raise LLMError("The LLM returned no content for the procedural memory summary.")
        items = [
            {
                "text": procedural_memory,
                "content_hash": memory_content_hash(procedural_memory),
                "metadata": extra,
            }
        ]
        stored = await self._persist_texts(
            items,
            scope,
            extra,
            memory_type=MemoryType.PROCEDURAL.value,
        )
        if self.settings.enable_entity_store:
            await self.linker.link_memories(stored, scope)
        return AddResult(
            memories=[stored_to_item(row, event="ADD") for row in stored],
            extracted=1,
        )

    async def _persist_texts(
        self,
        items: list[dict[str, Any]],
        scope: MemoryScope,
        extra: dict[str, Any],
        *,
        memory_type: str,
    ) -> list[StoredMemory]:
        texts = [item["text"] for item in items]
        vectors = await self.embedding.embed(texts)
        now = datetime.now(timezone.utc)
        stored: list[StoredMemory] = []
        for item, vector in zip(items, vectors):
            meta = dict(item.get("metadata") or extra)
            meta["observed_at"] = now.isoformat()
            if meta.get("occurred_at") is not None:
                meta["occurred_at"] = _absolute_observation_time(meta["occurred_at"])
            if item.get("attributed_to"):
                meta["attributed_to"] = item["attributed_to"]
            if item.get("linked_memory_ids"):
                meta["linked_memory_ids"] = item["linked_memory_ids"]
            meta["memory_type"] = memory_type
            stored.append(
                StoredMemory(
                    id=str(uuid.uuid4()),
                    content=item["text"],
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    vehicle_id=scope.vehicle_id,
                    occupant_id=scope.occupant_id or "primary",
                    session_id=scope.session_id,
                    scene=scope.scene,
                    source=scope.source or "conversation",
                    memory_type=memory_type,
                    content_hash=item.get("content_hash") or memory_content_hash(item["text"]),
                    text_lemmatized=lemmatize_for_bm25(item["text"]),
                    embedding=vector,
                    metadata=meta,
                    created_at=now,
                    updated_at=now,
                    embedding_model=self.embedding_model,
                    embedding_dims=self.embedding_dims,
                )
            )
        await self.store.insert(stored)
        self._record_history(
            [
                {
                    "memory_id": row.id,
                    "old_memory": None,
                    "new_memory": row.content,
                    "event": "ADD",
                    "created_at": now.isoformat(),
                    "is_deleted": 0,
                }
                for row in stored
            ]
        )
        return stored

    async def _evolve_layers(
        self,
        messages: list[dict],
        stored: list[StoredMemory],
        scope: MemoryScope,
        extra: dict[str, Any],
    ) -> tuple[Any, int]:
        if not stored:
            return None, 0
        occurred_at = extra.get("occurred_at") if isinstance(extra.get("occurred_at"), str) else None
        episode_item = None
        if self.settings.enable_episodes:
            try:
                episode_row = await self._upsert_episode(messages, stored, scope, extra, occurred_at)
                if episode_row is not None:
                    episode_item = stored_to_item(episode_row, event="ADD")
            except Exception:
                logger.warning("Episode update failed; L1 facts were still stored")
        beliefs_applied = 0
        if self.settings.enable_profile:
            try:
                cluster = list(stored)
                episode_id = episode_item.id if episode_item is not None else None
                neighbor_cap = self.settings.profile_neighbor_top_k
                if stored[0].embedding:
                    neighbors = await self.store.search(
                        stored[0].embedding,
                        tenant_id=scope.tenant_id,
                        user_id=scope.user_id,
                        top_k=neighbor_cap,
                    )
                    seen = {row.id for row in cluster}
                    for row in neighbors:
                        if row.memory_type == MemoryType.EPISODIC.value:
                            continue
                        if row.id in seen:
                            continue
                        cluster.append(row)
                        seen.add(row.id)
                beliefs_applied = await self.distiller.distill(
                    scope=scope,
                    cluster=cluster,
                    episode_id=episode_id,
                )
            except Exception:
                logger.warning("Profile distillation failed; L1 facts were still stored")
        return episode_item, beliefs_applied

    async def _upsert_episode(
        self,
        messages: list[dict],
        stored: list[StoredMemory],
        scope: MemoryScope,
        extra: dict[str, Any],
        occurred_at: str | None,
    ) -> StoredMemory | None:
        event_start, event_end = _episode_time_bounds(stored, occurred_at)
        active = await self.store.get_active_episode(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            occupant_id=scope.occupant_id or "primary",
        )
        similarity = episode_similarity(active, stored)
        judgment = await self.episode_builder.judge(
            messages=messages,
            new_facts=stored,
            active=active,
            similarity=similarity,
            occurred_at=event_end,
        )
        if judgment is None:
            return None
        source_ids = [row.id for row in stored]
        if judgment.continues and active is not None:
            prev_ids = list((active.metadata or {}).get("source_memory_ids") or [])
            source_ids = list(dict.fromkeys([*prev_ids, *source_ids]))
            meta = dict(active.metadata or {})
            meta["episode_status"] = "active"
            meta["source_memory_ids"] = source_ids
            meta["confidence"] = judgment.confidence
            meta["occurred_end"] = event_end
            vectors = await self.embedding.embed([judgment.summary])
            active.content = judgment.summary
            active.content_hash = memory_content_hash("episodic:" + active.id + ":" + judgment.summary)
            active.text_lemmatized = lemmatize_for_bm25(judgment.summary)
            active.embedding = vectors[0]
            active.metadata = meta
            active.updated_at = datetime.now(timezone.utc)
            await self.store.update(active)
            return active

        if active is not None:
            closed = dict(active.metadata or {})
            closed["episode_status"] = "complete"
            active.metadata = closed
            if active.embedding is None:
                vectors = await self.embedding.embed([active.content])
                active.embedding = vectors[0]
            await self.store.update(active)

        meta = dict(extra)
        meta["episode_status"] = "active"
        meta["source_memory_ids"] = source_ids
        meta["confidence"] = judgment.confidence
        meta["occurred_at"] = event_start
        if event_end != event_start:
            meta["occurred_end"] = event_end
        created = await self._persist_texts(
            [
                {
                    "text": judgment.summary,
                    "content_hash": memory_content_hash("episodic:" + judgment.summary),
                    "metadata": meta,
                }
            ],
            scope,
            extra,
            memory_type=MemoryType.EPISODIC.value,
        )
        return created[0] if created else None

    def _record_history(self, records: list[dict[str, Any]]) -> None:
        writer = getattr(self.db, "batch_add_history", None)
        if callable(writer):
            writer(records)

    async def search(
        self,
        query: str,
        user_id: str,
        filters: dict | None = None,
        top_k: int = 5,
        *,
        tenant_id: str = "default",
        threshold: float | None = None,
    ) -> list[dict]:
        if not query or not str(query).strip():
            raise ValidationError("query must be a non-empty string", error_code="VAL_QUERY")
        scope = MemoryScope(
            tenant_id=(filters or {}).get("tenant_id") or tenant_id,
            user_id=user_id,
        )
        search_filters = SearchFilters(
            vehicle_id=(filters or {}).get("vehicle_id"),
            occupant_id=(filters or {}).get("occupant_id"),
            session_id=(filters or {}).get("session_id"),
            scene=(filters or {}).get("scene"),
            source=(filters or {}).get("source"),
            memory_type=(filters or {}).get("memory_type"),
        )
        result = await self.search_result(
            query,
            scope,
            filters=search_filters,
            top_k=top_k,
            threshold=threshold,
        )
        return [item.to_public_dict() for item in result.memories]

    async def search_result(
        self,
        query: str,
        scope: MemoryScope,
        *,
        filters: SearchFilters | dict | None = None,
        top_k: int = 5,
        threshold: float | None = None,
    ) -> SearchResult:
        fetch_k = max(top_k, self.settings.rerank_candidate_limit) if self.settings.enable_rerank else top_k
        hits = await self.retriever.search(
            query,
            scope,
            top_k=fetch_k,
            filters=filters,
            threshold=threshold,
            explain=False,
        )
        hits = await self._expand_episode_evidence(hits, scope, limit=max(fetch_k * 2, top_k))
        profile = ProfileView()
        if self.settings.enable_profile:
            try:
                profile = await self.distiller.profile_view(scope)
            except Exception:
                logger.warning("Failed to load profile for search")
        if self.settings.enable_rerank:
            try:
                hits = await self.reranker.select(query, hits, profile=profile, top_k=top_k)
            except Exception:
                logger.warning("Rerank failed; returning vector ranking")
                hits = hits[:top_k]
        else:
            hits = hits[:top_k]
        return SearchResult(
            memories=[stored_to_item(row) for row in hits],
            query=query,
            top_k=top_k,
            profile=profile,
        )

    async def _expand_episode_evidence(
        self,
        hits: list[StoredMemory],
        scope: MemoryScope,
        *,
        limit: int,
    ) -> list[StoredMemory]:
        """Add L1 evidence referenced by recalled L2 episodes to the rerank pool."""
        expanded = list(hits)
        seen = {row.id for row in hits}
        for episode in hits:
            if episode.memory_type != MemoryType.EPISODIC.value:
                continue
            source_ids = (episode.metadata or {}).get("source_memory_ids") or []
            if not isinstance(source_ids, list):
                continue
            for memory_id in source_ids:
                if len(expanded) >= limit or str(memory_id) in seen:
                    break
                row = await self.store.get(
                    str(memory_id),
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                )
                if row is None or row.memory_type == MemoryType.EPISODIC.value:
                    continue
                row.metadata = dict(row.metadata or {})
                row.metadata["expanded_from_episode_id"] = episode.id
                expanded.append(row)
                seen.add(row.id)
        return expanded

    async def get_profile(self, user_id: str, *, tenant_id: str = "default") -> ProfileView:
        scope = MemoryScope(tenant_id=tenant_id, user_id=user_id)
        return await self.distiller.profile_view(scope)

    async def history(self, memory_id: str) -> list[dict[str, Any]]:
        reader = getattr(self.db, "get_history", None)
        if not callable(reader):
            return []
        return await reader(memory_id)

    async def history_result(
        self,
        memory_id: str,
        *,
        scope: MemoryScope | None = None,
    ) -> HistoryResult:
        if scope is not None:
            owned = await self.store.get(
                memory_id,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            if owned is None:
                from desaymem.core.exceptions import MemoryNotFoundError

                raise MemoryNotFoundError(
                    details={
                        "memory_id": memory_id,
                        "tenant_id": scope.tenant_id,
                        "user_id": scope.user_id,
                    }
                )
        return HistoryResult(memory_id=memory_id, events=await self.history(memory_id))

    async def get_all(
        self,
        user_id: str,
        filters: dict | None = None,
        *,
        tenant_id: str = "default",
        limit: int | None = None,
    ) -> list[dict]:
        scope = MemoryScope(
            tenant_id=(filters or {}).get("tenant_id") or tenant_id,
            user_id=user_id,
        )
        search_filters = {
            key: value
            for key, value in (filters or {}).items()
            if key in {"vehicle_id", "occupant_id", "session_id", "scene", "source", "memory_type"} and value
        }
        result = await self.get_all_result(scope, filters=search_filters, limit=limit)
        return [item.to_public_dict() for item in result.memories]

    async def get_all_result(
        self,
        scope: MemoryScope,
        *,
        filters: dict | None = None,
        limit: int | None = None,
    ) -> ListResult:
        rows = await self.store.list(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            filters=filters,
            limit=limit or self.settings.get_all_limit,
        )
        memories = [stored_to_item(row) for row in rows]
        return ListResult(memories=memories, count=len(memories))

    async def delete(self, memory_id: str, user_id: str, *, tenant_id: str = "default") -> bool:
        if not memory_id or not str(memory_id).strip():
            raise ValidationError("memory_id is required", error_code="VAL_MEMORY_ID")
        scope = MemoryScope(tenant_id=tenant_id, user_id=user_id)
        existing = await self.store.get(memory_id, tenant_id=tenant_id, user_id=user_id)
        if self.settings.enable_entity_store:
            await self.linker.unlink_memory(memory_id, scope)
        deleted = await self.store.delete(
            memory_id,
            tenant_id=tenant_id,
            user_id=user_id,
        )
        if deleted and existing:
            self._record_history(
                [
                    {
                        "memory_id": memory_id,
                        "old_memory": existing.content,
                        "new_memory": None,
                        "event": "DELETE",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "is_deleted": 1,
                    }
                ]
            )
        return deleted

    async def delete_result(
        self,
        memory_id: str,
        scope: MemoryScope,
    ) -> DeleteResult:
        deleted = await self.delete(memory_id, scope.user_id, tenant_id=scope.tenant_id)
        return DeleteResult(deleted=deleted, memory_id=memory_id)

    async def delete_all(self, user_id: str, *, tenant_id: str = "default") -> int:
        if self.settings.enable_entity_store:
            await self.entities.delete_by_user(tenant_id=tenant_id, user_id=user_id)
        await self.messages.delete_by_user(tenant_id=tenant_id, user_id=user_id)
        await self.profiles.delete_by_user(tenant_id=tenant_id, user_id=user_id)
        return await self.store.delete_by_user(tenant_id=tenant_id, user_id=user_id)

    async def delete_all_result(self, scope: MemoryScope) -> DeleteAllResult:
        count = await self.delete_all(scope.user_id, tenant_id=scope.tenant_id)
        return DeleteAllResult(
            deleted_count=count,
            user_id=scope.user_id,
            tenant_id=scope.tenant_id,
        )

    async def healthcheck(self) -> bool:
        return await self.store.healthcheck()

    async def prepare(self) -> None:
        await self.store.check_schema(self.embedding_dims)

    async def close(self) -> None:
        closer = getattr(self.db, "close", None)
        if callable(closer):
            closer()
        await self.store.close()
