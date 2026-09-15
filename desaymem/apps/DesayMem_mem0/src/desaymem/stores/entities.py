"""PostgreSQL entity store.

Mem0 OSS uses a second vector collection (`{collection}_entities`) with
payload JSONB. DesayMem uses a first-class `memory_entities` table with
tenant/user isolation and `linked_memory_ids`.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.types.json import Jsonb

from desaymem.core.exceptions import DatabaseError, EmbeddingError
from desaymem.stores.base import StoredEntity
from desaymem.stores.pgvector import PgVectorStore


def _row_to_entity(row: dict[str, Any], score: float | None = None) -> StoredEntity:
    linked = row.get("linked_memory_ids") or []
    if isinstance(linked, str):
        linked = json.loads(linked)
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    embedding = row.get("embedding")
    if embedding is not None:
        embedding = list(embedding)
    return StoredEntity(
        id=str(row["id"]),
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        entity_text=row["entity_text"],
        entity_type=row["entity_type"],
        normalized_text=row["normalized_text"],
        embedding=embedding,
        linked_memory_ids=[str(item) for item in linked],
        metadata=metadata,
        score=score,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


class PgEntityStore:
    def __init__(self, backend: PgVectorStore) -> None:
        self._backend = backend

    def _validate(self, vector: list[float]) -> None:
        if len(vector) != self._backend.embedding_dims:
            raise EmbeddingError(
                f"Embedding dimension mismatch: expected {self._backend.embedding_dims}, got {len(vector)}",
                error_code="EMBED_006",
                details={"expected": self._backend.embedding_dims, "actual": len(vector)},
            )

    async def insert(self, items: list[StoredEntity]) -> None:
        if not items:
            return
        await self._backend.open()
        for item in items:
            if item.embedding is None:
                raise DatabaseError("Cannot insert entity without embedding")
            self._validate(item.embedding)
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    for item in items:
                        await cur.execute(
                            """
                            INSERT INTO memory_entities (
                                id, tenant_id, user_id, entity_text, entity_type,
                                normalized_text, embedding, linked_memory_ids, metadata
                            ) VALUES (
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s
                            )
                            ON CONFLICT (tenant_id, user_id, normalized_text) DO UPDATE
                            SET linked_memory_ids = (
                                    SELECT jsonb_agg(DISTINCT value)
                                    FROM jsonb_array_elements(
                                        memory_entities.linked_memory_ids || EXCLUDED.linked_memory_ids
                                    )
                                ),
                                updated_at = NOW()
                            """,
                            (
                                item.id,
                                item.tenant_id,
                                item.user_id,
                                item.entity_text,
                                item.entity_type,
                                item.normalized_text,
                                item.embedding,
                                Jsonb(item.linked_memory_ids or []),
                                Jsonb(item.metadata or {}),
                            ),
                        )
        except Exception as exc:
            raise DatabaseError("Failed to insert entities") from exc

    async def search(
        self,
        vector: list[float],
        *,
        tenant_id: str,
        user_id: str,
        top_k: int = 5,
    ) -> list[StoredEntity]:
        await self._backend.open()
        self._validate(vector)
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT id, tenant_id, user_id, entity_text, entity_type,
                               normalized_text, linked_memory_ids, metadata,
                               created_at, updated_at,
                               embedding <=> %s::vector AS distance
                        FROM memory_entities
                        WHERE tenant_id = %s AND user_id = %s
                        ORDER BY distance
                        LIMIT %s
                        """,
                        (vector, tenant_id, user_id, top_k),
                    )
                    rows = await cur.fetchall()
        except Exception as exc:
            raise DatabaseError("Failed to search entities") from exc
        results: list[StoredEntity] = []
        for row in rows:
            score = max(0.0, 1.0 - float(row["distance"]))
            results.append(_row_to_entity(row, score=score))
        return results

    async def list(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 10000,
    ) -> list[StoredEntity]:
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT id, tenant_id, user_id, entity_text, entity_type,
                               normalized_text, linked_memory_ids, metadata,
                               created_at, updated_at
                        FROM memory_entities
                        WHERE tenant_id = %s AND user_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (tenant_id, user_id, limit),
                    )
                    rows = await cur.fetchall()
        except Exception as exc:
            raise DatabaseError("Failed to list entities") from exc
        return [_row_to_entity(row) for row in rows]

    async def get_by_normalized(
        self,
        *,
        tenant_id: str,
        user_id: str,
        normalized_text: str,
    ) -> StoredEntity | None:
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT id, tenant_id, user_id, entity_text, entity_type,
                               normalized_text, linked_memory_ids, metadata,
                               created_at, updated_at
                        FROM memory_entities
                        WHERE tenant_id = %s AND user_id = %s AND normalized_text = %s
                        """,
                        (tenant_id, user_id, normalized_text),
                    )
                    row = await cur.fetchone()
        except Exception as exc:
            raise DatabaseError("Failed to load entity") from exc
        return _row_to_entity(row) if row else None

    async def update_links(
        self,
        entity_id: str,
        linked_memory_ids: list[str],
        *,
        tenant_id: str,
        user_id: str,
    ) -> None:
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        UPDATE memory_entities
                        SET linked_memory_ids = %s, updated_at = NOW()
                        WHERE id = %s AND tenant_id = %s AND user_id = %s
                        """,
                        (Jsonb(linked_memory_ids), entity_id, tenant_id, user_id),
                    )
        except Exception as exc:
            raise DatabaseError("Failed to update entity links") from exc

    async def delete(
        self,
        entity_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> bool:
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        DELETE FROM memory_entities
                        WHERE id = %s AND tenant_id = %s AND user_id = %s
                        RETURNING id
                        """,
                        (entity_id, tenant_id, user_id),
                    )
                    row = await cur.fetchone()
        except Exception as exc:
            raise DatabaseError("Failed to delete entity") from exc
        return row is not None

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int:
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        DELETE FROM memory_entities
                        WHERE tenant_id = %s AND user_id = %s
                        """,
                        (tenant_id, user_id),
                    )
                    return cur.rowcount or 0
        except Exception as exc:
            raise DatabaseError("Failed to delete user entities") from exc
