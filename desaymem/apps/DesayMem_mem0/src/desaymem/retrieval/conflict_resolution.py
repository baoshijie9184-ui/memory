"""Conflict resolution for semantically related memories.

When a user updates a preference (e.g. "I now prefer 26 degrees" after
previously saying "I like 22 degrees"), both memories coexist in the store
because the additive extraction pipeline only supports ADD operations.
This module identifies and filters superseded memories so the retriever
returns only the current value to downstream consumers (LLM, car HMI).

Design principles:
- **Conservative**: only act when an explicit update expression is detected.
  A vague topical overlap is NOT a conflict — it's a *RELATED_TO* link.
- **Same user only**: cross-user isolation is never relaxed.
- **Same memory_key**: two memories must share a normalised key (entity +
  attribute) to be considered conflicting. Different attributes of the same
  entity are distinct memories.
- **Time-ordered**: when conflict is detected, the newer memory *supersedes*
  the older one. The older one is filtered from search results but remains
  in the store for audit/history.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from desaymem.core.logging import get_logger
from desaymem.retrieval.scoring import _coerce_datetime

logger = get_logger(__name__)


# ── Enums ────────────────────────────────────────────────────────────────────

class RelationType(str, Enum):
    RELATED_TO = "RELATED_TO"
    SUPERSEDES = "SUPERSEDES"


# ── Explicit update expression detection ─────────────────────────────────────

# Chinese update patterns — must contain an explicit change-of-state cue
_UPDATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"从现在开始"),
    re.compile(r"以后(?:喜欢|偏好|设置|调|改|用|换)"),
    re.compile(r"改为"),
    re.compile(r"改成"),
    re.compile(r"换成"),
    re.compile(r"不再(?:喜欢|偏好|需要|用)"),
    re.compile(r"默认设置(?:为|到)"),
    re.compile(r"更新为"),
    re.compile(r"修改为"),
    re.compile(r"调整为"),
    re.compile(r"现在(?:喜欢|偏好|设置|调|改|用|换)"),
    re.compile(r"改用"),
    re.compile(r"切换(?:为|到)"),
]

# Value extraction: numbers with unit, or quoted strings
_VALUE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(\d+(?:\.\d+)?\s*(?:度|°|档|挡|级|分|分钟|km|公里|小时))", re.IGNORECASE),
    re.compile(r'''["']([^"']+)["']'''),
]

# Entity/attribute extraction: cockpit nouns + their attribute words
_COCKPIT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"空调"), "空调温度"),
    (re.compile(r"温度"), "空调温度"),
    # "度" with a number is almost always AC temperature in cockpit context
    (re.compile(r"\d+(?:\.\d+)?\s*度"), "空调温度"),
    (re.compile(r"座椅(?:加热)?"), "座椅加热"),
    (re.compile(r"音乐|歌|播放"), "音乐偏好"),
    (re.compile(r"导航|目的地|去"), "导航"),
    (re.compile(r"音量"), "音量"),
    (re.compile(r"后视镜"), "后视镜"),
    (re.compile(r"车窗"), "车窗"),
]

# Topic/attribute words for matching
_TOPIC_WORDS: list[str] = [
    "空调", "温度", "座椅", "加热", "音乐", "歌", "播放", "导航",
    "音量", "后视镜", "车窗", "偏好", "设置",
]


def detect_update_expression(text: str) -> bool:
    """Return True if *text* contains an explicit update/change expression."""
    return any(p.search(text) for p in _UPDATE_PATTERNS)


def extract_memory_key(text: str) -> str | None:
    """Extract a normalised memory_key from memory content.

    The key represents the "attribute slot" this memory fills, e.g.
    "空调温度" for both "喜欢22度" and "改为26度". Returns None if no
    recognisable cockpit attribute is found.
    """
    for pattern, key in _COCKPIT_PATTERNS:
        if pattern.search(text):
            return key
    return None


def extract_value(text: str) -> str | None:
    """Extract the primary value from memory content.

    Returns the first matching value: numeric+unit, quoted string, or None.
    """
    for pattern in _VALUE_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1) if m.groups() else m.group(0)
    return None


def extract_effective_at(text: str, created_at: Any = None) -> datetime | None:
    """Extract effective timestamp, falling back to created_at."""
    # We do not parse relative dates from text here because the extraction
    # prompt already grounds temporal references. The effective_at is the
    # memory's created_at — this is the most reliable signal we have without
    # re-running NLP on the stored text.
    return _coerce_datetime(created_at)


# ── Conflict resolution data structures ──────────────────────────────────────

@dataclass
class MemoryMeta:
    """Parsed metadata for a stored memory used in conflict resolution."""

    id: str
    content: str
    memory_key: str | None
    value: str | None
    is_update: bool
    effective_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None
    user_id: str
    tenant_id: str
    raw: Any = None


@dataclass
class ConflictResult:
    """Output of conflict resolution."""

    kept_ids: set[str] = field(default_factory=set)
    filtered_ids: set[str] = field(default_factory=set)
    relations: list[dict[str, Any]] = field(default_factory=list)


def _parse_meta(row: Any) -> MemoryMeta:
    """Build MemoryMeta from a StoredMemory or dict-like row."""
    content = getattr(row, "content", None) or (row.get("content") if isinstance(row, dict) else "")
    mem_id = str(getattr(row, "id", None) or (row.get("id") if isinstance(row, dict) else ""))
    user_id = getattr(row, "user_id", None) or (row.get("user_id", "") if isinstance(row, dict) else "")
    tenant_id = getattr(row, "tenant_id", None) or (row.get("tenant_id", "") if isinstance(row, dict) else "")
    created_at = getattr(row, "created_at", None)
    if created_at is None and isinstance(row, dict):
        created_at = row.get("created_at")
    updated_at = getattr(row, "updated_at", None)
    if updated_at is None and isinstance(row, dict):
        updated_at = row.get("updated_at")

    return MemoryMeta(
        id=mem_id,
        content=content,
        memory_key=extract_memory_key(content),
        value=extract_value(content),
        is_update=detect_update_expression(content),
        effective_at=extract_effective_at(content, created_at),
        created_at=_coerce_datetime(created_at),
        updated_at=_coerce_datetime(updated_at),
        user_id=user_id,
        tenant_id=tenant_id,
        raw=row,
    )


def _sort_key(meta: MemoryMeta) -> datetime:
    """Sort by effective_at → updated_at → created_at (latest first)."""
    return meta.effective_at or meta.updated_at or meta.created_at or datetime.min.replace(tzinfo=None)


def resolve_conflicts(
    rows: list[Any],
    *,
    query: str | None = None,
) -> ConflictResult:
    """Identify superseded memories among *rows*.

    Algorithm:
    1. Parse all rows into MemoryMeta.
    2. Group by (user_id, memory_key) — only same-key memories can conflict.
    3. Within each group, if at least one memory has *is_update=True*,
       identify whether values differ (genuine conflict) vs. same value (no-op).
    4. If values differ and one is an explicit update, the newer (by
       effective_at → updated_at → created_at) supersedes the older.
    5. Non-update memories that share the same key+value are *RELATED_TO*
       (not superseded).

    Returns ConflictResult with kept_ids, filtered_ids, and relation records.
    """
    result = ConflictResult()

    if not rows:
        return result

    metas = [_parse_meta(row) for row in rows]

    # Initially keep everything
    for m in metas:
        result.kept_ids.add(m.id)

    # Group by (user_id, memory_key) — only same user + same key can conflict
    groups: dict[tuple[str, str], list[MemoryMeta]] = {}
    for m in metas:
        if m.memory_key is None:
            continue
        group_key = (m.user_id, m.memory_key)
        groups.setdefault(group_key, []).append(m)

    for (uid, mem_key), group in groups.items():
        if len(group) < 2:
            continue

        # Sort newest first
        group.sort(key=_sort_key, reverse=True)

        # Find memories with explicit update expressions
        update_metas = [m for m in group if m.is_update]

        if not update_metas:
            # No explicit update → all are RELATED_TO, none filtered
            for i, m in enumerate(group):
                for older in group[i + 1:]:
                    result.relations.append({
                        "source_id": m.id,
                        "target_id": older.id,
                        "relation_type": RelationType.RELATED_TO.value,
                        "memory_key": mem_key,
                    })
            continue

        # We have at least one explicit update.
        # The newest update supersedes all older memories with DIFFERENT values.
        newest_update = update_metas[0]  # already sorted newest-first
        for m in group:
            if m.id == newest_update.id:
                continue
            # Only supersede if values differ (genuine conflict)
            if m.value is not None and newest_update.value is not None and m.value != newest_update.value:
                result.filtered_ids.add(m.id)
                result.kept_ids.discard(m.id)
                result.relations.append({
                    "source_id": newest_update.id,
                    "target_id": m.id,
                    "relation_type": RelationType.SUPERSEDES.value,
                    "memory_key": mem_key,
                    "reason": "explicit_update_with_different_value",
                })
            elif m.value is not None and newest_update.value is not None and m.value == newest_update.value:
                # Same value — RELATED_TO, not superseded
                result.relations.append({
                    "source_id": newest_update.id,
                    "target_id": m.id,
                    "relation_type": RelationType.RELATED_TO.value,
                    "memory_key": mem_key,
                })

    return result


def annotate_metadata(
    row: Any,
    conflict_result: ConflictResult,
) -> dict[str, Any]:
    """Build enrichment metadata for a kept memory.

    Returns a dict with memory_key, effective_at, is_current, relation_type,
    supersedes — to be merged into row.metadata by the retriever.
    """
    meta = _parse_meta(row)
    enrichment: dict[str, Any] = {}

    if meta.memory_key:
        enrichment["memory_key"] = meta.memory_key

    if meta.effective_at:
        enrichment["effective_at"] = meta.effective_at.isoformat()

    # Determine if this memory is the current value for its key
    is_current = True
    supersedes: list[str] = []
    relation_type = None

    for rel in conflict_result.relations:
        if rel["source_id"] == meta.id and rel["relation_type"] == RelationType.SUPERSEDES.value:
            supersedes.append(rel["target_id"])
            relation_type = RelationType.SUPERSEDES.value
        elif rel["target_id"] == meta.id and rel["relation_type"] == RelationType.SUPERSEDES.value:
            # This memory is superseded by another — should not happen for kept rows
            is_current = False
            relation_type = "SUPERSEDED_BY"

    enrichment["is_current"] = is_current
    if relation_type:
        enrichment["relation_type"] = relation_type
    if supersedes:
        enrichment["supersedes"] = supersedes

    return enrichment
