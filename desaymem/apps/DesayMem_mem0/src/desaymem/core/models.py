"""Public memory models for DesayMem_mem0.

Inspired by mem0.configs.base.MemoryItem, but redesigned around cockpit
isolation fields (tenant/user/vehicle/occupant) instead of Mem0's
user_id/agent_id/run_id payload layout.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class MemoryScope(BaseModel):
    """Forced isolation scope plus optional cockpit metadata."""

    tenant_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    vehicle_id: str = ""
    occupant_id: str = "primary"
    session_id: str = ""
    scene: str = ""
    source: str = "conversation"

    @field_validator("tenant_id", "user_id", mode="before")
    @classmethod
    def _require_non_empty(cls, value: Any, info) -> str:
        if value is None:
            raise ValueError(f"{info.field_name} is required")
        text = str(value).strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        if any(ch.isspace() for ch in text):
            raise ValueError(f"{info.field_name} must not contain internal whitespace")
        return text

    @field_validator("vehicle_id", "occupant_id", "session_id", "scene", "source", mode="before")
    @classmethod
    def _coerce_optional(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip()


class MemoryItem(BaseModel):
    """Canonical memory record returned by the core API."""

    id: str
    content: str
    tenant_id: str
    user_id: str
    vehicle_id: str = ""
    occupant_id: str = "primary"
    session_id: str = ""
    scene: str = ""
    source: str = "conversation"
    memory_type: str = "semantic_memory"
    content_hash: str | None = None
    text_lemmatized: str | None = None
    score: float | None = None
    score_details: dict[str, Any] | None = None
    event: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    embedding_model: str | None = None
    embedding_dims: int | None = None

    def to_public_dict(self) -> dict[str, Any]:
        payload = self.model_dump()
        created = payload.get("created_at")
        updated = payload.get("updated_at")
        if isinstance(created, datetime):
            payload["created_at"] = created.isoformat()
        if isinstance(updated, datetime):
            payload["updated_at"] = updated.isoformat()
        return payload


class ChatMessage(BaseModel):
    role: str
    content: str
    name: str | None = None

    @field_validator("role")
    @classmethod
    def _role(cls, value: str) -> str:
        allowed = {"user", "assistant", "system"}
        role = value.strip().lower()
        if role not in allowed:
            raise ValueError(f"role must be one of {sorted(allowed)}")
        return role


class BeliefItem(BaseModel):
    """One current-belief row from L3. Not a vector-search hit."""

    id: str
    subject: str = "User"
    attribute: str
    value: str
    conditions: dict[str, Any] = Field(default_factory=dict)
    stability: str = "episode"
    status: str = "active"
    confidence: float = 0.5
    support_count: int = 1
    occupant_id: str = "primary"
    evidence_memory_ids: list[str] = Field(default_factory=list)
    evidence_episode_ids: list[str] = Field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return self.model_dump()


class ProfileView(BaseModel):
    """User-scoped current beliefs plus a narrative for extraction context."""

    narrative: str = ""
    beliefs: list[BeliefItem] = Field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "narrative": self.narrative,
            "beliefs": [item.to_public_dict() for item in self.beliefs],
        }


class SearchFilters(BaseModel):
    vehicle_id: str | None = None
    occupant_id: str | None = None
    session_id: str | None = None
    scene: str | None = None
    source: str | None = None
    memory_type: str | None = None

    def as_store_filters(self) -> dict[str, str]:
        return {key: value for key, value in self.model_dump().items() if value}
