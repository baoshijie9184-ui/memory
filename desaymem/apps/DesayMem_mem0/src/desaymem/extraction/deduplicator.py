"""Content-hash deduplication.

Migrated from mem0.memory.main._add_to_vector_store Phase 5
(OSS v2.0.18, commit 4fa48390). Mem0 uses MD5 of the extracted memory text
and skips items whose hash already exists in retrieved neighbors or in the
current batch.

Modifications:
- Isolated as a pure function
- Tenant/user uniqueness is additionally enforced by PostgreSQL
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from desaymem.core.logging import get_logger

logger = get_logger(__name__)


def memory_content_hash(text: str) -> str:
    """Match Mem0 OSS: hashlib.md5(text.encode()).hexdigest()."""
    return hashlib.md5(text.encode()).hexdigest()


def drop_duplicate_texts(
    items: list[dict],
    existing_hashes: Iterable[str],
) -> tuple[list[dict], int]:
    """Keep first occurrence of each hash; skip hashes already stored.

    Returns (kept_items_with_hash, skipped_count).
    """
    known = set(existing_hashes)
    seen: set[str] = set()
    kept: list[dict] = []
    skipped = 0
    for item in items:
        text = item.get("text") or ""
        if not text:
            skipped += 1
            continue
        digest = memory_content_hash(text)
        if digest in known or digest in seen:
            logger.debug("Skipping duplicate memory (hash match)")
            skipped += 1
            continue
        seen.add(digest)
        enriched = dict(item)
        enriched["content_hash"] = digest
        kept.append(enriched)
    return kept, skipped
