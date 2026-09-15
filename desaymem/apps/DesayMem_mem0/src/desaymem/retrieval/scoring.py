"""Retrieval scoring.

`distance_to_score` matches mem0.vector_stores.pgvector.PGVector.search:
cosine distance is converted with `max(0.0, 1.0 - distance)`.

`score_and_rank` is the Mem0 hybrid combiner (semantic + BM25 + entity
boost + temporal recency). Threshold gates the semantic score first; BM25
only boosts candidates already returned by vector search. Temporal score
provides a small recency bonus that acts as a tie-breaker, not an override.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

ENTITY_BOOST_WEIGHT = 0.5
TEMPORAL_WEIGHT = 0.15
TEMPORAL_HALF_LIFE_DAYS = 30.0


def get_bm25_params(query: str, *, lemmatized: str | None = None) -> tuple[float, float]:
    """Mem0 query-length-adaptive sigmoid parameters for BM25."""
    if lemmatized is None:
        from desaymem.retrieval.lemmatization import lemmatize_for_bm25

        lemmatized = lemmatize_for_bm25(query)
    num_terms = len(lemmatized.split()) if lemmatized else 1
    if num_terms <= 3:
        return 5.0, 0.7
    if num_terms <= 6:
        return 7.0, 0.6
    if num_terms <= 9:
        return 9.0, 0.5
    if num_terms <= 15:
        return 10.0, 0.5
    return 12.0, 0.5


def normalize_bm25(raw_score: float, midpoint: float, steepness: float) -> float:
    """Normalize BM25 score to [0, 1] using logistic sigmoid (Mem0)."""
    return 1.0 / (1.0 + math.exp(-steepness * (raw_score - midpoint)))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)


def distance_to_score(distance: float) -> float:
    """Convert cosine distance to a similarity score in [0, 1]."""
    return max(0.0, 1.0 - float(distance))


def apply_threshold(items: list, threshold: float) -> list:
    kept = []
    for item in items:
        score = getattr(item, "score", None)
        if score is None:
            score = item.get("score") if isinstance(item, dict) else 0.0
        if (score or 0.0) >= threshold:
            kept.append(item)
    return kept


def _coerce_datetime(value: Any) -> datetime | None:
    """Accept datetime, ISO string, or None; return datetime or None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return None


def temporal_score(
    created_at: Any,
    now: datetime | None = None,
    *,
    weight: float = TEMPORAL_WEIGHT,
    half_life_days: float = TEMPORAL_HALF_LIFE_DAYS,
) -> float:
    """Exponential-decay recency bonus in [0, weight].

    A memory created today gets *weight*; a memory older than *half_life_days*
    gets roughly half of that. The function degrades gracefully so that a
    highly-relevant old memory is not pushed below a marginally-relevant new one.

    Returns 0.0 if no timestamp is available — this preserves backward
    compatibility with stores that don't populate *created_at*.
    """
    ts = _coerce_datetime(created_at)
    if ts is None:
        return 0.0
    ref = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    delta_days = max((ref - ts).total_seconds() / 86400.0, 0.0)
    return weight * math.exp(-math.log(2.0) * delta_days / half_life_days)


def score_and_rank(
    semantic_results: list[dict[str, Any]],
    bm25_scores: dict[str, float] | None,
    entity_boosts: dict[str, float] | None,
    threshold: float,
    top_k: int,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Hybrid scoring: (semantic + bm25 + entity + temporal) / max_possible.

    The *temporal* component is a gentle recency tie-breaker (default weight
    0.15, capped). It is intentionally small enough that semantic relevance
    still dominates. Conflict resolution (supersede filtering) is handled
    downstream in the retriever, not here.
    """
    bm25_scores = bm25_scores or {}
    entity_boosts = entity_boosts or {}
    has_bm25 = bool(bm25_scores)
    has_entity = bool(entity_boosts)
    max_possible = 1.0
    if has_bm25:
        max_possible += 1.0
    if has_entity:
        max_possible += ENTITY_BOOST_WEIGHT
    max_possible += TEMPORAL_WEIGHT

    ref_now = now or datetime.now(timezone.utc)

    scored: list[dict[str, Any]] = []
    for result in semantic_results:
        mem_id = result.get("id")
        if mem_id is None:
            continue
        semantic_score = result.get("score") or 0.0
        mem_id_str = str(mem_id)
        bm25_score = bm25_scores.get(mem_id_str, 0.0)
        if semantic_score < threshold and bm25_score <= 0.0:
            continue
        entity_boost = entity_boosts.get(mem_id_str, 0.0)

        payload = result.get("payload")
        created_at = None
        if payload is not None:
            created_at = getattr(payload, "created_at", None)
            if created_at is None and isinstance(payload, dict):
                created_at = payload.get("created_at")
        if created_at is None:
            created_at = result.get("created_at")

        t_score = temporal_score(created_at, now=ref_now)

        raw_combined = semantic_score + bm25_score + entity_boost + t_score
        combined = min(raw_combined / max_possible, 1.0)
        scored.append(
            {
                "id": mem_id_str,
                "score": combined,
                "payload": result.get("payload"),
                "temporal_score": t_score,
            }
        )
    scored.sort(key=lambda row: row["score"], reverse=True)
    return scored[:top_k]
