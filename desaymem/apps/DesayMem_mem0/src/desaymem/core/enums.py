"""Memory type enum migrated from mem0.configs.enums.MemoryType."""

from __future__ import annotations

from enum import Enum


class MemoryType(str, Enum):
    SEMANTIC = "semantic_memory"
    EPISODIC = "episodic_memory"
    PROCEDURAL = "procedural_memory"


class BeliefStability(str, Enum):
    EPISODE = "episode"
    RECURRING = "recurring"
    IDENTITY = "identity"


class BeliefStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"


class BeliefDecision(str, Enum):
    CREATE = "CREATE"
    CONFIRM = "CONFIRM"
    REFINE = "REFINE"
    COEXIST = "COEXIST"
    SUPERSEDE = "SUPERSEDE"
    NOOP = "NOOP"
