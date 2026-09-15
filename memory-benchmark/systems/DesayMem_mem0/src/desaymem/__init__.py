"""DesayMem_mem0 — privately deployable cloud long-term memory for cockpit agents.

This package is a source-level migration of Mem0 OSS core memory logic into
the `desaymem` namespace. It does not depend on the `mem0ai` package or the
Mem0 hosted cloud API.
"""

from desaymem.core.config import Settings
from desaymem.core.enums import MemoryType
from desaymem.core.exceptions import (
    ConfigurationError,
    DatabaseError,
    DesayMemError,
    EmbeddingError,
    LLMError,
    MemoryNotFoundError,
    ValidationError,
)
from desaymem.core.memory import DesayMemory
from desaymem.core.models import BeliefItem, MemoryItem, MemoryScope, ProfileView

__version__ = "0.1.0"

__all__ = [
    "DesayMemory",
    "MemoryItem",
    "MemoryScope",
    "MemoryType",
    "BeliefItem",
    "ProfileView",
    "Settings",
    "DesayMemError",
    "ConfigurationError",
    "DatabaseError",
    "EmbeddingError",
    "LLMError",
    "MemoryNotFoundError",
    "ValidationError",
    "__version__",
]
