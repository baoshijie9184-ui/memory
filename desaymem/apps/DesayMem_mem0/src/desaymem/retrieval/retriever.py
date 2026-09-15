"""Nine-step search migrated from mem0.memory.main._search_vector_store.

  1. Preprocess query (lemmatize + extract entities)
  2. Embed query
  3. Semantic search (over-fetch)
  4. Keyword search
  5. Normalize BM25
  6. Entity boosts
  7. Build candidates
  8. score_and_rank (with temporal_score)
  9. Format results + conflict resolution (supersede filtering)
"""

from __future__ import annotations

from typing import Any

from desaymem.core.logging import get_logger
from desaymem.core.models import MemoryItem, MemoryScope, SearchFilters
from desaymem.extraction.entities import extract_entities
from desaymem.providers.embedding.base import EmbeddingProvider
from desaymem.retrieval.conflict_resolution import (
    ConflictResult,
    annotate_metadata,
    resolve_conflicts,
)
from desaymem.retrieval.lemmatization import lemmatize_for_bm25
from desaymem.retrieval.scoring import get_bm25_params, normalize_bm25, score_and_rank
from desaymem.stores.base import StoredMemory, VectorStore

logger = get_logger(__name__)


class MemoryRetriever:
    def __init__(
        self,
        store: VectorStore,
        embedding: EmbeddingProvider,
        *,
        threshold: float = 0.1,
        linker: Any | None = None,
    ) -> None:
        self.store = store
        self.embedding = embedding
        self.threshold = threshold
        self.linker = linker

    async def search(
        self,
        query: str,
        scope: MemoryScope,
        *,
        top_k: int = 5,
        filters: SearchFilters | dict[str, Any] | None = None,
        threshold: float | None = None,
        explain: bool = False,
    ) -> list[StoredMemory]:
        cut = self.threshold if threshold is None else threshold
        store_filters = _as_filters(filters)

        # Step 1: Preprocess query
        query_lemmatized = lemmatize_for_bm25(query)
        query_entities = extract_entities(query)

        # Step 2: Embed query
        vectors = await self.embedding.embed([query])

        # Step 3: Semantic search (over-fetch for scoring pool)
        internal_limit = max(top_k * 4, 60)
        semantic_results = await self.store.search(
            vectors[0],
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            top_k=internal_limit,
            filters=store_filters,
        )

        # Step 4: Keyword search (if store supports it)
        keyword_results = None
        keyword_search = getattr(self.store, "keyword_search", None)
        if callable(keyword_search):
            keyword_results = await keyword_search(
                query_lemmatized,
                tenant_id=scope.tenant_id,
                user_id=scope.user_id,
                top_k=internal_limit,
                filters=store_filters,
            )

        # Step 5: Compute BM25 scores from keyword results
        bm25_scores: dict[str, float] = {}
        if keyword_results:
            midpoint, steepness = get_bm25_params(query, lemmatized=query_lemmatized)
            for mem in keyword_results:
                raw_score = mem.score or 0.0
                if raw_score > 0:
                    bm25_scores[str(mem.id)] = normalize_bm25(raw_score, midpoint, steepness)

        # Step 6: Compute entity boosts
        entity_boosts: dict[str, float] = {}
        if self.linker is not None and query_entities:
            entity_boosts = await self.linker.boosts_for_query(query, scope)

        # Step 7: Build the union of semantic and keyword candidates.
        by_id = {row.id: row for row in semantic_results}
        for row in keyword_results or []:
            by_id.setdefault(row.id, row)
        semantic_scores = {row.id: row.score or 0.0 for row in semantic_results}
        candidates = [
            {"id": row.id, "score": semantic_scores.get(row.id, 0.0), "payload": row}
            for row in by_id.values()
        ]

        # Step 8: Score and rank (now includes temporal_score)
        ranked = score_and_rank(
            semantic_results=candidates,
            bm25_scores=bm25_scores,
            entity_boosts=entity_boosts,
            threshold=cut,
            top_k=len(candidates),
        )

        # Step 9a: Format results (fetch rows for ranked items)
        out: list[StoredMemory] = []
        for item in ranked:
            row = by_id.get(item["id"])
            if row is None:
                continue
            row.score = item["score"]
            t_score = item.get("temporal_score", 0.0)
            if explain:
                row.metadata = dict(row.metadata or {})
                row.metadata["score_details"] = {
                    "semantic_score": semantic_scores.get(item["id"], 0.0),
                    "bm25_score": bm25_scores.get(item["id"], 0.0),
                    "entity_boost": entity_boosts.get(item["id"], 0.0),
                    "temporal_score": t_score,
                    "final_score": item["score"],
                }
            out.append(row)

        # Step 9b: Conflict resolution — filter superseded memories
        conflict_result = resolve_conflicts(out, query=query)
        if conflict_result.filtered_ids:
            logger.info(
                "Conflict resolution filtered %d superseded memories",
                len(conflict_result.filtered_ids),
            )
            out = [row for row in out if row.id not in conflict_result.filtered_ids]

        # Step 9c: Annotate metadata on kept rows
        for row in out:
            enrichment = annotate_metadata(row, conflict_result)
            if enrichment:
                row.metadata = dict(row.metadata or {})
                row.metadata.update(enrichment)
                if explain:
                    row.metadata.setdefault("score_details", {})
                    row.metadata["score_details"]["conflict_resolution"] = {
                        "filtered_ids": list(conflict_result.filtered_ids),
                        "relations": conflict_result.relations,
                    }

        return out[:top_k]

    async def neighbors_for_extraction(
        self,
        query_text: str,
        scope: MemoryScope,
        top_k: int = 10,
    ) -> list[StoredMemory]:
        vectors = await self.embedding.embed([query_text])
        return await self.store.search(
            vectors[0],
            tenant_id=scope.tenant_id,
            user_id=scope.user_id,
            top_k=top_k,
            filters=None,
        )


def stored_to_item(row: StoredMemory, *, event: str | None = None) -> MemoryItem:
    metadata = dict(row.metadata or {})
    score_details = metadata.pop("score_details", None)
    return MemoryItem(
        id=row.id,
        content=row.content,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        vehicle_id=row.vehicle_id,
        occupant_id=row.occupant_id,
        session_id=row.session_id,
        scene=row.scene,
        source=row.source,
        memory_type=row.memory_type or "semantic_memory",
        content_hash=row.content_hash,
        text_lemmatized=row.text_lemmatized or None,
        score=row.score,
        score_details=score_details,
        event=event,
        metadata=metadata,
        created_at=row.created_at,
        updated_at=row.updated_at,
        embedding_model=row.embedding_model,
        embedding_dims=row.embedding_dims,
    )


def _as_filters(filters: SearchFilters | dict[str, Any] | None) -> dict[str, Any]:
    if filters is None:
        return {}
    if isinstance(filters, SearchFilters):
        return filters.as_store_filters()
    return {key: value for key, value in filters.items() if value not in (None, "")}
