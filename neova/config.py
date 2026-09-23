"""Central configuration.

All settings are read lazily at call time so foundation-only operations
(health, fixture seed, offline tests) work without environment variables.
Future model operations must request credentials explicitly; errors name
the missing variable, never its value.
"""

import os
from typing import Optional

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_DATABASE_URL = "sqlite:///./neova.db"

CLOCK_MODES = ("live", "frozen")


class ConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _get_env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    return value if value else None


def _require(name: str, purpose: str) -> str:
    value = _get_env(name)
    if value is None:
        raise ConfigurationError(
            f"Missing required environment variable {name} ({purpose})."
        )
    return value


def get_openrouter_base_url() -> str:
    return _get_env("OPENROUTER_BASE_URL") or DEFAULT_OPENROUTER_BASE_URL


def require_openrouter_api_key() -> str:
    """Return the OpenRouter key; fail closed with a sanitized error."""
    return _require("OPENROUTER_API_KEY", "model calls via OpenRouter")


def get_api_base_url() -> str:
    return _get_env("API_BASE_URL") or DEFAULT_API_BASE_URL


def get_database_url() -> str:
    """SQLite database path; tests override it with a temporary path."""
    return _get_env("DATABASE_URL") or DEFAULT_DATABASE_URL


def get_clock_mode() -> str:
    """Return the clock mode: ``live`` (default) or ``frozen``."""
    mode = (_get_env("CLOCK_MODE") or "live").strip().lower()
    if mode not in CLOCK_MODES:
        raise ConfigurationError("Invalid CLOCK_MODE: expected live or frozen.")
    return mode


def get_demo_timestamp() -> Optional[str]:
    return _get_env("DEMO_TIMESTAMP")
