"""PostgreSQL last-k session messages.

Mem0 OSS stores these in SQLite (`mem0.memory.storage.SQLiteManager`).
DesayMem keeps them in Postgres so a multi-replica API shares one history.
"""

from __future__ import annotations

from typing import Any

from desaymem.core.exceptions import DatabaseError
from desaymem.stores.pgvector import PgVectorStore


class PgMessageStore:
    def __init__(self, backend: PgVectorStore) -> None:
        self._backend = backend

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
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    for message in messages:
                        await cur.execute(
                            """
                            INSERT INTO session_messages (
                                tenant_id, user_id, session_scope, role, content, name
                            ) VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                tenant_id,
                                user_id,
                                session_scope,
                                message.get("role"),
                                message.get("content"),
                                message.get("name"),
                            ),
                        )
                    await cur.execute(
                        """
                        DELETE FROM session_messages
                        WHERE session_scope = %s
                          AND id NOT IN (
                            SELECT id FROM (
                                SELECT id
                                FROM session_messages
                                WHERE session_scope = %s
                                ORDER BY created_at DESC
                                LIMIT %s
                            ) keep_rows
                          )
                        """,
                        (session_scope, session_scope, limit),
                    )
        except Exception as exc:
            raise DatabaseError("Failed to save session messages") from exc

    async def get_last_messages(
        self,
        session_scope: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT role, content, name, created_at FROM (
                            SELECT role, content, name, created_at
                            FROM session_messages
                            WHERE session_scope = %s
                            ORDER BY created_at DESC
                            LIMIT %s
                        ) recent
                        ORDER BY created_at ASC
                        """,
                        (session_scope, limit),
                    )
                    rows = await cur.fetchall()
        except Exception as exc:
            raise DatabaseError("Failed to load session messages") from exc
        return [
            {
                "role": row["role"],
                "content": row["content"],
                "name": row.get("name"),
                "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            }
            for row in rows
        ]

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int:
        await self._backend.open()
        try:
            async with self._backend.pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        DELETE FROM session_messages
                        WHERE tenant_id = %s AND user_id = %s
                        """,
                        (tenant_id, user_id),
                    )
                    return cur.rowcount or 0
        except Exception as exc:
            raise DatabaseError("Failed to delete session messages") from exc
