from datetime import datetime, timezone
from uuid import uuid4

import pytest

from desaymem_light.contracts.providers import ChatResult
from desaymem_light.domain.errors import ContractError
from desaymem_light.domain.models import (
    CrossEventRequest,
    MemoryScope,
    ModelUsage,
    NumberedEvent,
    ProfileItemView,
    ProfileUpdateRequest,
)
from desaymem_light.modules.cross_event.structmem_synthesizer import StructMemStyleSynthesizer
from desaymem_light.modules.profile.timem_updater import TiMemStyleProfileUpdater


class FakeLlm:
    def __init__(self, data):
        self.data = data
        self.calls = 0

    async def generate_json(self, request):
        self.calls += 1
        return ChatResult(
            data=self.data,
            usage=ModelUsage(model="Qwen3-32B", input_tokens=20, output_tokens=8, latency_ms=1),
        )


def _scope():
    return MemoryScope(tenant_id="t1", user_id="u1", vehicle_id="v1", occupant_id="driver")


def _events():
    now = datetime.now(timezone.utc)
    return [
        NumberedEvent(number=0, memory_id=uuid4(), content="通勤时播放周杰伦", occurred_at=now),
        NumberedEvent(number=1, memory_id=uuid4(), content="下班时播放周杰伦", occurred_at=now),
    ]


def _cross_event():
    now = datetime.now(timezone.utc)
    return NumberedEvent(
        number=0, memory_id=uuid4(),
        content="用户多次在通勤时播放周杰伦", occurred_at=now,
    )


@pytest.mark.asyncio
async def test_cross_event_uses_one_call_and_keeps_valid_sources() -> None:
    llm = FakeLlm(
        {"should_create": True, "summary": "用户两次在通勤时播放周杰伦", "supporting_event_numbers": []}
    )
    result = await StructMemStyleSynthesizer(llm=llm, prompt="cross").synthesize(
        CrossEventRequest(
            request_id=uuid4(), scope=_scope(), events=_events(),
            covered_event_count=2, prompt_version="v2",
        )
    )
    assert llm.calls == 1
    assert result.supporting_event_numbers == []


@pytest.mark.asyncio
async def test_cross_event_rejects_hallucinated_source_number() -> None:
    llm = FakeLlm(
        {"should_create": True, "summary": "invalid", "supporting_event_numbers": [99]}
    )
    with pytest.raises(ContractError, match="unknown event"):
        await StructMemStyleSynthesizer(llm=llm, prompt="cross").synthesize(
            CrossEventRequest(
                request_id=uuid4(), scope=_scope(), events=_events(),
                covered_event_count=2, prompt_version="v2",
            )
        )


@pytest.mark.asyncio
async def test_cross_event_rejects_cbuf_number_as_historical_support() -> None:
    llm = FakeLlm(
        {"should_create": True, "summary": "complete", "supporting_event_numbers": [0]}
    )
    with pytest.raises(ContractError, match="covered Cbuf"):
        await StructMemStyleSynthesizer(llm=llm, prompt="cross").synthesize(
            CrossEventRequest(
                request_id=uuid4(), scope=_scope(), events=_events(),
                covered_event_count=2, prompt_version="v2",
            )
        )


@pytest.mark.asyncio
async def test_profile_update_supports_multiple_values_in_one_call() -> None:
    existing = ProfileItemView(
        number=0, id=uuid4(), attribute="喜欢的歌手", value="周杰伦", confirmation_count=2
    )
    llm = FakeLlm(
        {
            "operations": [
                {
                    "action": "COEXIST",
                    "attribute": "喜欢的歌手",
                    "value": "陶喆",
                    "profile_item_number": 0,
                    "evidence_event_numbers": [1],
                }
            ],
            "summary": "用户喜欢周杰伦和陶喆。",
        }
    )
    result = await TiMemStyleProfileUpdater(llm=llm, prompt="profile").update(
        ProfileUpdateRequest(
            request_id=uuid4(), scope=_scope(), current_summary="用户喜欢周杰伦。",
            current_items=[existing], cross_event=_cross_event(),
            evidence_events=_events(), prompt_version="v1",
        )
    )
    assert llm.calls == 1
    assert result.operations[0].action == "COEXIST"
    assert result.summary == "用户喜欢周杰伦和陶喆。"


@pytest.mark.asyncio
async def test_profile_update_rejects_unknown_profile_reference() -> None:
    llm = FakeLlm(
        {
            "operations": [
                {"action": "CONFIRM", "profile_item_number": 9, "evidence_event_numbers": [0]}
            ],
            "summary": "unchanged",
        }
    )
    with pytest.raises(ContractError, match="unknown profile item"):
        await TiMemStyleProfileUpdater(llm=llm, prompt="profile").update(
            ProfileUpdateRequest(
                request_id=uuid4(), scope=_scope(), current_items=[],
                cross_event=_cross_event(), evidence_events=_events(), prompt_version="v1",
            )
        )
