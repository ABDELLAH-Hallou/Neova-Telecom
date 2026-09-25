"""Central configuration.

All settings are read lazily at call time so foundation-only operations
(health, fixture seed, offline tests) work without environment variables.
Future model operations must request credentials explicitly; errors name
the missing variable, never its value.
"""

import os
from typing import Optional

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
    return _require(
        "OPENROUTER_BASE_URL",
        "OpenRouter client configuration",
    )


def get_embedding_model() -> str:
    """Return the configured OpenRouter embedding model name."""
    return _require("EMBEDDING_MODEL", "OpenRouter embeddings via retrieval")


def get_classifier_model() -> str:
    """Return the configured OpenRouter intent-classification model name."""
    return _require("CLASSIFIER_MODEL", "OpenRouter intent classification")


def get_chat_fallback_model() -> Optional[str]:
    """Return the configured fallback chat model, or None when unset.

    The fallback is optional at runtime (a missing name degrades to
    primary-only, never a guess); issue #6 requires it to be configured
    and verified as a different upstream provider before publication.
    """
    return _get_env("CHAT_FALLBACK_MODEL")


def get_usage_log_path() -> Optional[str]:
    """Return the optional redacted usage-log JSONL path, or None.

    ``USAGE_LOG`` persists the per-call usage ledger (model, provider,
    tokens, cost) so spend evidence survives the server stopping.
    """
    return _get_env("USAGE_LOG")


def get_langfuse_public_key() -> Optional[str]:
    """Return the optional Langfuse public key, or None when unset."""
    return _get_env("LANGFUSE_PUBLIC_KEY")


def get_langfuse_secret_key() -> Optional[str]:
    """Return the optional Langfuse secret key, or None when unset.

    The value itself is only ever passed to the Langfuse client
    constructor; it is never logged, recorded or returned elsewhere.
    """
    return _get_env("LANGFUSE_SECRET_KEY")


def get_langfuse_host() -> str:
    """Return the Langfuse server URL (EU cloud by default).

    The application configuration names the server ``LANGFUSE_BASE_URL``
    (as in ``.env.example``); the SDK-native ``LANGFUSE_HOST`` is also
    accepted.
    """
    return (_get_env("LANGFUSE_BASE_URL")
            or _get_env("LANGFUSE_HOST")
            or "https://cloud.langfuse.com")


def langfuse_tracing_enabled() -> bool:
    """Return False only when tracing is explicitly switched off.

    Tracing is enabled whenever both Langfuse keys are configured; the
    SDK-native ``LANGFUSE_TRACING_ENABLED=false`` env var disables it
    (used by the offline test suite to guarantee no network traffic).
    """
    return (_get_env("LANGFUSE_TRACING_ENABLED") or "").strip().lower() != "false"


def require_openrouter_api_key() -> str:
    """Return the OpenRouter key; fail closed with a sanitized error."""
    return _require("OPENROUTER_API_KEY", "model calls via OpenRouter")

def get_api_base_url() -> str:
    return _require(
        "API_BASE_URL",
        "FastAPI service configuration",
    )


def get_database_url() -> str:
    return _require(
        "DATABASE_URL",
        "SQLite database configuration",
    )


def get_clock_mode() -> str:
    """Return the clock mode: ``live`` (default) or ``frozen``."""
    mode = (_get_env("CLOCK_MODE") or "live").strip().lower()
    if mode not in CLOCK_MODES:
        raise ConfigurationError("Invalid CLOCK_MODE: expected live or frozen.")
    return mode


def get_demo_timestamp() -> Optional[str]:
    return _get_env("DEMO_TIMESTAMP")
