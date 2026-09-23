"""Central configuration and environment handling."""

import os
from typing import Optional


def _require_env(name: str) -> str:
    """Return env var or raise with redacted placeholder."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _optional_env(name: str) -> Optional[str]:
    """Return env var or None."""
    return os.environ.get(name)


# OpenRouter
OPENROUTER_API_KEY: str = _require_env("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL: str = _optional_env("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"

# Models
CHAT_MODEL: str = _require_env("CHAT_MODEL")
CHAT_FALLBACK_MODEL: str = _require_env("CHAT_FALLBACK_MODEL")
EMBEDDING_MODEL: str = _require_env("EMBEDDING_MODEL")

# FastAPI
API_BASE_URL: str = _optional_env("API_BASE_URL") or "http://127.0.0.1:8000"

# Langfuse (bonus)
LANGFUSE_PUBLIC_KEY: Optional[str] = _optional_env("LANGFUSE_PUBLIC_KEY")
LANGFUSE_SECRET_KEY: Optional[str] = _optional_env("LANGFUSE_SECRET_KEY")
LANGFUSE_HOST: str = _optional_env("LANGFUSE_HOST") or "https://cloud.langfuse.com"

# Database
def get_database_url() -> str:
    """Return DATABASE_URL from env."""
    return _optional_env("DATABASE_URL") or "sqlite:///./neova.db"


# Clock mode
CLOCK_MODE: str = _optional_env("CLOCK_MODE") or "live"
DEMO_TIMESTAMP: Optional[str] = _optional_env("DEMO_TIMESTAMP")

# Demo isolation (internal, not user-typed)
DEMO_SESSION_SECRET: str = _require_env("DEMO_SESSION_SECRET")
