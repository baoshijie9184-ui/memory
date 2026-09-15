"""Apply or check the DesayMem SQL migration. Never mutates schema on API traffic."""

from __future__ import annotations

import argparse
import asyncio
import selectors
import sys
from pathlib import Path

from psycopg import AsyncConnection

from desaymem.core.config import Settings
from desaymem.core.exceptions import ConfigurationError
from desaymem.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


def default_migrations_dir() -> Path:
    candidates = [
        Path.cwd() / "migrations",
        Path(__file__).resolve().parents[2] / "migrations",
    ]
    for path in candidates:
        if path.is_dir():
            return path
    return candidates[-1]


def default_migration_path() -> Path:
    directory = default_migrations_dir()
    initial = directory / "001_initial.sql"
    if initial.exists():
        return initial
    fallback = [
        Path.cwd() / "migrations" / "001_initial.sql",
        Path(__file__).resolve().parents[2] / "migrations" / "001_initial.sql",
        Path(__file__).resolve().parent / "sql" / "001_initial.sql",
    ]
    for path in fallback:
        if path.exists():
            return path
    return fallback[-1]


def _statements(sql: str) -> list[str]:
    """Split SQL into executable statements.

    Comments (lines starting with --) are stripped *before* splitting on
    ``;`` so that a semicolon inside a comment does not produce a phantom
    statement.
    """
    lines = [line for line in sql.splitlines() if line.strip() and not line.strip().startswith("--")]
    cleaned = "\n".join(lines)
    chunks: list[str] = []
    for raw in cleaned.split(";"):
        stmt = raw.strip()
        if stmt:
            chunks.append(stmt)
    return chunks


async def apply_migration(dsn: str, sql_path: Path) -> None:
    if not sql_path.exists():
        raise ConfigurationError(f"Migration file not found: {sql_path}")
    sql = sql_path.read_text(encoding="utf-8")
    async with await AsyncConnection.connect(dsn, autocommit=True) as conn:
        for stmt in _statements(sql):
            await conn.execute(stmt)
    logger.info("Applied migration %s", sql_path.name)


async def apply_all_migrations(dsn: str, migrations_dir: Path) -> None:
    paths = sorted(migrations_dir.glob("*.sql"))
    if not paths:
        raise ConfigurationError(f"No SQL migrations found in {migrations_dir}")
    for path in paths:
        await apply_migration(dsn, path)


async def check_ready(dsn: str, expected_dims: int) -> None:
    from desaymem.stores.pgvector import PgVectorStore

    store = PgVectorStore(dsn, embedding_dims=expected_dims)
    try:
        await store.check_schema(expected_dims)
    finally:
        await store.close()


def _run(coro):
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
    return asyncio.run(coro)


def main() -> None:
    parser = argparse.ArgumentParser(description="DesayMem schema migrate/check")
    parser.add_argument("action", choices=["apply", "check"], help="apply SQL or only verify")
    parser.add_argument("--dsn", default=None)
    parser.add_argument("--sql", default=None, help="apply a single SQL file instead of all migrations")
    args = parser.parse_args()
    settings = Settings()
    configure_logging(settings.log_level)
    dsn = args.dsn or settings.postgres_dsn
    if args.action == "apply":
        if args.sql:
            _run(apply_migration(dsn, Path(args.sql)))
        else:
            _run(apply_all_migrations(dsn, default_migrations_dir()))
    else:
        _run(check_ready(dsn, settings.embedding_dims))
        logger.info("Schema check passed")


if __name__ == "__main__":
    main()
