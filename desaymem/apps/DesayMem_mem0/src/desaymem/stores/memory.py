"""In-memory vector store used by unit tests and offline demos."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from desaymem.core.exceptions import EmbeddingError
from desaymem.retrieval.scoring import cosine_similarity, distance_to_score
from desaymem.stores.base import StoredEntity, StoredMemory

_OPTIONAL_FILTERS = ("vehicle_id", "occupant_id", "session_id", "scene", "source", "memory_type")


class InMemoryVectorStore:
    def __init__(self, embedding_dims: int = 8) -> None:
        self.embedding_dims = embedding_dims
        self._items: list[StoredMemory] = []

    def _validate_vector(self, vector: list[float]) -> None:
        if len(vector) != self.embedding_dims:
            raise EmbeddingError(
                f"Embedding dimension mismatch: expected {self.embedding_dims}, got {len(vector)}",
                error_code="EMBED_006",
                details={"expected": self.embedding_dims, "actual": len(vector)},
            )

    def _matches(
        self,
        item: StoredMemory,
        *,
        tenant_id: str,
        user_id: str,
        filters: dict[str, Any] | None,
    ) -> bool:
        if item.tenant_id != tenant_id or item.user_id != user_id:
            return False
        if not filters:
            return True
        for key in _OPTIONAL_FILTERS:
            expected = filters.get(key)
            if expected in (None, ""):
                continue
            if getattr(item, key) != expected:
                return False
        return True

    async def insert(self, items: list[StoredMemory]) -> None:
        existing = {(item.tenant_id, item.user_id, item.content_hash) for item in self._items}
        for item in items:
            if item.embedding is None:
                raise EmbeddingError("Cannot insert memory without embedding")
            self._validate_vector(item.embedding)
            key = (item.tenant_id, item.user_id, item.content_hash)
            if key in existing:
                continue
            stored = deepcopy(item)
            if stored.created_at is None:
                stored.created_at = datetime.now(timezone.utc)
            if stored.updated_at is None:
                stored.updated_at = stored.created_at
            if not stored.memory_type:
                stored.memory_type = "semantic_memory"
            self._items.append(stored)
            existing.add(key)

    async def update(self, item: StoredMemory) -> bool:
        for index, existing_item in enumerate(self._items):
            if (
                existing_item.id == item.id
                and existing_item.tenant_id == item.tenant_id
                and existing_item.user_id == item.user_id
            ):
                stored = deepcopy(item)
                stored.updated_at = datetime.now(timezone.utc)
                if stored.embedding is not None:
                    self._validate_vector(stored.embedding)
                self._items[index] = stored
                return True
        return False

    async def get_active_episode(
        self,
        *,
        tenant_id: str,
        user_id: str,
        occupant_id: str = "primary",
    ) -> StoredMemory | None:
        occupant = occupant_id or "primary"
        matches: list[StoredMemory] = []
        for item in self._items:
            if item.tenant_id != tenant_id or item.user_id != user_id:
                continue
            if item.memory_type != "episodic_memory":
                continue
            if (item.occupant_id or "primary") != occupant:
                continue
            status = (item.metadata or {}).get("episode_status") or "active"
            if status != "active":
                continue
            matches.append(item)
        if not matches:
            return None
        matches.sort(key=lambda row: row.updated_at or row.created_at or datetime.min, reverse=True)
        return deepcopy(matches[0])

    async def search(
        self,
        vector: list[float],
        *,
        tenant_id: str,
        user_id: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[StoredMemory]:
        self._validate_vector(vector)
        scored: list[StoredMemory] = []
        for item in self._items:
            if not self._matches(item, tenant_id=tenant_id, user_id=user_id, filters=filters):
                continue
            if not item.embedding:
                continue
            score = cosine_similarity(vector, item.embedding)
            cloned = deepcopy(item)
            cloned.score = distance_to_score(1.0 - score)
            scored.append(cloned)
        scored.sort(key=lambda row: row.score or 0.0, reverse=True)
        return scored[:top_k]

    async def keyword_search(
        self,
        query: str,
        *,
        tenant_id: str,
        user_id: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[StoredMemory] | None:
        terms = [term for term in (query or "").lower().split() if term]
        if not terms:
            return []
        scored: list[StoredMemory] = []
        for item in self._items:
            if not self._matches(item, tenant_id=tenant_id, user_id=user_id, filters=filters):
                continue
            haystack = (item.text_lemmatized or item.content).lower()
            hits = sum(1 for term in terms if term in haystack)
            if hits <= 0:
                continue
            cloned = deepcopy(item)
            cloned.score = float(hits)
            scored.append(cloned)
        scored.sort(key=lambda row: row.score or 0.0, reverse=True)
        return scored[:top_k]

    async def list(
        self,
        *,
        tenant_id: str,
        user_id: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[StoredMemory]:
        matched = [
            deepcopy(item)
            for item in self._items
            if self._matches(item, tenant_id=tenant_id, user_id=user_id, filters=filters)
        ]
        matched.sort(key=lambda item: item.created_at or datetime.min, reverse=True)
        return matched[:limit]

    async def get(
        self,
        memory_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> StoredMemory | None:
        for item in self._items:
            if item.id == memory_id and item.tenant_id == tenant_id and item.user_id == user_id:
                return deepcopy(item)
        return None

    async def delete(
        self,
        memory_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> bool:
        for index, item in enumerate(self._items):
            if item.id == memory_id and item.tenant_id == tenant_id and item.user_id == user_id:
                del self._items[index]
                return True
        return False

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int:
        before = len(self._items)
        self._items = [
            item for item in self._items if not (item.tenant_id == tenant_id and item.user_id == user_id)
        ]
        return before - len(self._items)

    async def existing_hashes(self, *, tenant_id: str, user_id: str) -> set[str]:
        return {
            item.content_hash
            for item in self._items
            if item.tenant_id == tenant_id and item.user_id == user_id and item.content_hash
        }

    async def healthcheck(self) -> bool:
        return True

    async def check_schema(self, expected_dims: int) -> None:
        if expected_dims != self.embedding_dims:
            from desaymem.core.exceptions import ConfigurationError

            raise ConfigurationError(
                f"In-memory store dims {self.embedding_dims} != configured {expected_dims}",
                error_code="CFG_DIMS",
            )

    async def close(self) -> None:
        return None


class InMemoryMessageStore:
    """Last-k session messages. Mirrors mem0 SQLite `messages` table in memory."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._seq = 0

    async def save_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        tenant_id: str,
        user_id: str,
        session_scope: str,
        limit: int = 10,
    ) -> None:
        if not messages:
            return
        now = datetime.now(timezone.utc)
        for message in messages:
            self._seq += 1
            self._rows.append(
                {
                    "id": str(uuid4()),
                    "seq": self._seq,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "session_scope": session_scope,
                    "role": message.get("role"),
                    "content": message.get("content"),
                    "name": message.get("name"),
                    "created_at": now,
                }
            )
        scoped = [row for row in self._rows if row["session_scope"] == session_scope]
        scoped.sort(key=lambda row: (row["created_at"], row.get("seq", 0)), reverse=True)
        keep_ids = {row["id"] for row in scoped[:limit]}
        self._rows = [
            row
            for row in self._rows
            if row["session_scope"] != session_scope or row["id"] in keep_ids
        ]

    async def get_last_messages(
        self,
        session_scope: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        scoped = [row for row in self._rows if row["session_scope"] == session_scope]
        scoped.sort(key=lambda row: (row["created_at"], row.get("seq", 0)), reverse=True)
        latest = list(reversed(scoped[:limit]))
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "name": row.get("name"),
                "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            }
            for row in latest
        ]

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int:
        before = len(self._rows)
        self._rows = [
            row
            for row in self._rows
            if not (row["tenant_id"] == tenant_id and row["user_id"] == user_id)
        ]
        return before - len(self._rows)


class InMemoryEntityStore:
    """Entity vector store used by tests. Same isolation as memory_items."""

    def __init__(self, embedding_dims: int = 8) -> None:
        self.embedding_dims = embedding_dims
        self._items: list[StoredEntity] = []

    def _validate(self, vector: list[float]) -> None:
        if len(vector) != self.embedding_dims:
            raise EmbeddingError(
                f"Embedding dimension mismatch: expected {self.embedding_dims}, got {len(vector)}",
                error_code="EMBED_006",
                details={"expected": self.embedding_dims, "actual": len(vector)},
            )

    async def insert(self, items: list[StoredEntity]) -> None:
        existing = {(item.tenant_id, item.user_id, item.normalized_text) for item in self._items}
        for item in items:
            if item.embedding is None:
                raise EmbeddingError("Cannot insert entity without embedding")
            self._validate(item.embedding)
            key = (item.tenant_id, item.user_id, item.normalized_text)
            if key in existing:
                continue
            stored = deepcopy(item)
            if stored.created_at is None:
                stored.created_at = datetime.now(timezone.utc)
            if stored.updated_at is None:
                stored.updated_at = stored.created_at
            self._items.append(stored)
            existing.add(key)

    async def search(
        self,
        vector: list[float],
        *,
        tenant_id: str,
        user_id: str,
        top_k: int = 5,
    ) -> list[StoredEntity]:
        self._validate(vector)
        scored: list[StoredEntity] = []
        for item in self._items:
            if item.tenant_id != tenant_id or item.user_id != user_id or not item.embedding:
                continue
            cloned = deepcopy(item)
            cloned.score = cosine_similarity(vector, item.embedding)
            scored.append(cloned)
        scored.sort(key=lambda row: row.score or 0.0, reverse=True)
        return scored[:top_k]

    async def list(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 10000,
    ) -> list[StoredEntity]:
        matched = [
            deepcopy(item)
            for item in self._items
            if item.tenant_id == tenant_id and item.user_id == user_id
        ]
        return matched[:limit]

    async def get_by_normalized(
        self,
        *,
        tenant_id: str,
        user_id: str,
        normalized_text: str,
    ) -> StoredEntity | None:
        for item in self._items:
            if (
                item.tenant_id == tenant_id
                and item.user_id == user_id
                and item.normalized_text == normalized_text
            ):
                return deepcopy(item)
        return None

    async def update_links(
        self,
        entity_id: str,
        linked_memory_ids: list[str],
        *,
        tenant_id: str,
        user_id: str,
    ) -> None:
        for item in self._items:
            if item.id == entity_id and item.tenant_id == tenant_id and item.user_id == user_id:
                item.linked_memory_ids = list(linked_memory_ids)
                item.updated_at = datetime.now(timezone.utc)
                return

    async def delete(
        self,
        entity_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> bool:
        for index, item in enumerate(self._items):
            if item.id == entity_id and item.tenant_id == tenant_id and item.user_id == user_id:
                del self._items[index]
                return True
        return False

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int:
        before = len(self._items)
        self._items = [
            item for item in self._items if not (item.tenant_id == tenant_id and item.user_id == user_id)
        ]
        return before - len(self._items)
