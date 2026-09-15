"""SQLite history + last-k messages.

Migrated from mem0.memory.storage.SQLiteManager (OSS v2.0.18).
This is Mem0's third store: vector memories and entities stay in pgvector;
history/messages live in SQLite (`history.db`).

Cockpit extension: `messages` also stores tenant_id/user_id so delete_all
can isolate without parsing session_scope.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from desaymem.core.logging import get_logger

logger = get_logger(__name__)


class SQLiteHistoryStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self.connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._create_history_table()
        self._create_messages_table()

    def _create_history_table(self) -> None:
        with self._lock:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id           TEXT PRIMARY KEY,
                    memory_id    TEXT,
                    old_memory   TEXT,
                    new_memory   TEXT,
                    event        TEXT,
                    created_at   DATETIME,
                    updated_at   DATETIME,
                    is_deleted   INTEGER,
                    actor_id     TEXT,
                    role         TEXT
                )
                """
            )
            self.connection.commit()

    def _create_messages_table(self) -> None:
        with self._lock:
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT,
                    user_id TEXT,
                    session_scope TEXT,
                    role TEXT,
                    content TEXT,
                    name TEXT,
                    created_at DATETIME
                )
                """
            )
            self.connection.commit()

    async def add_history(
        self,
        memory_id: str,
        old_memory: str | None,
        new_memory: str | None,
        event: str,
        *,
        created_at: str | None = None,
        updated_at: str | None = None,
        is_deleted: int = 0,
        actor_id: str | None = None,
        role: str | None = None,
    ) -> None:
        self.batch_add_history(
            [
                {
                    "memory_id": memory_id,
                    "old_memory": old_memory,
                    "new_memory": new_memory,
                    "event": event,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "is_deleted": is_deleted,
                    "actor_id": actor_id,
                    "role": role,
                }
            ]
        )

    def batch_add_history(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return
        with self._lock:
            self.connection.executemany(
                """
                INSERT INTO history (
                    id, memory_id, old_memory, new_memory, event,
                    created_at, updated_at, is_deleted, actor_id, role
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(uuid.uuid4()),
                        record.get("memory_id"),
                        record.get("old_memory"),
                        record.get("new_memory"),
                        record.get("event"),
                        record.get("created_at"),
                        record.get("updated_at"),
                        record.get("is_deleted", 0),
                        record.get("actor_id"),
                        record.get("role"),
                    )
                    for record in records
                ],
            )
            self.connection.commit()

    async def get_history(self, memory_id: str) -> list[dict[str, Any]]:
        with self._lock:
            cur = self.connection.execute(
                """
                SELECT id, memory_id, old_memory, new_memory, event,
                       created_at, updated_at, is_deleted, actor_id, role
                FROM history
                WHERE memory_id = ?
                ORDER BY created_at ASC, DATETIME(updated_at) ASC
                """,
                (memory_id,),
            )
            rows = cur.fetchall()
        return [
            {
                "id": row[0],
                "memory_id": row[1],
                "old_memory": row[2],
                "new_memory": row[3],
                "event": row[4],
                "created_at": row[5],
                "updated_at": row[6],
                "is_deleted": bool(row[7]),
                "actor_id": row[8],
                "role": row[9],
            }
            for row in rows
        ]

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
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            for message in messages:
                self.connection.execute(
                    """
                    INSERT INTO messages (id, tenant_id, user_id, session_scope, role, content, name, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        tenant_id,
                        user_id,
                        session_scope,
                        message.get("role"),
                        message.get("content"),
                        message.get("name"),
                        now,
                    ),
                )
            self.connection.execute(
                """
                DELETE FROM messages WHERE session_scope = ? AND id NOT IN (
                    SELECT id FROM (
                        SELECT id FROM messages WHERE session_scope = ? ORDER BY created_at DESC LIMIT ?
                    )
                )
                """,
                (session_scope, session_scope, limit),
            )
            self.connection.commit()

    async def get_last_messages(
        self,
        session_scope: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        with self._lock:
            cur = self.connection.execute(
                """
                SELECT role, content, name, created_at FROM (
                    SELECT role, content, name, created_at
                    FROM messages
                    WHERE session_scope = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                ) ORDER BY created_at ASC
                """,
                (session_scope, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "role": row[0],
                "content": row[1],
                "name": row[2],
                "created_at": row[3],
            }
            for row in rows
        ]

    async def delete_by_user(self, *, tenant_id: str, user_id: str) -> int:
        with self._lock:
            cur = self.connection.execute(
                """
                DELETE FROM messages
                WHERE tenant_id = ? AND user_id = ?
                """,
                (tenant_id, user_id),
            )
            self.connection.commit()
            return cur.rowcount or 0

    def close(self) -> None:
        if self.connection:
            self.connection.close()
            self.connection = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            return
