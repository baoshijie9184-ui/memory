"""Semantic rerank of vector candidates. No query keyword routing."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from desaymem.core.logging import get_logger
from desaymem.core.models import ProfileView
from desaymem.layers.parser import parse_object, remap_ids
from desaymem.layers.prompts import RERANK_SYSTEM_PROMPT
from desaymem.providers.llm.base import LLMProvider
from desaymem.stores.base import StoredMemory

logger = get_logger(__name__)


class SemanticReranker:
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm

    async def select(
        self,
        query: str,
        candidates: list[StoredMemory],
        *,
        profile: ProfileView | None = None,
        top_k: int = 5,
    ) -> list[StoredMemory]:
        if not candidates:
            return []
        mapping: dict[str, str] = {}
        payload: list[dict[str, Any]] = []
        by_fake: dict[str, StoredMemory] = {}
        for idx, row in enumerate(candidates):
            fake = str(idx)
            mapping[fake] = row.id
            by_fake[fake] = row
            meta = row.metadata or {}
            payload.append(
                {
                    "id": fake,
                    "layer": row.memory_type or "semantic_memory",
                    "text": row.content,
                    "occurred_at": meta.get("occurred_at")
                    or _as_iso(row.created_at),
                    "score": row.score,
                }
            )
        profile_payload = (profile.to_public_dict() if profile is not None else {"narrative": "", "beliefs": []})
        user_prompt = (
            f"## Current date\n{datetime.now(timezone.utc).date().isoformat()}\n\n"
            f"## Query\n{query}\n\n"
            f"## Profile\n{json.dumps(profile_payload, ensure_ascii=False)}\n\n"
            f"## Candidates\n{json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
            "# Output:"
        )
        try:
            raw = await self.llm.complete(
                [
                    {"role": "system", "content": RERANK_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
        except Exception:
            logger.warning("Rerank LLM call failed; returning vector ranking")
            return candidates[:top_k]
        parsed = parse_object(raw)
        if "selected_ids" not in parsed or not isinstance(parsed.get("selected_ids"), list):
            logger.warning("Rerank LLM returned no valid selected_ids; returning vector ranking")
            return candidates[:top_k]
        selected = remap_ids(parsed.get("selected_ids"), mapping)
        ordered: list[StoredMemory] = []
        seen: set[str] = set()
        fake_by_real = {real: fake for fake, real in mapping.items()}
        for real_id in selected:
            fake = fake_by_real.get(real_id)
            row = by_fake.get(fake or "")
            if row is None or row.id in seen:
                continue
            seen.add(row.id)
            ordered.append(row)
        return ordered[:top_k]


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
