"""L3 profile store: structured current beliefs, not a vector-search collection.

Attribute embeddings exist only so Python can merge semantically equivalent
keys (same subject/attribute/conditions). User queries never ANN-search this
table as the primary retrieval path.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from desaymem.core.exceptions import DatabaseError, EmbeddingError
from desaymem.retrieval.scoring import cosine_similarity
from desaymem.stores.base import StoredBelief
from desaymem.stores.pgvector import PgVectorStore


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _row_to_belief(row: dict[str, Any], score: float | None = None) -> StoredBelief:
    embedding = row.get("attribute_embedding")
    if embedding is not None:
        embedding = list(embedding)
    return StoredBelief(
        id=str(row["id"]),
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        occupant_id=row.get("occupant_id") or "primary",
        subject=row.get("subject") or "User",
        attribute=row.get("attribute") or "",
        value=row.get("value") or "",
        conditions=_as_dict(row.get("conditions")),
        stability=row.get("stability") or "episode",
        status=row.get("status") or "active",
        confidence=float(row.get("confidence") or 0.5),
        support_count=int(row.get("support_count") or 1),
        evidence_memory_ids=_as_list(row.get("evidence_memory_ids")),
        evidence_episode_ids=_as_list(row.get("evidence_episode_ids")),
        attribute_embedding=embedding,
        valid_from=row.get("valid_from"),
        valid_to=row.get("valid_to"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        score=score,
    )


class InMemoryProfileStore:
    def __init__(self, embedding_dims: int = 8) -> None:
        self.embedding_dims = embedding_dims
        self._items: list[StoredBelief] = []
        self._snapshots: dict[tuple[str, str], str] = {}

    def _validate(self, vector: list[float]) -> None:
        if len(vector) != self.embedding_dims:
            raise EmbeddingError(
                f"Embedding dimension mismatch: expected {self.embedding_dims}, got {len(vector)}",
                error_code="EMBED_006",
                details={"expected": self.embedding_dims, "actual": len(vector)},
            )

    async def list_active(
        self,
        *,
        tenant_id: str,
        user_id: str,
        occupant_id: str | None = None,
    ) -> list[StoredBelief]:
        out: list[StoredBelief] = []
        for item in self._items:
            if item.tenant_id != tenant_id or item.user_id != user_id:
                continue
            if item.status != "active":
                continue
            if occupant_id and (item.occupant_id or "primary") != occupant_id:
                continue
            out.append(deepcopy(item))
        return out

    async def search_similar(
        self,
        vector: list[float],
        *,
        tenant_id: str,
        user_id: str,
        occupant_id: str = "primary",
        top_k: int = 5,
        active_only: bool = True,
    ) -> list[StoredBelief]:
        self._validate(vector)
        occupant = occupant_id or "primary"
        scored: list[StoredBelief] = []
        for item in self._items:
            if item.tenant_id != tenant_id or item.user_id != user_id:
                continue
            if (item.occupant_id or "primary") != occupant:
                continue
            if active_only and item.status != "active":
                continue
            if not item.attribute_embedding:
                continue
            cloned = deepcopy(item)
            cloned.score = cosine_similarity(vector, item.attribute_embedding)
            scored.append(cloned)
        scored.sort(key=lambda row: row.score or 0.0, reverse=True)
        return scored[:top_k]

    async def insert(self, item: StoredBelief) -> None:
        if item.attribute_embedding is None:
            raise EmbeddingError("Cannot insert belief without embedding")
        self._validate(item.attribute_embedding)
        stored = deepcopy(item)
        if not stored.id:
            stored.id = str(uuid4())
        now = datetime.now(timezone.utc)
        stored.created_at = stored.created_at or now
        stored.updated_at = now
        self._items.append(stored)

    async def update(self, item: StoredBelief) -> bool:
        for index, existing in enumerate(self._items):
            if existing.id == item.id and existing.tenant_id == item.tenant_id and existing.user_id == item.user_id:
                stored = deepcopy(item)
                stored.updated_at = datetime.now(timezone.utc)
                if stored.attribute_embedding is not None:
                    self._validate(stored.attribute_embedding)
                self._items[index] = stored
                return True
        return False

    async def get_snapshot(self, *, tenant_id: str, user_id: str) -> str:
        return self._snapshots.get((tenant_id, user_id), "")

    async def upsert_snapshot(self, *, tenant_id: str, user_id: str, narrative: str) -> None:
        self._snapshots[(tenant_id, user_id)] = narrative or ""

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int:
        before = len(self._items)
        self._items = [
            item for item in self._items if not (item.tenant_id == tenant_id and item.user_id == user_id)
        ]
        self._snapshots.pop((tenant_id, user_id), None)
        return before - len(self._items)


class PgProfileStore:
    def __init__(self, backend: PgVectorStore) -> None:
        self._backend = backend

    def _validate(self, vector: list[float]) -> None:
        if len(vector) != self._backend.embedding_dims:
            raise EmbeddingError(
                f"Embedding dimension mismatch: expected {self._backend.embedding_dims}, got {len(vector)}",
                error_code="EMBED_006",
                details={"expected": self._backend.embedding_dims, "actual": len(vector)},
            )

    async def list_active(
        self,
        *,
        tenant_id: str,
        user_id: str,
        occupant_id: str | None = None,
    ) -> list[StoredBelief]:
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    if occupant_id:
                        await cur.execute(
                            """
                            SELECT id, tenant_id, user_id, occupant_id, subject, attribute, value,
                                   conditions, stability, status, confidence, support_count,
                                   evidence_memory_ids, evidence_episode_ids, attribute_embedding,
                                   valid_from, valid_to, created_at, updated_at
                            FROM profile_beliefs
                            WHERE tenant_id = %s AND user_id = %s AND occupant_id = %s
                              AND status = 'active'
                            ORDER BY updated_at DESC
                            """,
                            (tenant_id, user_id, occupant_id),
                        )
                    else:
                        await cur.execute(
                            """
                            SELECT id, tenant_id, user_id, occupant_id, subject, attribute, value,
                                   conditions, stability, status, confidence, support_count,
                                   evidence_memory_ids, evidence_episode_ids, attribute_embedding,
                                   valid_from, valid_to, created_at, updated_at
                            FROM profile_beliefs
                            WHERE tenant_id = %s AND user_id = %s AND status = 'active'
                            ORDER BY updated_at DESC
                            """,
                            (tenant_id, user_id),
                        )
                    rows = await cur.fetchall()
        except Exception as exc:
            raise DatabaseError("Failed to list profile beliefs") from exc
        return [_row_to_belief(row) for row in rows]

    async def search_similar(
        self,
        vector: list[float],
        *,
        tenant_id: str,
        user_id: str,
        occupant_id: str = "primary",
        top_k: int = 5,
        active_only: bool = True,
    ) -> list[StoredBelief]:
        self._validate(vector)
        await self._backend.open()
        occupant = occupant_id or "primary"
        status_clause = "AND status = 'active'" if active_only else ""
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT id, tenant_id, user_id, occupant_id, subject, attribute, value,
                               conditions, stability, status, confidence, support_count,
                               evidence_memory_ids, evidence_episode_ids, attribute_embedding,
                               valid_from, valid_to, created_at, updated_at,
                               attribute_embedding <=> %s::vector AS distance
                        FROM profile_beliefs
                        WHERE tenant_id = %s AND user_id = %s AND occupant_id = %s
                        {status_clause}
                        ORDER BY distance
                        LIMIT %s
                        """,
                        (vector, tenant_id, user_id, occupant, top_k),
                    )
                    rows = await cur.fetchall()
        except Exception as exc:
            raise DatabaseError("Failed to search profile beliefs") from exc
        results: list[StoredBelief] = []
        for row in rows:
            distance = float(row["distance"])
            results.append(_row_to_belief(row, score=max(0.0, 1.0 - distance)))
        return results

    async def insert(self, item: StoredBelief) -> None:
        if item.attribute_embedding is None:
            raise DatabaseError("Cannot insert belief without embedding")
        self._validate(item.attribute_embedding)
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO profile_beliefs (
                            id, tenant_id, user_id, occupant_id, subject, attribute, value,
                            conditions, stability, status, confidence, support_count,
                            evidence_memory_ids, evidence_episode_ids, attribute_embedding,
                            valid_from, valid_to
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s,
                            %s, %s
                        )
                        """,
                        (
                            item.id,
                            item.tenant_id,
                            item.user_id,
                            item.occupant_id or "primary",
                            item.subject or "User",
                            item.attribute,
                            item.value,
                            Jsonb(item.conditions or {}),
                            item.stability or "episode",
                            item.status or "active",
                            item.confidence,
                            max(1, int(item.support_count or 1)),
                            Jsonb(item.evidence_memory_ids or []),
                            Jsonb(item.evidence_episode_ids or []),
                            item.attribute_embedding,
                            item.valid_from,
                            item.valid_to,
                        ),
                    )
        except Exception as exc:
            raise DatabaseError("Failed to insert profile belief") from exc

    async def update(self, item: StoredBelief) -> bool:
        if item.attribute_embedding is None:
            raise DatabaseError("Cannot update belief without embedding")
        self._validate(item.attribute_embedding)
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        UPDATE profile_beliefs
                        SET subject = %s,
                            attribute = %s,
                            value = %s,
                            conditions = %s,
                            stability = %s,
                            status = %s,
                            confidence = %s,
                            support_count = %s,
                            evidence_memory_ids = %s,
                            evidence_episode_ids = %s,
                            attribute_embedding = %s,
                            valid_from = %s,
                            valid_to = %s,
                            updated_at = NOW()
                        WHERE id = %s AND tenant_id = %s AND user_id = %s
                        RETURNING id
                        """,
                        (
                            item.subject or "User",
                            item.attribute,
                            item.value,
                            Jsonb(item.conditions or {}),
                            item.stability or "episode",
                            item.status or "active",
                            item.confidence,
                            max(1, int(item.support_count or 1)),
                            Jsonb(item.evidence_memory_ids or []),
                            Jsonb(item.evidence_episode_ids or []),
                            item.attribute_embedding,
                            item.valid_from,
                            item.valid_to,
                            item.id,
                            item.tenant_id,
                            item.user_id,
                        ),
                    )
                    row = await cur.fetchone()
        except Exception as exc:
            raise DatabaseError("Failed to update profile belief") from exc
        return row is not None

    async def get_snapshot(self, *, tenant_id: str, user_id: str) -> str:
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT narrative FROM user_profile_snapshots
                        WHERE tenant_id = %s AND user_id = %s
                        """,
                        (tenant_id, user_id),
                    )
                    row = await cur.fetchone()
        except Exception as exc:
            raise DatabaseError("Failed to load profile snapshot") from exc
        return str(row["narrative"]) if row and row.get("narrative") else ""

    async def upsert_snapshot(self, *, tenant_id: str, user_id: str, narrative: str) -> None:
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO user_profile_snapshots (tenant_id, user_id, narrative, updated_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (tenant_id, user_id) DO UPDATE
                        SET narrative = EXCLUDED.narrative, updated_at = NOW()
                        """,
                        (tenant_id, user_id, narrative or ""),
                    )
        except Exception as exc:
            raise DatabaseError("Failed to upsert profile snapshot") from exc

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int:
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        DELETE FROM profile_beliefs
                        WHERE tenant_id = %s AND user_id = %s
                        """,
                        (tenant_id, user_id),
                    )
                    count = cur.rowcount or 0
                    await cur.execute(
                        """
                        DELETE FROM user_profile_snapshots
                        WHERE tenant_id = %s AND user_id = %s
                        """,
                        (tenant_id, user_id),
                    )
                    return count
        except Exception as exc:
            raise DatabaseError("Failed to delete user profile") from exc
