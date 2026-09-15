"""PostgreSQL + pgvector store.

Inspired by mem0.vector_stores.pgvector.PGVector (OSS v2.0.18, commit 4fa48390):
cosine distance `<=>`, score = max(0, 1 - distance), HNSW index.

Not a copy of Mem0's `id / vector / payload` table. DesayMem owns
`memory_items` with first-class tenant/user/vehicle columns.

Schema is never created on first request. Startup must call `check_schema`.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from desaymem.core.exceptions import ConfigurationError, DatabaseError
from desaymem.core.logging import get_logger
from desaymem.stores.base import StoredMemory

logger = get_logger(__name__)

_OPTIONAL_FILTERS = ("vehicle_id", "occupant_id", "session_id", "scene", "source", "memory_type")


async def _register_vector(conn) -> None:
    from pgvector.psycopg import register_vector_async

    await register_vector_async(conn)


def _row_to_memory(row: dict[str, Any], score: float | None = None) -> StoredMemory:
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    embedding = row.get("embedding")
    if embedding is not None:
        embedding = list(embedding)
    return StoredMemory(
        id=str(row["id"]),
        content=row["content"],
        tenant_id=row["tenant_id"],
        user_id=row["user_id"],
        vehicle_id=row.get("vehicle_id") or "",
        occupant_id=row.get("occupant_id") or "primary",
        session_id=row.get("session_id") or "",
        scene=row.get("scene") or "",
        source=row.get("source") or "conversation",
        memory_type=row.get("memory_type") or "semantic_memory",
        content_hash=row.get("content_hash") or "",
        text_lemmatized=row.get("text_lemmatized") or "",
        embedding=embedding,
        metadata=metadata,
        score=score,
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        embedding_model=row.get("embedding_model"),
        embedding_dims=row.get("embedding_dims"),
    )


class PgVectorStore:
    def __init__(
        self,
        dsn: str,
        *,
        embedding_dims: int,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        self._dsn = dsn
        self.embedding_dims = embedding_dims
        self._pool = AsyncConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row},
            configure=_register_vector,
        )
        self._opened = False

    @property
    def pool(self) -> AsyncConnectionPool:
        return self._pool

    async def open(self) -> None:
        if self._opened:
            return
        try:
            await self._pool.open()
        except Exception as exc:
            raise DatabaseError("Failed to connect to PostgreSQL") from exc
        self._opened = True

    async def close(self) -> None:
        if self._opened:
            await self._pool.close()
            self._opened = False

    async def check_schema(self, expected_dims: int) -> None:
        await self.open()
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.tables
                            WHERE table_schema = 'public' AND table_name = 'memory_items'
                        ) AS present
                        """
                    )
                    row = await cur.fetchone()
                    if not row or not row["present"]:
                        raise ConfigurationError(
                            "memory_items table is missing. Apply migrations/001_initial.sql before starting the API.",
                            error_code="CFG_SCHEMA",
                        )
                    for table_name in ("session_messages", "memory_entities"):
                        await cur.execute(
                            """
                            SELECT EXISTS (
                                SELECT 1 FROM information_schema.tables
                                WHERE table_schema = 'public' AND table_name = %s
                            ) AS present
                            """,
                            (table_name,),
                        )
                        present = await cur.fetchone()
                        if not present or not present["present"]:
                            raise ConfigurationError(
                                f"{table_name} table is missing. Apply migrations/002_session_entities.sql before starting the API.",
                                error_code="CFG_SCHEMA",
                            )
                    await cur.execute(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'memory_items'
                          AND column_name = 'memory_type'
                        """
                    )
                    if not await cur.fetchone():
                        raise ConfigurationError(
                            "memory_items.memory_type is missing. Apply migrations/002_session_entities.sql.",
                            error_code="CFG_SCHEMA",
                        )
                    await cur.execute(
                        """
                        SELECT column_name FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'memory_items'
                          AND column_name = 'text_lemmatized'
                        """
                    )
                    if not await cur.fetchone():
                        raise ConfigurationError(
                            "memory_items.text_lemmatized is missing. Apply migrations/003_bm25.sql.",
                            error_code="CFG_SCHEMA",
                        )
                    for table_name, migration in (
                        ("profile_beliefs", "004_layers.sql"),
                        ("user_profile_snapshots", "004_layers.sql"),
                    ):
                        await cur.execute(
                            """
                            SELECT EXISTS (
                                SELECT 1 FROM information_schema.tables
                                WHERE table_schema = 'public' AND table_name = %s
                            ) AS present
                            """,
                            (table_name,),
                        )
                        present = await cur.fetchone()
                        if not present or not present["present"]:
                            raise ConfigurationError(
                                f"{table_name} table is missing. Apply migrations/{migration} before starting the API.",
                                error_code="CFG_SCHEMA",
                            )
                    await cur.execute(
                        """
                        SELECT format_type(a.atttypid, a.atttypmod) AS col_type
                        FROM pg_attribute a
                        JOIN pg_class c ON a.attrelid = c.oid
                        JOIN pg_namespace n ON c.relnamespace = n.oid
                        WHERE n.nspname = 'public'
                          AND c.relname = 'memory_items'
                          AND a.attname = 'embedding'
                          AND a.attisdropped = false
                        """
                    )
                    type_row = await cur.fetchone()
                    if not type_row:
                        raise ConfigurationError(
                            "memory_items.embedding column is missing",
                            error_code="CFG_SCHEMA",
                        )
                    col_type = type_row["col_type"]
                    expected = f"vector({expected_dims})"
                    if col_type != expected:
                        raise ConfigurationError(
                            f"Embedding dimension mismatch versus schema: database has {col_type}, "
                            f"config expects {expected}. Update EMBEDDING_DIMS or re-apply the migration.",
                            error_code="CFG_DIMS",
                            details={"database": col_type, "expected": expected},
                        )
                    await cur.execute(
                        """
                        SELECT format_type(a.atttypid, a.atttypmod) AS col_type
                        FROM pg_attribute a
                        JOIN pg_class c ON a.attrelid = c.oid
                        JOIN pg_namespace n ON c.relnamespace = n.oid
                        WHERE n.nspname = 'public'
                          AND c.relname = 'memory_entities'
                          AND a.attname = 'embedding'
                          AND a.attisdropped = false
                        """
                    )
                    entity_type_row = await cur.fetchone()
                    if not entity_type_row:
                        raise ConfigurationError(
                            "memory_entities.embedding column is missing",
                            error_code="CFG_SCHEMA",
                        )
                    if entity_type_row["col_type"] != expected:
                        raise ConfigurationError(
                            f"Entity embedding dimension mismatch versus schema: database has "
                            f"{entity_type_row['col_type']}, config expects {expected}.",
                            error_code="CFG_DIMS",
                            details={"database": entity_type_row["col_type"], "expected": expected},
                        )
                    await cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                    if not await cur.fetchone():
                        raise ConfigurationError(
                            "pgvector extension is not installed",
                            error_code="CFG_PGVECTOR",
                        )
        except ConfigurationError:
            raise
        except Exception as exc:
            raise DatabaseError("Failed to verify database schema") from exc

    async def healthcheck(self) -> bool:
        try:
            await self.open()
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1 AS ok")
                    row = await cur.fetchone()
                    return bool(row and row["ok"] == 1)
        except Exception:
            logger.error("PostgreSQL healthcheck failed")
            return False

    def _validate_vector(self, vector: list[float]) -> None:
        if len(vector) != self.embedding_dims:
            from desaymem.core.exceptions import EmbeddingError

            raise EmbeddingError(
                f"Embedding dimension mismatch: expected {self.embedding_dims}, got {len(vector)}",
                error_code="EMBED_006",
                details={"expected": self.embedding_dims, "actual": len(vector)},
            )

    @staticmethod
    def _filter_sql(filters: dict[str, Any] | None) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for key in _OPTIONAL_FILTERS:
            if filters and filters.get(key) not in (None, ""):
                clauses.append(f"{key} = %s")
                params.append(filters[key])
        extra = (" AND " + " AND ".join(clauses)) if clauses else ""
        return extra, params

    async def insert(self, items: list[StoredMemory]) -> None:
        if not items:
            return
        await self.open()
        for item in items:
            if item.embedding is None:
                raise DatabaseError("Cannot insert memory without embedding")
            self._validate_vector(item.embedding)
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    for item in items:
                        await cur.execute(
                            """
                            INSERT INTO memory_items (
                                id, tenant_id, user_id, vehicle_id, occupant_id,
                                session_id, scene, source, memory_type, content, content_hash,
                                text_lemmatized, embedding, embedding_model, embedding_dims, metadata
                            ) VALUES (
                                %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s,
                                %s, %s, %s, %s, %s
                            )
                            ON CONFLICT (tenant_id, user_id, content_hash) DO NOTHING
                            """,
                            (
                                item.id,
                                item.tenant_id,
                                item.user_id,
                                item.vehicle_id,
                                item.occupant_id,
                                item.session_id,
                                item.scene,
                                item.source,
                                item.memory_type or "semantic_memory",
                                item.content,
                                item.content_hash,
                                item.text_lemmatized or "",
                                item.embedding,
                                item.embedding_model,
                                item.embedding_dims,
                                Jsonb(item.metadata or {}),
                            ),
                        )
        except Exception as exc:
            raise DatabaseError("Failed to insert memories") from exc

    async def update(self, item: StoredMemory) -> bool:
        if item.embedding is None:
            raise DatabaseError("Cannot update memory without embedding")
        self._validate_vector(item.embedding)
        await self.open()
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        UPDATE memory_items
                        SET content = %s,
                            content_hash = %s,
                            text_lemmatized = %s,
                            embedding = %s,
                            metadata = %s,
                            embedding_model = %s,
                            embedding_dims = %s,
                            updated_at = NOW()
                        WHERE id = %s AND tenant_id = %s AND user_id = %s
                        RETURNING id
                        """,
                        (
                            item.content,
                            item.content_hash,
                            item.text_lemmatized or "",
                            item.embedding,
                            Jsonb(item.metadata or {}),
                            item.embedding_model,
                            item.embedding_dims,
                            item.id,
                            item.tenant_id,
                            item.user_id,
                        ),
                    )
                    row = await cur.fetchone()
        except Exception as exc:
            raise DatabaseError("Failed to update memory") from exc
        return row is not None

    async def get_active_episode(
        self,
        *,
        tenant_id: str,
        user_id: str,
        occupant_id: str = "primary",
    ) -> StoredMemory | None:
        await self.open()
        occupant = occupant_id or "primary"
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT id, tenant_id, user_id, vehicle_id, occupant_id,
                               session_id, scene, source, memory_type, content, content_hash,
                               text_lemmatized, embedding,
                               metadata, created_at, updated_at,
                               embedding_model, embedding_dims
                        FROM memory_items
                        WHERE tenant_id = %s AND user_id = %s
                          AND occupant_id = %s
                          AND memory_type = 'episodic_memory'
                          AND COALESCE(metadata->>'episode_status', 'active') = 'active'
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (tenant_id, user_id, occupant),
                    )
                    row = await cur.fetchone()
        except Exception as exc:
            raise DatabaseError("Failed to load active episode") from exc
        return _row_to_memory(row) if row else None

    async def search(
        self,
        vector: list[float],
        *,
        tenant_id: str,
        user_id: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[StoredMemory]:
        await self.open()
        self._validate_vector(vector)
        extra, extra_params = self._filter_sql(filters)
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT id, tenant_id, user_id, vehicle_id, occupant_id,
                               session_id, scene, source, memory_type, content, content_hash,
                               text_lemmatized,
                               metadata, created_at, updated_at,
                               embedding_model, embedding_dims,
                               embedding <=> %s::vector AS distance
                        FROM memory_items
                        WHERE tenant_id = %s AND user_id = %s
                        {extra}
                        ORDER BY distance
                        LIMIT %s
                        """,
                        (vector, tenant_id, user_id, *extra_params, top_k),
                    )
                    rows = await cur.fetchall()
        except Exception as exc:
            raise DatabaseError("Failed to search memories") from exc
        results: list[StoredMemory] = []
        for row in rows:
            distance = float(row["distance"])
            score = max(0.0, 1.0 - distance)
            results.append(_row_to_memory(row, score=score))
        return results

    async def keyword_search(
        self,
        query: str,
        *,
        tenant_id: str,
        user_id: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[StoredMemory] | None:
        if not query or not str(query).strip():
            return []
        await self.open()
        extra, extra_params = self._filter_sql(filters)
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT id, tenant_id, user_id, vehicle_id, occupant_id,
                               session_id, scene, source, memory_type, content, content_hash,
                               text_lemmatized,
                               metadata, created_at, updated_at,
                               embedding_model, embedding_dims,
                               ts_rank_cd(
                                   to_tsvector('simple', text_lemmatized),
                                   plainto_tsquery('simple', %s)
                               ) AS rank
                        FROM memory_items
                        WHERE tenant_id = %s AND user_id = %s
                          AND to_tsvector('simple', text_lemmatized)
                              @@ plainto_tsquery('simple', %s)
                        {extra}
                        ORDER BY rank DESC
                        LIMIT %s
                        """,
                        (query, tenant_id, user_id, query, *extra_params, top_k),
                    )
                    rows = await cur.fetchall()
        except Exception as exc:
            logger.debug("Keyword search failed: %s", exc)
            return None
        return [_row_to_memory(row, score=float(row["rank"])) for row in rows]

    async def list(
        self,
        *,
        tenant_id: str,
        user_id: str,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[StoredMemory]:
        await self.open()
        extra, extra_params = self._filter_sql(filters)
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT id, tenant_id, user_id, vehicle_id, occupant_id,
                               session_id, scene, source, memory_type, content, content_hash,
                               text_lemmatized,
                               metadata, created_at, updated_at,
                               embedding_model, embedding_dims
                        FROM memory_items
                        WHERE tenant_id = %s AND user_id = %s
                        {extra}
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (tenant_id, user_id, *extra_params, limit),
                    )
                    rows = await cur.fetchall()
        except Exception as exc:
            raise DatabaseError("Failed to list memories") from exc
        return [_row_to_memory(row) for row in rows]

    async def get(
        self,
        memory_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> StoredMemory | None:
        await self.open()
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT id, tenant_id, user_id, vehicle_id, occupant_id,
                               session_id, scene, source, memory_type, content, content_hash,
                               text_lemmatized,
                               metadata, created_at, updated_at,
                               embedding_model, embedding_dims
                        FROM memory_items
                        WHERE id = %s AND tenant_id = %s AND user_id = %s
                        """,
                        (memory_id, tenant_id, user_id),
                    )
                    row = await cur.fetchone()
        except Exception as exc:
            raise DatabaseError("Failed to load memory") from exc
        return _row_to_memory(row) if row else None

    async def delete(
        self,
        memory_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> bool:
        await self.open()
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        DELETE FROM memory_items
                        WHERE id = %s AND tenant_id = %s AND user_id = %s
                        RETURNING id
                        """,
                        (memory_id, tenant_id, user_id),
                    )
                    row = await cur.fetchone()
        except Exception as exc:
            raise DatabaseError("Failed to delete memory") from exc
        return row is not None

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int:
        await self.open()
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        DELETE FROM memory_items
                        WHERE tenant_id = %s AND user_id = %s
                        """,
                        (tenant_id, user_id),
                    )
                    return cur.rowcount or 0
        except Exception as exc:
            raise DatabaseError("Failed to delete user memories") from exc

    async def existing_hashes(self, *, tenant_id: str, user_id: str) -> set[str]:
        await self.open()
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT content_hash FROM memory_items
                        WHERE tenant_id = %s AND user_id = %s
                        """,
                        (tenant_id, user_id),
                    )
                    rows = await cur.fetchall()
        except Exception as exc:
            raise DatabaseError("Failed to load content hashes") from exc
        return {row["content_hash"] for row in rows if row.get("content_hash")}
