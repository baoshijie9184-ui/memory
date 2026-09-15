"""Entity store linking, migrated from mem0.memory.main Phase 7 / entity boost.

On add: extract entities from new memories, upsert into the entity store,
and attach `linked_memory_ids`.

On search: extract entities from the query and boost memories linked to
matching entities (Mem0 `_compute_entity_boosts`).

On delete: strip the memory id; drop the entity if no links remain.
"""

from __future__ import annotations

import uuid

from desaymem.core.logging import get_logger
from desaymem.core.models import MemoryScope
from desaymem.extraction.entities import extract_entities, extract_entities_batch, normalize_entity_text
from desaymem.providers.embedding.base import EmbeddingProvider
from desaymem.retrieval.scoring import ENTITY_BOOST_WEIGHT
from desaymem.stores.base import EntityStore, StoredEntity, StoredMemory

logger = get_logger(__name__)


class EntityLinker:
    def __init__(
        self,
        store: EntityStore,
        embedding: EmbeddingProvider,
        *,
        match_threshold: float = 0.95,
        boost_min_similarity: float = 0.5,
    ) -> None:
        self.store = store
        self.embedding = embedding
        self.match_threshold = match_threshold
        self.boost_min_similarity = boost_min_similarity

    async def link_memories(self, items: list[StoredMemory], scope: MemoryScope) -> None:
        if not items:
            return
        try:
            texts = [item.content for item in items]
            batched = extract_entities_batch(texts)
            grouped: dict[str, tuple[str, str, set[str]]] = {}
            for item, entities in zip(items, batched):
                for entity_type, entity_text in entities:
                    key = normalize_entity_text(entity_text)
                    if not key:
                        continue
                    if key in grouped:
                        grouped[key][2].add(item.id)
                    else:
                        grouped[key] = (entity_type, entity_text, {item.id})
            if not grouped:
                return

            keys = list(grouped.keys())
            entity_texts = [grouped[key][1] for key in keys]
            vectors = await self.embedding.embed(entity_texts)
            for key, vector in zip(keys, vectors):
                entity_type, entity_text, memory_ids = grouped[key]
                await self._upsert(
                    scope,
                    entity_type=entity_type,
                    entity_text=entity_text,
                    normalized=key,
                    vector=vector,
                    memory_ids=memory_ids,
                )
        except Exception as exc:
            logger.warning("Batch entity linking failed: %s", exc)

    async def unlink_memory(self, memory_id: str, scope: MemoryScope) -> None:
        try:
            rows = await self.store.list(tenant_id=scope.tenant_id, user_id=scope.user_id)
            for row in rows:
                if memory_id not in row.linked_memory_ids:
                    continue
                remaining = [item for item in row.linked_memory_ids if item != memory_id]
                if remaining:
                    await self.store.update_links(
                        row.id,
                        remaining,
                        tenant_id=scope.tenant_id,
                        user_id=scope.user_id,
                    )
                else:
                    await self.store.delete(
                        row.id,
                        tenant_id=scope.tenant_id,
                        user_id=scope.user_id,
                    )
        except Exception as exc:
            logger.warning("Entity unlink failed: %s", exc)

    async def boosts_for_query(self, query: str, scope: MemoryScope) -> dict[str, float]:
        query_entities = extract_entities(query)
        if not query_entities:
            return {}
        seen: set[str] = set()
        deduped: list[tuple[str, str]] = []
        for entity_type, entity_text in query_entities[:8]:
            key = normalize_entity_text(entity_text)
            if key and key not in seen:
                seen.add(key)
                deduped.append((entity_type, entity_text))
        if not deduped:
            return {}

        boosts: dict[str, float] = {}
        try:
            texts = [text for _, text in deduped]
            vectors = await self.embedding.embed(texts)
            for text, vector in zip(texts, vectors):
                matches = await self.store.search(
                    vector,
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    top_k=500,
                )
                for match in matches:
                    similarity = match.score or 0.0
                    if similarity < self.boost_min_similarity:
                        continue
                    linked = match.linked_memory_ids or []
                    num_linked = max(len(linked), 1)
                    memory_count_weight = 1.0 / (1.0 + 0.001 * ((num_linked - 1) ** 2))
                    boost = similarity * ENTITY_BOOST_WEIGHT * memory_count_weight
                    for memory_id in linked:
                        if memory_id:
                            key = str(memory_id)
                            boosts[key] = max(boosts.get(key, 0.0), boost)
        except Exception as exc:
            logger.warning("Entity boost computation failed: %s", exc)
        return boosts

    async def _upsert(
        self,
        scope: MemoryScope,
        *,
        entity_type: str,
        entity_text: str,
        normalized: str,
        vector: list[float],
        memory_ids: set[str],
    ) -> None:
        exact = await self.store.get_by_normalized(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            normalized_text=normalized,
        )
        match = exact
        if match is None:
            hits = await self.store.search(
                vector,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                top_k=1,
            )
            if hits and (hits[0].score or 0.0) >= self.match_threshold:
                match = hits[0]
        if match is not None:
            linked = set(match.linked_memory_ids or [])
            linked |= memory_ids
            await self.store.update_links(
                match.id,
                sorted(linked),
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
            )
            return
        await self.store.insert(
            [
                StoredEntity(
                    id=str(uuid.uuid4()),
                    tenant_id=scope.tenant_id,
                    user_id=scope.user_id,
                    entity_text=entity_text,
                    entity_type=entity_type,
                    normalized_text=normalized,
                    embedding=vector,
                    linked_memory_ids=sorted(memory_ids),
                )
            ]
        )
