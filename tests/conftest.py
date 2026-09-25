"""Shared test fixtures: the whole suite runs fully offline."""

import pytest

from neova import observability

_LANGFUSE_ENV_VARS = (
    "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL", "LANGFUSE_HOST", "LANGFUSE_TRACING_ENABLED",
)


@pytest.fixture(autouse=True)
def offline_observability(monkeypatch):
    """Remove any Langfuse configuration for every test.

    Tracing must be opt-in: with no keys configured, ``observability``
    constructs no client and emits no network traffic, so the suite
    stays deterministic even on a machine where the developer's shell
    carries real Langfuse credentials.
    """
    for name in _LANGFUSE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    observability.reset_for_tests()
    yield
    observability.reset_for_tests()