"""Windows asyncio compatibility for psycopg async connections."""

from __future__ import annotations

import asyncio
import sys


def ensure_compatible_event_loop() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
