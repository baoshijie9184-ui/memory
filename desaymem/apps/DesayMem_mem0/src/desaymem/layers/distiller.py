"""L3 belief distillation: LLM proposes beliefs; Python is the only writer."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from desaymem.core.enums import BeliefDecision, BeliefStability, BeliefStatus
from desaymem.core.logging import get_logger
from desaymem.core.models import BeliefItem, MemoryScope, ProfileView
from desaymem.layers.parser import parse_object, remap_ids
from desaymem.layers.prompts import DISTILL_SYSTEM_PROMPT
from desaymem.providers.embedding.base import EmbeddingProvider
from desaymem.providers.llm.base import LLMProvider
from desaymem.stores.base import ProfileStore, StoredBelief, StoredMemory

logger = get_logger(__name__)

_STABILITY = {item.value for item in BeliefStability}
_DECISIONS = {item.value for item in BeliefDecision}


def belief_key_text(subject: str, attribute: str, conditions: dict[str, Any] | None) -> str:
    cond = json.dumps(conditions or {}, ensure_ascii=False, sort_keys=True)
    return f"{subject or 'User'} | {attribute} | {cond}"


def assemble_narrative(beliefs: list[StoredBelief]) -> str:
    lines: list[str] = []
    for item in beliefs:
        if item.status != BeliefStatus.ACTIVE.value:
            continue
        cond = item.conditions or {}
        suffix = f" ({json.dumps(cond, ensure_ascii=False, sort_keys=True)})" if cond else ""
        lines.append(f"{item.subject} {item.attribute}: {item.value}{suffix}")
    return "\n".join(lines)


def stored_to_belief_item(row: StoredBelief) -> BeliefItem:
    return BeliefItem(
        id=row.id,
        subject=row.subject,
        attribute=row.attribute,
        value=row.value,
        conditions=dict(row.conditions or {}),
        stability=row.stability,
        status=row.status,
        confidence=row.confidence,
        support_count=row.support_count,
        occupant_id=row.occupant_id,
        evidence_memory_ids=list(row.evidence_memory_ids or []),
        evidence_episode_ids=list(row.evidence_episode_ids or []),
    )


def _evidence_day(row: StoredMemory) -> str | None:
    value = (row.metadata or {}).get("occurred_at") or row.created_at
    if value is None:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    return parsed.date().isoformat()


class ProfileDistiller:
    def __init__(
        self,
        llm: LLMProvider,
        embedding: EmbeddingProvider,
        store: ProfileStore,
        *,
        match_threshold: float = 0.82,
    ) -> None:
        self.llm = llm
        self.embedding = embedding
        self.store = store
        self.match_threshold = match_threshold

    async def distill(
        self,
        *,
        scope: MemoryScope,
        cluster: list[StoredMemory],
        episode_id: str | None = None,
    ) -> int:
        if not cluster:
            return 0
        mapping, facts = _index_facts(cluster)
        prompt = _build_user_prompt(facts)
        try:
            raw = await self.llm.complete(
                [
                    {"role": "system", "content": DISTILL_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
            )
        except Exception:
            logger.warning("Profile distillation LLM call failed; skipping L3 update")
            return 0
        payload = parse_object(raw)
        candidates = payload.get("beliefs") or []
        if not isinstance(candidates, list):
            return 0
        applied = 0
        valid_ids = set(mapping.values())
        evidence_days = {
            row.id: _evidence_day(row)
            for row in cluster
        }
        for raw_belief in candidates:
            if not isinstance(raw_belief, dict):
                continue
            raw_belief["evidence_ids"] = remap_ids(raw_belief.get("evidence_ids"), mapping)
            if await self._apply_one(
                scope,
                raw_belief,
                valid_ids,
                evidence_days=evidence_days,
                episode_id=episode_id,
            ):
                applied += 1
        if applied:
            await self.refresh_snapshot(scope)
        return applied

    async def refresh_snapshot(self, scope: MemoryScope) -> str:
        active = await self.store.list_active(tenant_id=scope.tenant_id, user_id=scope.user_id)
        narrative = assemble_narrative(active)
        await self.store.upsert_snapshot(
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            narrative=narrative,
        )
        return narrative

    async def profile_view(self, scope: MemoryScope) -> ProfileView:
        rows = await self.store.list_active(tenant_id=scope.tenant_id, user_id=scope.user_id)
        narrative = await self.store.get_snapshot(tenant_id=scope.tenant_id, user_id=scope.user_id)
        if not narrative:
            narrative = assemble_narrative(rows)
        return ProfileView(
            narrative=narrative,
            beliefs=[stored_to_belief_item(row) for row in rows],
        )

    async def _apply_one(
        self,
        scope: MemoryScope,
        raw: dict[str, Any],
        valid_ids: set[str],
        *,
        evidence_days: dict[str, str | None],
        episode_id: str | None,
    ) -> bool:
        attribute = str(raw.get("attribute") or "").strip()
        value = str(raw.get("value") or "").strip()
        if not attribute or not value:
            return False
        evidence = [eid for eid in (raw.get("evidence_ids") or []) if eid in valid_ids]
        if not evidence:
            logger.info("Dropping belief without grounded evidence ids")
            return False
        decision = str(raw.get("decision") or BeliefDecision.CREATE.value).upper()
        if decision not in _DECISIONS or decision == BeliefDecision.NOOP.value:
            return False
        stability = str(raw.get("stability") or BeliefStability.EPISODE.value).lower()
        if stability not in _STABILITY:
            stability = BeliefStability.EPISODE.value
        if stability == BeliefStability.RECURRING.value:
            distinct_days = {evidence_days.get(item) for item in evidence} - {None}
            if len(distinct_days) < 2:
                stability = BeliefStability.EPISODE.value
        conditions = raw.get("conditions") if isinstance(raw.get("conditions"), dict) else {}
        try:
            confidence = float(raw.get("confidence") or 0.5)
        except (TypeError, ValueError):
            confidence = 0.5
        subject = str(raw.get("subject") or "User").strip() or "User"
        key = belief_key_text(subject, attribute, conditions)
        vectors = await self.embedding.embed([key])
        embedding = vectors[0]
        now = datetime.now(timezone.utc)
        match = await self._nearest(scope, embedding)
        episode_ids = [episode_id] if episode_id else []

        if decision == BeliefDecision.COEXIST.value or match is None:
            await self.store.insert(
                _new_belief(
                    scope=scope,
                    subject=subject,
                    attribute=attribute,
                    value=value,
                    conditions=conditions,
                    stability=stability,
                    confidence=confidence,
                    evidence=evidence,
                    episode_ids=episode_ids,
                    embedding=embedding,
                    now=now,
                )
            )
            return True

        if decision in {BeliefDecision.CONFIRM.value, BeliefDecision.CREATE.value} and match.score is not None:
            if (match.score or 0.0) < self.match_threshold and decision == BeliefDecision.CREATE.value:
                await self.store.insert(
                    _new_belief(
                        scope=scope,
                        subject=subject,
                        attribute=attribute,
                        value=value,
                        conditions=conditions,
                        stability=stability,
                        confidence=confidence,
                        evidence=evidence,
                        episode_ids=episode_ids,
                        embedding=embedding,
                        now=now,
                    )
                )
                return True
            merged = list(dict.fromkeys([*(match.evidence_memory_ids or []), *evidence]))
            match.evidence_memory_ids = merged
            match.evidence_episode_ids = list(
                dict.fromkeys([*(match.evidence_episode_ids or []), *episode_ids])
            )
            match.support_count = max(len(merged), match.support_count, 1)
            match.confidence = max(match.confidence, confidence)
            match.value = value or match.value
            match.stability = _prefer_stability(match.stability, stability, match.support_count)
            match.attribute_embedding = embedding
            await self.store.update(match)
            return True

        if decision in {BeliefDecision.SUPERSEDE.value, BeliefDecision.REFINE.value}:
            match.status = BeliefStatus.SUPERSEDED.value
            match.valid_to = now
            await self.store.update(match)
            await self.store.insert(
                _new_belief(
                    scope=scope,
                    subject=subject,
                    attribute=attribute,
                    value=value,
                    conditions=conditions,
                    stability=stability,
                    confidence=confidence,
                    evidence=evidence,
                    episode_ids=episode_ids,
                    embedding=embedding,
                    now=now,
                )
            )
            return True

        return False

    async def _nearest(self, scope: MemoryScope, embedding: list[float]) -> StoredBelief | None:
        hits = await self.store.search_similar(
            embedding,
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            occupant_id=scope.occupant_id or "primary",
            top_k=1,
            active_only=True,
        )
        if not hits:
            return None
        top = hits[0]
        if (top.score or 0.0) < self.match_threshold:
            return None
        return top


def _index_facts(cluster: list[StoredMemory]) -> tuple[dict[str, str], list[dict[str, Any]]]:
    mapping: dict[str, str] = {}
    facts: list[dict[str, Any]] = []
    for idx, row in enumerate(cluster):
        fake = str(idx)
        mapping[fake] = row.id
        stamp = row.created_at
        occurred = (row.metadata or {}).get("occurred_at")
        facts.append(
            {
                "id": fake,
                "text": row.content,
                "occurred_at": occurred or (stamp.isoformat() if hasattr(stamp, "isoformat") else stamp),
            }
        )
    return mapping, facts


def _build_user_prompt(facts: list[dict[str, Any]]) -> str:
    return (
        "## Related facts\n"
        f"{json.dumps(facts, ensure_ascii=False, default=str)}\n\n"
        "# Output:"
    )


def _prefer_stability(old: str, new: str, support_count: int) -> str:
    rank = {
        BeliefStability.EPISODE.value: 0,
        BeliefStability.RECURRING.value: 1,
        BeliefStability.IDENTITY.value: 2,
    }
    chosen = new if rank.get(new, 0) >= rank.get(old, 0) else old
    if chosen == BeliefStability.RECURRING.value and support_count < 2:
        return BeliefStability.EPISODE.value
    return chosen


def _new_belief(
    *,
    scope: MemoryScope,
    subject: str,
    attribute: str,
    value: str,
    conditions: dict[str, Any],
    stability: str,
    confidence: float,
    evidence: list[str],
    episode_ids: list[str],
    embedding: list[float],
    now: datetime,
) -> StoredBelief:
    return StoredBelief(
        id=str(uuid.uuid4()),
        tenant_id=scope.tenant_id,
        user_id=scope.user_id,
        occupant_id=scope.occupant_id or "primary",
        subject=subject,
        attribute=attribute,
        value=value,
        conditions=dict(conditions or {}),
        stability=stability,
        status=BeliefStatus.ACTIVE.value,
        confidence=max(0.0, min(1.0, confidence)),
        support_count=max(1, len(set(evidence))),
        evidence_memory_ids=list(dict.fromkeys(evidence)),
        evidence_episode_ids=list(dict.fromkeys(episode_ids)),
        attribute_embedding=embedding,
        valid_from=now,
        created_at=now,
        updated_at=now,
    )
