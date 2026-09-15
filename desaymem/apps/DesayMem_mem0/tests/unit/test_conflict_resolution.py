"""Unit tests for temporal scoring and conflict resolution.

These tests do NOT require a live PostgreSQL/embedding backend — they test
the pure logic in scoring.py and conflict_resolution.py.

For integration tests that exercise the full retriever pipeline, see
test_mem0_pipeline.py and test_mem0_cockpit_flow.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from desaymem.retrieval.conflict_resolution import (
    RelationType,
    annotate_metadata,
    detect_update_expression,
    extract_memory_key,
    extract_value,
    resolve_conflicts,
)
from desaymem.retrieval.scoring import (
    TEMPORAL_WEIGHT,
    score_and_rank,
    temporal_score,
)
from desaymem.stores.base import StoredMemory


# ─────────────────────────────────────────────────────────────────────────────
# temporal_score tests
# ─────────────────────────────────────────────────────────────────────────────


class TestTemporalScore:
    def test_today_gets_full_weight(self):
        now = datetime.now(timezone.utc)
        score = temporal_score(now, now=now)
        assert score == pytest.approx(TEMPORAL_WEIGHT, rel=1e-6)

    def test_30_days_ago_approximately_half(self):
        now = datetime.now(timezone.utc)
        old = now - timedelta(days=30)
        score = temporal_score(old, now=now)
        # A configured 30-day half-life retains exactly half the bonus.
        expected = TEMPORAL_WEIGHT * 0.5
        assert score == pytest.approx(expected, rel=0.01)

    def test_none_returns_zero(self):
        assert temporal_score(None) == 0.0

    def test_iso_string_accepted(self):
        now = datetime.now(timezone.utc)
        iso = now.isoformat()
        score = temporal_score(iso, now=now)
        assert score == pytest.approx(TEMPORAL_WEIGHT, rel=1e-6)

    def test_naive_datetime_treated_as_utc(self):
        now = datetime.now(timezone.utc)
        naive = now.replace(tzinfo=None)
        score = temporal_score(naive, now=now)
        assert score == pytest.approx(TEMPORAL_WEIGHT, rel=1e-6)

    def test_old_memory_gets_small_but_nonzero(self):
        now = datetime.now(timezone.utc)
        very_old = now - timedelta(days=365)
        score = temporal_score(very_old, now=now)
        assert 0.0 < score < 0.01  # very small but nonzero


# ─────────────────────────────────────────────────────────────────────────────
# score_and_rank with temporal_score tests
# ─────────────────────────────────────────────────────────────────────────────


class TestScoreAndRankTemporal:
    def test_newer_gets_slight_boost_when_semantic_equal(self):
        """When semantic scores are equal, newer memory ranks higher."""
        now = datetime.now(timezone.utc)
        recent = now - timedelta(days=1)
        old = now - timedelta(days=100)

        ranked = score_and_rank(
            semantic_results=[
                {"id": "old", "score": 0.8, "payload": StoredMemory(id="old", content="old", tenant_id="t", user_id="u", created_at=old)},
                {"id": "recent", "score": 0.8, "payload": StoredMemory(id="recent", content="recent", tenant_id="t", user_id="u", created_at=recent)},
            ],
            bm25_scores={},
            entity_boosts={},
            threshold=0.1,
            top_k=2,
            now=now,
        )
        assert ranked[0]["id"] == "recent"

    def test_high_semantic_old_beats_low_semantic_new(self):
        """Temporal boost must NOT override strong semantic relevance."""
        now = datetime.now(timezone.utc)
        recent = now - timedelta(days=1)
        old = now - timedelta(days=100)

        ranked = score_and_rank(
            semantic_results=[
                {"id": "old_relevant", "score": 0.95, "payload": StoredMemory(id="old_relevant", content="relevant", tenant_id="t", user_id="u", created_at=old)},
                {"id": "new_irrelevant", "score": 0.30, "payload": StoredMemory(id="new_irrelevant", content="irrelevant", tenant_id="t", user_id="u", created_at=recent)},
            ],
            bm25_scores={},
            entity_boosts={},
            threshold=0.1,
            top_k=2,
            now=now,
        )
        # Old but highly relevant should still rank first
        assert ranked[0]["id"] == "old_relevant"

    def test_temporal_score_in_output(self):
        now = datetime.now(timezone.utc)
        recent = now - timedelta(days=1)

        ranked = score_and_rank(
            semantic_results=[
                {"id": "a", "score": 0.8, "payload": StoredMemory(id="a", content="test", tenant_id="t", user_id="u", created_at=recent)},
            ],
            bm25_scores={},
            entity_boosts={},
            threshold=0.1,
            top_k=1,
            now=now,
        )
        assert "temporal_score" in ranked[0]
        assert ranked[0]["temporal_score"] > 0.0

    def test_backward_compat_no_created_at(self):
        """Memories without created_at still score correctly (temporal=0)."""
        ranked = score_and_rank(
            semantic_results=[
                {"id": "a", "score": 0.8, "payload": StoredMemory(id="a", content="test", tenant_id="t", user_id="u")},
            ],
            bm25_scores={},
            entity_boosts={},
            threshold=0.1,
            top_k=1,
        )
        assert ranked[0]["temporal_score"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# detect_update_expression tests
# ─────────────────────────────────────────────────────────────────────────────


class TestDetectUpdateExpression:
    @pytest.mark.parametrize("text", [
        "用户从现在开始将空调设置为26度",
        "以后喜欢26度",
        "改为26度",
        "改成26度",
        "换成26度",
        "不再喜欢22度",
        "默认设置为26度",
        "现在喜欢26度",
        "改用26度",
    ])
    def test_explicit_update_detected(self, text):
        assert detect_update_expression(text) is True

    @pytest.mark.parametrize("text", [
        "我开车的时候喜欢把空调调到22度",
        "帮我把空调打开",
        "导航去上海迪士尼",
        "播放周杰伦的歌",
        "今天天气不错",
    ])
    def test_no_update_expression(self, text):
        assert detect_update_expression(text) is False


# ─────────────────────────────────────────────────────────────────────────────
# extract_memory_key / extract_value tests
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractKeyAndValue:
    def test_acpu_key(self):
        assert extract_memory_key("用户喜欢空调22度") == "空调温度"

    def test_seat_key(self):
        assert extract_memory_key("座椅加热3挡") == "座椅加热"

    def test_music_key(self):
        assert extract_memory_key("播放周杰伦的歌") == "音乐偏好"

    def test_no_key(self):
        assert extract_memory_key("今天天气很好") is None

    def test_numeric_value(self):
        assert extract_value("空调调到22度") is not None
        assert "22" in extract_value("空调调到22度")

    def test_no_value(self):
        assert extract_value("帮我把空调打开") is None


# ─────────────────────────────────────────────────────────────────────────────
# resolve_conflicts tests
# ─────────────────────────────────────────────────────────────────────────────


def _make_memory(
    mem_id: str,
    content: str,
    *,
    user_id: str = "user_001",
    tenant_id: str = "tenant_001",
    created_at: datetime | None = None,
) -> StoredMemory:
    return StoredMemory(
        id=mem_id,
        content=content,
        tenant_id=tenant_id,
        user_id=user_id,
        created_at=created_at or datetime.now(timezone.utc),
    )


class TestResolveConflicts:
    def test_22_to_26_explicit_update_supersedes_old(self):
        """Core scenario: 22° stored first, then 26° with explicit update.
        Only 26° should survive."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=10)
        new_time = now

        rows = [
            _make_memory("mem_22", "用户喜欢空调22度", created_at=old_time),
            _make_memory("mem_26", "用户从现在开始将空调设置为26度", created_at=new_time),
        ]
        result = resolve_conflicts(rows)

        assert "mem_22" in result.filtered_ids
        assert "mem_22" not in result.kept_ids
        assert "mem_26" in result.kept_ids

        # Verify relation type
        supersede_rels = [r for r in result.relations if r["relation_type"] == RelationType.SUPERSEDES.value]
        assert len(supersede_rels) == 1
        assert supersede_rels[0]["source_id"] == "mem_26"
        assert supersede_rels[0]["target_id"] == "mem_22"

    def test_related_not_superseded(self):
        """Two memories about the same entity but different aspects → RELATED_TO."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=10)
        new_time = now

        rows = [
            _make_memory("mem_22", "用户喜欢空调22度", created_at=old_time),
            _make_memory("mem_nav", "用户从现在开始将导航默认设置为最快路线", created_at=new_time),
        ]
        result = resolve_conflicts(rows)

        # Different memory_keys → no conflict at all, both kept
        assert not result.filtered_ids
        assert "mem_22" in result.kept_ids
        assert "mem_nav" in result.kept_ids

    def test_same_value_no_supersede(self):
        """If update expression has same value as old → RELATED_TO, not SUPERSEDES."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=10)
        new_time = now

        rows = [
            _make_memory("mem_old_22", "用户喜欢空调22度", created_at=old_time),
            _make_memory("mem_new_22", "用户从现在开始将空调设置为22度", created_at=new_time),
        ]
        result = resolve_conflicts(rows)

        assert not result.filtered_ids
        related = [r for r in result.relations if r["relation_type"] == RelationType.RELATED_TO.value]
        assert len(related) >= 1

    def test_no_conflict_preserves_original_order(self):
        """When no conflicts, filtered_ids is empty — original ranking untouched."""
        now = datetime.now(timezone.utc)
        rows = [
            _make_memory("a", "导航去上海迪士尼", created_at=now),
            _make_memory("b", "播放周杰伦的歌", created_at=now),
        ]
        result = resolve_conflicts(rows)
        assert not result.filtered_ids
        assert len(result.kept_ids) == 2

    def test_multi_user_isolation(self):
        """Conflicts are only detected within same user_id."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=10)
        new_time = now

        rows = [
            _make_memory("user_a_22", "用户喜欢空调22度", user_id="user_a", created_at=old_time),
            _make_memory("user_b_26", "用户从现在开始将空调设置为26度", user_id="user_b", created_at=new_time),
        ]
        result = resolve_conflicts(rows)

        # Different users → no cross-user conflict
        assert not result.filtered_ids
        assert "user_a_22" in result.kept_ids
        assert "user_b_26" in result.kept_ids

    def test_no_explicit_update_no_supersede(self):
        """Two memories with same key but no update expression → RELATED_TO only."""
        now = datetime.now(timezone.utc)
        old_time = now - timedelta(days=10)
        new_time = now

        rows = [
            _make_memory("old", "用户喜欢空调22度", created_at=old_time),
            _make_memory("new", "用户也偏好空调26度", created_at=new_time),
        ]
        result = resolve_conflicts(rows)

        assert not result.filtered_ids
        related = [r for r in result.relations if r["relation_type"] == RelationType.RELATED_TO.value]
        assert len(related) >= 1

    def test_three_memories_chain(self):
        """22° → 26° → 24°: only the latest (24°) survives."""
        now = datetime.now(timezone.utc)
        rows = [
            _make_memory("m22", "用户喜欢空调22度", created_at=now - timedelta(days=20)),
            _make_memory("m26", "用户改为26度", created_at=now - timedelta(days=10)),
            _make_memory("m24", "用户从现在开始将空调设置为24度", created_at=now),
        ]
        result = resolve_conflicts(rows)

        # m24 supersedes m26, m26 supersedes m22 — but since m26 is also filtered,
        # only m24 remains. m24 should supersede both m22 and m26.
        assert "m24" in result.kept_ids
        assert "m26" in result.filtered_ids
        # m22 may or may not be explicitly filtered depending on whether m26
        # (which is itself superseded) participates in the comparison.
        # The key invariant: only m24 is kept.
        assert "m22" not in result.kept_ids or "m22" in result.filtered_ids


# ─────────────────────────────────────────────────────────────────────────────
# annotate_metadata tests
# ─────────────────────────────────────────────────────────────────────────────


class TestAnnotateMetadata:
    def test_enrichment_fields(self):
        now = datetime.now(timezone.utc)
        rows = [
            _make_memory("m22", "用户喜欢空调22度", created_at=now - timedelta(days=10)),
            _make_memory("m26", "用户从现在开始将空调设置为26度", created_at=now),
        ]
        result = resolve_conflicts(rows)

        # Annotate the kept memory (m26)
        m26 = next(r for r in rows if r.id == "m26")
        enrichment = annotate_metadata(m26, result)

        assert enrichment["memory_key"] == "空调温度"
        assert enrichment["is_current"] is True
        assert enrichment["relation_type"] == RelationType.SUPERSEDES.value
        assert "m22" in enrichment["supersedes"]
        assert "effective_at" in enrichment

    def test_no_enrichment_for_unrecognized(self):
        row = _make_memory("x", "今天天气很好")
        result = resolve_conflicts([row])
        enrichment = annotate_metadata(row, result)
        # memory_key is None, no enrichment except is_current
        assert enrichment.get("memory_key") is None
        assert enrichment["is_current"] is True
