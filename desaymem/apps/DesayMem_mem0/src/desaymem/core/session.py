"""Session-scope key used by last-k message storage.

Migrated from mem0.memory.main._build_session_scope. DesayMem adds tenant_id
and cockpit session/occupant fields; Mem0 used user_id/agent_id/run_id.
"""

from __future__ import annotations

from typing import Any

from desaymem.core.models import MemoryScope


def escape_scope_value(value: Any) -> str:
    return str(value).replace("%", "%25").replace("&", "%26").replace("=", "%3D")


def build_session_scope(scope: MemoryScope) -> str:
    parts: list[str] = []
    for key in ("tenant_id", "user_id", "occupant_id", "session_id"):
        value = getattr(scope, key, "") or ""
        if value:
            parts.append(f"{key}={escape_scope_value(value)}")
    return "&".join(parts)
