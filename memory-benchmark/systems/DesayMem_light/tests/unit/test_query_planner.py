from datetime import datetime, timezone
from uuid import uuid4

import pytest

from desaymem_light.contracts.providers import ChatResult
from desaymem_light.domain.models import (
    MemoryScope, ModelUsage, SearchRequest, SearchResult, SearchUsage,
)
from desaymem_light.modules.retrieval.planned_retriever import PlannedRetriever
from desaymem_light.modules.retrieval.qwen_query_planner import QwenQueryPlanner


class Llm:
    def __init__(self): self.calls = 0
    async def generate_json(self, request):
        self.calls += 1
        return ChatResult(
            data={
                "query": "去过的餐厅", "tags": ["餐厅"],
                "time_from": "2026-08-31T00:00:00+08:00",
                "time_to": "2026-09-06T23:59:59+08:00", "intent": "history",
            },
            usage=ModelUsage(model="Qwen3-32B", input_tokens=30, output_tokens=20, latency_ms=1),
        )


class Retriever:
    def __init__(self): self.request = None
    async def search(self, request):
        self.request = request
        return SearchResult(
            request_id=request.request_id,
            usage=SearchUsage(embedding_calls=1, llm_calls=0),
        )


def request(query, **values):
    return SearchRequest(
        request_id=uuid4(), query=query,
        scope=MemoryScope(tenant_id="t", user_id="u", vehicle_id="v", occupant_id="driver"),
        **values,
    )


@pytest.mark.asyncio
async def test_general_query_skips_query_planner_llm():
    llm = Llm()
    plan = await QwenQueryPlanner(llm=llm).plan(request("播放什么音乐"))
    assert llm.calls == plan.llm_calls == 0
    assert plan.query == "播放什么音乐"


@pytest.mark.asyncio
async def test_relative_time_query_is_planned_and_passed_to_retrieval():
    llm, retriever = Llm(), Retriever()
    planned = PlannedRetriever(planner=QwenQueryPlanner(llm=llm), retriever=retriever)
    result = await planned.search(request("上一周去过哪些餐厅"))

    assert llm.calls == result.usage.llm_calls == 1
    assert retriever.request.query == "去过的餐厅"
    assert retriever.request.tags == ["餐厅"]
    assert retriever.request.time_from.utcoffset().total_seconds() == 8 * 3600
    assert retriever.request.time_to > retriever.request.time_from


@pytest.mark.asyncio
async def test_explicit_filters_skip_planner_and_are_preserved():
    llm, retriever = Llm(), Retriever()
    planned = PlannedRetriever(planner=QwenQueryPlanner(llm=llm), retriever=retriever)
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    await planned.search(request("餐厅", tags=["餐饮"], time_from=start))

    assert llm.calls == 0
    assert retriever.request.tags == ["餐饮"]
    assert retriever.request.time_from == start
