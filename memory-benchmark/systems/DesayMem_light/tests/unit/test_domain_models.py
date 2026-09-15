from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from desaymem_light.domain.enums import MessageRole
from desaymem_light.domain.models import Message, SearchRequest, SessionScope


def test_session_scope_requires_vehicle_and_session() -> None:
    with pytest.raises(ValidationError):
        SessionScope(tenant_id="t1", user_id="u1", occupant_id="driver", vehicle_id="", session_id="s1")


def test_message_rejects_unknown_fields() -> None:
    now = datetime.now(timezone.utc)
    scope = SessionScope(
        tenant_id="t1", user_id="u1", vehicle_id="v1", occupant_id="driver", session_id="s1"
    )
    with pytest.raises(ValidationError):
        Message(
            id=uuid4(),
            scope=scope,
            request_id="r1",
            sequence_no=1,
            role=MessageRole.USER,
            content="hello",
            content_tokens=1,
            occurred_at=now,
            ingested_at=now,
            unexpected=True,
        )


def test_search_time_range_must_be_ordered() -> None:
    scope = SessionScope(
        tenant_id="t1", user_id="u1", vehicle_id="v1", occupant_id="driver", session_id="s1"
    )
    with pytest.raises(ValidationError):
        SearchRequest(
            request_id=uuid4(),
            query="music",
            scope=scope,
            time_from=datetime(2026, 1, 2, tzinfo=timezone.utc),
            time_to=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
