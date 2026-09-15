#!/usr/bin/env python3
"""One-shot export of existing database contents to JSON mirror files.

Exports every table of the three PostgreSQL databases (bench_desaymem,
bench_desaymem_test, desaymem) and the SQLite history databases
(history.db, history_test.db) into json_mirror/<db>/<table>.json so the
data can be inspected in an editor.

Run from the project root:

    python scripts/export_db_to_json.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from desaymem.core.config import get_settings  # noqa: E402
from desaymem.stores.json_mirror import (  # noqa: E402
    SQLITE_TABLES,
    db_name_from_dsn,
    export_pg_database,
    mirror_sqlite_tables,
)

PG_HOST = "127.0.0.1"
PG_PORT = 20143
PG_USER = "desaymem"
PG_DATABASES = ("bench_desaymem", "bench_desaymem_test", "desaymem")

SQLITE_FILES = ("history.db", "history_test.db")


def main() -> int:
    settings = get_settings()
    history_dir = Path(settings.history_db_path).expanduser().resolve().parent
    print(f"Mirror root: {history_dir / 'json_mirror'}")
    print(f"Primary DSN database: {db_name_from_dsn(settings.postgres_dsn)}")

    failed = False

    for database in PG_DATABASES:
        dsn = f"postgresql://{PG_USER}@{PG_HOST}:{PG_PORT}/{database}"
        print(f"\nPostgreSQL {database}:")
        try:
            export_pg_database(dsn, database)
        except Exception as exc:
            failed = True
            print(f"  ERROR: {exc}")

    for filename in SQLITE_FILES:
        db_path = history_dir / filename
        print(f"\nSQLite {db_path}:")
        if not db_path.exists():
            print("  file not found, skipped")
            continue
        try:
            mirror_sqlite_tables(str(db_path), SQLITE_TABLES)
            print(f"  {filename}: exported {', '.join(SQLITE_TABLES)} -> json_mirror/{db_path.stem}/")
        except Exception as exc:
            failed = True
            print(f"  ERROR: {exc}")

    if failed:
        print("\nSome exports failed — see errors above.")
        return 1
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
