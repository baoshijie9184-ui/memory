"""Unified API result envelopes. These are DesayMem-owned and do not mirror
Mem0's internal `{"results": [...]}` payload shape.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from desaymem.core.models import MemoryItem, ProfileView


class AddResult(BaseModel):
    memories: list[MemoryItem] = Field(default_factory=list)
    skipped_duplicates: int = 0
    extracted: int = 0
    episode: MemoryItem | None = None
    beliefs_applied: int = 0

    def to_public_dict(self) -> dict[str, Any]:
        payload = {
            "memories": [item.to_public_dict() for item in self.memories],
            "extracted": self.extracted,
            "skipped_duplicates": self.skipped_duplicates,
            "beliefs_applied": self.beliefs_applied,
        }
        if self.episode is not None:
            payload["episode"] = self.episode.to_public_dict()
        return payload


class SearchResult(BaseModel):
    memories: list[MemoryItem] = Field(default_factory=list)
    query: str
    top_k: int
    profile: ProfileView = Field(default_factory=ProfileView)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "memories": [item.to_public_dict() for item in self.memories],
            "query": self.query,
            "top_k": self.top_k,
            "profile": self.profile.to_public_dict(),
        }


class ListResult(BaseModel):
    memories: list[MemoryItem] = Field(default_factory=list)
    count: int = 0

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "memories": [item.to_public_dict() for item in self.memories],
            "count": self.count,
        }


class DeleteResult(BaseModel):
    deleted: bool
    memory_id: str

    def to_public_dict(self) -> dict[str, Any]:
        return {"deleted": self.deleted, "memory_id": self.memory_id}


class DeleteAllResult(BaseModel):
    deleted_count: int
    user_id: str
    tenant_id: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "deleted_count": self.deleted_count,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
        }


class HistoryResult(BaseModel):
    memory_id: str
    events: list[dict[str, Any]] = Field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {"memory_id": self.memory_id, "events": self.events}


class HealthResult(BaseModel):
    status: str
    app: str
    database: str
    embedding_dims: int | None = None
    llm_model: str | None = None
    embedding_model: str | None = None
