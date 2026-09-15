"""L2 episode formation: LLM judges continue vs new; Python writes rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from desaymem.core.logging import get_logger
from desaymem.extraction.parser import parse_messages
from desaymem.layers.parser import parse_object
from desaymem.layers.prompts import EPISODE_SYSTEM_PROMPT
from desaymem.providers.llm.base import LLMProvider
from desaymem.retrieval.scoring import cosine_similarity
from desaymem.stores.base import StoredMemory

logger = get_logger(__name__)


@dataclass
class EpisodeJudgment:
    continues: bool
    summary: str
    confidence: float


class EpisodeBuilder:
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def judge(
        self,
        *,
        messages: list[dict[str, Any]],
        new_facts: list[StoredMemory],
        active: StoredMemory | None,
        similarity: float | None = None,
        occurred_at: str | None = None,
    ) -> EpisodeJudgment | None:
        if not new_facts:
            return None
        user_prompt = _build_user_prompt(
            messages=messages,
            new_facts=new_facts,
            active=active,
            similarity=similarity,
            occurred_at=occurred_at,
        )
        try:
            raw = await self.llm.complete(
                [
                    {"role": "system", "content": EPISODE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
        except Exception:
            logger.warning("Episode judgment LLM call failed; skipping L2 update")
            return None
        payload = parse_object(raw)
        summary = str(payload.get("episode_summary") or "").strip()
        if not summary and active is not None:
            summary = active.content
        if not summary:
            summary = new_facts[0].content
        continues = bool(payload.get("continues")) and active is not None
        try:
            confidence = float(payload.get("confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        return EpisodeJudgment(
            continues=continues,
            summary=summary,
            confidence=max(0.0, min(1.0, confidence)),
        )


def mean_pool(vectors: list[list[float]]) -> list[float] | None:
    if not vectors:
        return None
    width = len(vectors[0])
    if width == 0 or any(len(vec) != width for vec in vectors):
        return None
    return [sum(col) / len(vectors) for col in zip(*vectors)]


def episode_similarity(active: StoredMemory | None, new_facts: list[StoredMemory]) -> float | None:
    if active is None or not active.embedding:
        return None
    pooled = mean_pool([row.embedding for row in new_facts if row.embedding])
    if pooled is None:
        return None
    return cosine_similarity(active.embedding, pooled)


def _build_user_prompt(
    *,
    messages: list[dict[str, Any]],
    new_facts: list[StoredMemory],
    active: StoredMemory | None,
    similarity: float | None,
    occurred_at: str | None,
) -> str:
    now = datetime.now(timezone.utc).date().isoformat()
    facts = [{"id": row.id, "text": row.content} for row in new_facts]
    active_payload: dict[str, Any] | None = None
    if active is not None:
        meta = active.metadata or {}
        active_payload = {
            "id": active.id,
            "summary": active.content,
            "updated_at": _as_iso(active.updated_at or active.created_at),
            "source_memory_ids": meta.get("source_memory_ids") or [],
        }
    sections = [
        f"## Current date\n{now}",
        f"## Observation time\n{occurred_at or now}",
        f"## Open episode\n{_dump(active_payload)}",
        f"## Embedding similarity to open episode\n{similarity if similarity is not None else 'n/a'}",
        f"## New facts\n{_dump(facts)}",
        f"## Latest messages\n{parse_messages(messages)}",
        "# Output:",
    ]
    return "\n\n".join(sections)


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _dump(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, default=str)
