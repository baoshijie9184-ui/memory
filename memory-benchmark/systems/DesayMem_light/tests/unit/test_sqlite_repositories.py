from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from desaymem_light.adapters.sqlite.migrations import apply_sqlite_migrations
from desaymem_light.adapters.sqlite.repositories import HistoryWrite, SQLiteCompatibilityStore
from desaymem_light.domain.enums import MessageRole
from desaymem_light.domain.models import Message, SessionScope


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.asyncio
async def test_sqlite_store_writes_compatibility_data_and_outbox() -> None:
    store = SQLiteCompatibilityStore(":memory:")
    apply_sqlite_migrations(store.connection, PROJECT_ROOT / "migrations" / "sqlite")
    now = datetime.now(timezone.utc)
    scope = SessionScope(
        tenant_id="t1", user_id="u1", vehicle_id="v1", occupant_id="driver", session_id="s1"
    )
    message = Message(
        id=uuid4(), scope=scope, request_id="r1", sequence_no=1, role=MessageRole.USER,
        content="play music", content_tokens=2, occurred_at=now, ingested_at=now,
    )
    history = HistoryWrite(
        id=uuid4(), scope=scope, memory_id=uuid4(), new_memory="likes music",
        event="ADD", source_type="topic", source_id=uuid4(),
        created_at=now.isoformat(), updated_at=now.isoformat(),
    )

    await store.cache_message(message)
    await store.append_history(history)

    recent = await store.recent_messages(scope)
    operations = store.connection.execute(
        "SELECT table_name, operation FROM json_outbox ORDER BY id"
    ).fetchall()
    assert recent[0]["content"] == "play music"
    assert [tuple(row) for row in operations] == [
        ("messages_cache", "INSERT"),
        ("history", "INSERT"),
    ]
    await store.close()


@pytest.mark.asyncio
async def test_sqlite_cache_isolated_by_vehicle() -> None:
    store = SQLiteCompatibilityStore(":memory:")
    apply_sqlite_migrations(store.connection, PROJECT_ROOT / "migrations" / "sqlite")
    now = datetime.now(timezone.utc)
    first_scope = SessionScope(
        tenant_id="t1", user_id="u1", vehicle_id="v1", occupant_id="driver", session_id="s1"
    )
    second_scope = first_scope.model_copy(update={"vehicle_id": "v2"})
    await store.cache_message(
        Message(
            id=uuid4(), scope=first_scope, request_id="r1", sequence_no=1,
            role=MessageRole.USER, content="private to v1", content_tokens=3,
            occurred_at=now, ingested_at=now,
        )
    )

    assert await store.recent_messages(second_scope) == []
    await store.close()
