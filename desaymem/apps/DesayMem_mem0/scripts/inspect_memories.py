"""Inspect stored memories for a tenant/user."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from desaymem.core.config import Settings
from desaymem.core.logging import configure_logging
from desaymem.stores.pgvector import PgVectorStore


async def main_async(tenant_id: str, user_id: str, limit: int) -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    store = PgVectorStore(settings.postgres_dsn, embedding_dims=settings.embedding_dims)
    await store.check_schema(settings.embedding_dims)
    rows = await store.list(tenant_id=tenant_id, user_id=user_id, limit=limit)
    payload = [
        {
            "id": row.id,
            "content": row.content,
            "tenant_id": row.tenant_id,
            "user_id": row.user_id,
            "vehicle_id": row.vehicle_id,
            "scene": row.scene,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    await store.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default="default")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    asyncio.run(main_async(args.tenant_id, args.user_id, args.limit))


if __name__ == "__main__":
    main()
