"""Vector store protocol.

Simplified from mem0.vector_stores.base.VectorStoreBase. DesayMem is async
and uses first-class tenant/user columns instead of JSONB payload filters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class StoredMemory:
    id: str
    content: str
    tenant_id: str
    user_id: str
    vehicle_id: str = ""
    occupant_id: str = "primary"
    session_id: str = ""
    scene: str = ""
    source: str = "conversation"
    memory_type: str = "semantic_memory"
    content_hash: str = ""
    text_lemmatized: str = ""
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    embedding_model: str | None = None
    embedding_dims: int | None = None


@dataclass
class StoredEntity:
    id: str
    tenant_id: str
    user_id: str
    entity_text: str
    entity_type: str
    normalized_text: str
    embedding: list[float] | None = None
    linked_memory_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class StoredBelief:
    id: str
    tenant_id: str
    user_id: str
    occupant_id: str = "primary"
    subject: str = "User"
    attribute: str = ""
    value: str = ""
    conditions: dict[str, Any] = field(default_factory=dict)
    stability: str = "episode"
    status: str = "active"
    confidence: float = 0.5
    support_count: int = 1
    evidence_memory_ids: list[str] = field(default_factory=list)
    evidence_episode_ids: list[str] = field(default_factory=list)
    attribute_embedding: list[float] | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    score: float | None = None


@runtime_checkable
class VectorStore(Protocol):
    async def insert(self, items: list[StoredMemory]) -> None: ...

    async def update(self, item: StoredMemory) -> bool: ...

    async def search(
        self,
        vector: list[float],
        *,
        tenant_id: str,
        user_id: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[StoredMemory]: ...

    async def list(
        self,
        *,
        tenant_id: str,
        user_id: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[StoredMemory]: ...

    async def get(
        self,
        memory_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> StoredMemory | None: ...

    async def get_active_episode(
        self,
        *,
        tenant_id: str,
        user_id: str,
        occupant_id: str = "primary",
    ) -> StoredMemory | None: ...

    async def delete(
        self,
        memory_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> bool: ...

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int: ...

    async def existing_hashes(self, *, tenant_id: str, user_id: str) -> set[str]: ...

    async def keyword_search(
        self,
        query: str,
        *,
        tenant_id: str,
        user_id: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[StoredMemory] | None: ...

    async def healthcheck(self) -> bool: ...

    async def check_schema(self, expected_dims: int) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class ProfileStore(Protocol):
    async def list_active(
        self,
        *,
        tenant_id: str,
        user_id: str,
        occupant_id: str | None = None,
    ) -> list[StoredBelief]: ...

    async def search_similar(
        self,
        vector: list[float],
        *,
        tenant_id: str,
        user_id: str,
        occupant_id: str = "primary",
        top_k: int = 5,
        active_only: bool = True,
    ) -> list[StoredBelief]: ...

    async def insert(self, item: StoredBelief) -> None: ...

    async def update(self, item: StoredBelief) -> bool: ...

    async def get_snapshot(self, *, tenant_id: str, user_id: str) -> str: ...

    async def upsert_snapshot(self, *, tenant_id: str, user_id: str, narrative: str) -> None: ...

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int: ...


@runtime_checkable
class SessionMessageStore(Protocol):
    async def save_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        tenant_id: str,
        user_id: str,
        session_scope: str,
        limit: int = 10,
    ) -> None: ...

    async def get_last_messages(
        self,
        session_scope: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]: ...

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int: ...


@runtime_checkable
class EntityStore(Protocol):
    async def insert(self, items: list[StoredEntity]) -> None: ...

    async def search(
        self,
        vector: list[float],
        *,
        tenant_id: str,
        user_id: str,
        top_k: int = 5,
    ) -> list[StoredEntity]: ...

    async def list(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 10000,
    ) -> list[StoredEntity]: ...

    async def get_by_normalized(
        self,
        *,
        tenant_id: str,
        user_id: str,
        normalized_text: str,
    ) -> StoredEntity | None: ...

    async def update_links(
        self,
        entity_id: str,
        linked_memory_ids: list[str],
        *,
        tenant_id: str,
        user_id: str,
    ) -> None: ...

    async def delete(
        self,
        entity_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> bool: ...

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int: ...
