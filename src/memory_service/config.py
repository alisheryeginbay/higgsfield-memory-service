"""Runtime configuration, env-driven via pydantic-settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LLM + embeddings -----------------------------------------------
    anthropic_api_key: str = ""
    voyage_api_key: str = ""

    anthropic_model_fast: str = "claude-haiku-4-5"
    anthropic_model_smart: str = "claude-sonnet-4-6"
    voyage_embed_model: str = "voyage-4-lite"
    voyage_embed_dim: int = 1024

    # --- extraction -----------------------------------------------------
    # Memories below this confidence are dropped on insert.
    extraction_confidence_floor: float = 0.4
    # Cap on output tokens for the per-turn extraction call.
    extraction_max_tokens: int = 1024

    # --- recall ---------------------------------------------------------
    # Max memories to surface via /recall after RRF (top-K).
    recall_top_k: int = 10

    # --- database -------------------------------------------------------
    # Plain libpq DSN — asyncpg accepts this directly. Tools that want a
    # SQLAlchemy URL (Alembic) call `sqlalchemy_url` below.
    database_url: str = "postgresql://memory:memory@db:5432/memory"

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgresql+asyncpg://"):
            return url
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url

    # --- auth (optional) ------------------------------------------------
    memory_auth_token: str = ""

    # --- server ---------------------------------------------------------
    log_level: str = Field(default="INFO")


@lru_cache
def get_settings() -> Settings:
    return Settings()
