"""SQL shape checks for cross-event history recall exclusion."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from desaymem_light.adapters.postgres.repositories import PostgresMemoryRepository
from desaymem_light.domain.models import MemoryScope


class CapturingConnection:
    def __init__(self) -> None:
        self.sql = ""
        self.parameters = None

    async def execute(self, sql, parameters=None):
        self.sql = sql
        self.parameters = parameters
        return self

    async def fetchall(self):
        return []


def scope() -> MemoryScope:
    return MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver")


async def test_related_events_allows_covered_but_excludes_used_history_events():
    """Covered Cbuf can return as history until it is actually used as Sk.

    A summarizes edge only means the event belonged to an earlier Cbuf. A
    related_to edge means it was later selected as historical support; excluding
    that case bounds repeated LLM consolidation without losing unused evidence.
    """
    connection = CapturingConnection()
    repository = PostgresMemoryRepository(connection)
    now = datetime.now(timezone.utc)
    exclude = [uuid4()]

    await repository.related_events(
        scope(), [0.1, 0.2], now - timedelta(days=90), now, exclude, 10,
    )

    sql = connection.sql
    assert "NOT EXISTS" in sql
    assert "memory_relations" in sql
    assert "related_to" in sql
    assert "relation_type IN" not in sql
    assert "summarizes" not in sql
    assert "r.target_memory_id = memory_items.id" in sql
    # scope and window filters are still applied
    assert connection.parameters is not None
    assert connection.parameters[3] == "driver"
    assert connection.parameters[-1] == 10
