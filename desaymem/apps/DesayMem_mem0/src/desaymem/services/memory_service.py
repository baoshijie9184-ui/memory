"""Service facade used by FastAPI routes."""

from __future__ import annotations

from desaymem.core.memory import DesayMemory
from desaymem.core.models import MemoryScope, ProfileView
from desaymem.core.result import AddResult, DeleteAllResult, DeleteResult, HistoryResult, ListResult, SearchResult


class MemoryService:
    def __init__(self, memory: DesayMemory) -> None:
        self.memory = memory

    async def add(
        self,
        messages: list[dict],
        scope: MemoryScope,
        extra_metadata: dict | None = None,
        *,
        infer: bool = True,
        memory_type: str | None = None,
        prompt: str | None = None,
    ) -> AddResult:
        return await self.memory.add_result(
            messages,
            scope,
            extra_metadata=extra_metadata,
            infer=infer,
            memory_type=memory_type,
            prompt=prompt,
        )

    async def search(
        self,
        query: str,
        scope: MemoryScope,
        *,
        top_k: int = 5,
        filters: dict | None = None,
    ) -> SearchResult:
        return await self.memory.search_result(query, scope, filters=filters, top_k=top_k)

    async def get_all(
        self,
        scope: MemoryScope,
        *,
        filters: dict | None = None,
        limit: int | None = None,
    ) -> ListResult:
        return await self.memory.get_all_result(scope, filters=filters, limit=limit)

    async def delete(self, memory_id: str, scope: MemoryScope) -> DeleteResult:
        return await self.memory.delete_result(memory_id, scope)

    async def delete_all(self, scope: MemoryScope) -> DeleteAllResult:
        return await self.memory.delete_all_result(scope)

    async def history(self, memory_id: str, scope: MemoryScope) -> HistoryResult:
        return await self.memory.history_result(memory_id, scope=scope)

    async def get_profile(self, scope: MemoryScope) -> ProfileView:
        return await self.memory.get_profile(scope.user_id, tenant_id=scope.tenant_id)

    async def health(self) -> bool:
        return await self.memory.healthcheck()
