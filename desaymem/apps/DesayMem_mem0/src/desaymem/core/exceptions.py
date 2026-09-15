"""Structured exceptions for DesayMem.

Adapted from mem0/exceptions.py (Apache-2.0, Mem0 OSS v2.0.18,
commit 4fa483907704735ba0bec030e3c946ee1614b50e). Platform/quota/rate-limit
classes that exist only for the hosted API were not migrated.
"""

from __future__ import annotations

from typing import Any


class DesayMemError(Exception):
    """Base exception for all DesayMem errors."""

    def __init__(
        self,
        message: str,
        error_code: str,
        details: dict[str, Any] | None = None,
        suggestion: str | None = None,
        debug_info: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.error_code = error_code
        self.details = details or {}
        self.suggestion = suggestion
        self.debug_info = debug_info or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "error": self.__class__.__name__,
            "message": self.message,
            "error_code": self.error_code,
        }
        if self.suggestion:
            payload["suggestion"] = self.suggestion
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(DesayMemError):
    def __init__(
        self,
        message: str,
        error_code: str = "VAL_001",
        details: dict[str, Any] | None = None,
        suggestion: str = "Check the request fields and try again",
        debug_info: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, error_code, details, suggestion, debug_info)


class MemoryNotFoundError(DesayMemError):
    def __init__(
        self,
        message: str = "Memory not found",
        error_code: str = "MEM_404",
        details: dict[str, Any] | None = None,
        suggestion: str = "Confirm the memory id belongs to this tenant and user",
        debug_info: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, error_code, details, suggestion, debug_info)


class ConfigurationError(DesayMemError):
    def __init__(
        self,
        message: str,
        error_code: str = "CFG_001",
        details: dict[str, Any] | None = None,
        suggestion: str = "Check environment variables and applied SQL migrations",
        debug_info: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, error_code, details, suggestion, debug_info)


class DatabaseError(DesayMemError):
    def __init__(
        self,
        message: str,
        error_code: str = "DB_001",
        details: dict[str, Any] | None = None,
        suggestion: str = "Check PostgreSQL connectivity and pgvector extension",
        debug_info: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, error_code, details, suggestion, debug_info)


class EmbeddingError(DesayMemError):
    def __init__(
        self,
        message: str,
        error_code: str = "EMBED_001",
        details: dict[str, Any] | None = None,
        suggestion: str = "Check embedding model, API key, and configured dimension",
        debug_info: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, error_code, details, suggestion, debug_info)


class LLMError(DesayMemError):
    def __init__(
        self,
        message: str,
        error_code: str = "LLM_001",
        details: dict[str, Any] | None = None,
        suggestion: str = "Check LLM configuration and API key",
        debug_info: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, error_code, details, suggestion, debug_info)


class VectorStoreError(DesayMemError):
    def __init__(
        self,
        message: str,
        error_code: str = "VECTOR_001",
        details: dict[str, Any] | None = None,
        suggestion: str = "Check vector store configuration and connection",
        debug_info: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, error_code, details, suggestion, debug_info)
