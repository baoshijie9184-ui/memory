"""Environment-driven configuration for DesayMem_mem0."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "DesayMem_mem0"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"

    postgres_dsn: str = "postgresql://desaymem:change_me@localhost:5432/desaymem"
    postgres_pool_min: int = 1
    postgres_pool_max: int = 10
    migration_file: str = "migrations/001_initial.sql"

    llm_model: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_temperature: float = 0.1
    llm_max_tokens: int = 2000
    llm_top_p: float = 0.1

    embedding_model: str = ""
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    embedding_dims: int = 1024

    search_threshold: float = 0.1
    search_existing_top_k: int = 10
    default_top_k: int = 5
    get_all_limit: int = 100
    custom_instructions: str = ""
    use_input_language: bool = True
    last_k_messages: int = 10
    entity_match_threshold: float = 0.95
    entity_boost_min_similarity: float = 0.5
    enable_entity_store: bool = True
    history_db_path: str = "history.db"

    enable_episodes: bool = True
    enable_profile: bool = True
    enable_rerank: bool = True
    profile_match_threshold: float = 0.82
    profile_neighbor_top_k: int = 12
    rerank_candidate_limit: int = 32

    @field_validator("embedding_dims")
    @classmethod
    def _dims(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("EMBEDDING_DIMS must be a positive integer")
        return value

    @field_validator("log_level")
    @classmethod
    def _log_level(cls, value: str) -> str:
        return value.upper()

    def require_runtime_secrets(self, *, require_llm: bool = True) -> None:
        from desaymem.core.exceptions import ConfigurationError

        if not self.postgres_dsn:
            raise ConfigurationError("POSTGRES_DSN is required")
        if require_llm and not self.llm_api_key:
            raise ConfigurationError("LLM_API_KEY is required for live extraction")
        if require_llm and not self.llm_model:
            raise ConfigurationError("LLM_MODEL is required for live extraction")
        if require_llm and not self.embedding_api_key:
            raise ConfigurationError("EMBEDDING_API_KEY is required")
        if require_llm and not self.embedding_model:
            raise ConfigurationError("EMBEDDING_MODEL is required")

    def migration_path(self, project_root: Path | None = None) -> Path:
        root = project_root or Path.cwd()
        return (root / self.migration_file).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
