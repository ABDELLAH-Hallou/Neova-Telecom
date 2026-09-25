"""Optional Langfuse tracing (bonus): one trace per conversation turn.

Tracing is **strictly opt-in**: it runs only when both ``LANGFUSE``
keys are configured (and not explicitly disabled via
``LANGFUSE_TRACING_ENABLED=false``). Without keys, every helper here
degrades to a no-op, so the offline test suite and key-less runs
neither construct a client nor touch the network — the same
"optional, never fails" contract as the OpenRouter calls.

Structure (Langfuse best practice: one trace per chatbot turn, turns
grouped into a session, one generation per model invocation):

- :func:`turn` — root observation for one ``/agent/chat`` turn, named
  ``agent-chat``, with ``session_id`` = the demo session token so
  multi-turn conversations group in the Sessions view. Input is the
  user message; output is the final French reply.
- :func:`step` — verb-named child observation for one bounded step
  (``classify-intent``, ``retrieve-context``, ``book-appointment``,
  ``create-handoff``, ...), typed via ``as_type`` (``tool`` for the
  state-changing/API tool calls, ``retriever`` for corpus search).
- Chat generations are captured automatically by the OpenAI-SDK
  drop-in (``langfuse.openai``): model name, token usage and API
  errors are attached by the integration, and each call nests under
  the step that requested it.

**Redaction rules** (same culture as ``neova.usage``): traces carry
the user message and the final French reply, route names, tool names,
counts and statuses — never the API key, never the Langfuse secret,
never customer-API payloads (``api_values``), and no ``user_id``:
the demo customer identity stays out of a third-party service. The
session token is a random process-local value (it dies with the app)
used only for grouping.

The client is constructed lazily on first use and never raises into
callers: a construction failure disables tracing with a warning, the
conversation continues unaffected.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from .config import (
    get_langfuse_host,
    get_langfuse_public_key,
    get_langfuse_secret_key,
    langfuse_tracing_enabled,
)

TRACE_NAME = "agent-chat"
TRACE_TAG = "conversation"

_lock = threading.Lock()
_client: Any = None
_client_disabled = False


# ---------------------------------------------------------------------------
# Disabled-path shim: same surface as a real observation, zero effect.


class _DisabledObservation:
    """No-op stand-in so callers need no branch on tracing state."""

    def update(self, *args: Any, **kwargs: Any) -> "_DisabledObservation":
        return self


_DISABLED = _DisabledObservation()


def _default_client_factory() -> Any:
    from langfuse import Langfuse  # imported lazily: keeps tests import-light

    return Langfuse(
        public_key=get_langfuse_public_key(),
        secret_key=get_langfuse_secret_key(),
        host=get_langfuse_host(),
    )


# Injectable for tests: a factory returning a recording double.
_client_factory = _default_client_factory


def enabled() -> bool:
    """True only when both keys are set and tracing is not switched off."""
    return bool(get_langfuse_public_key() and get_langfuse_secret_key()
                and langfuse_tracing_enabled())


def client() -> Any:
    """Lazily constructed Langfuse client, or None when tracing is off.

    A construction failure disables tracing for the process with a
    single warning — observability must never break the conversation.
    """
    global _client, _client_disabled
    if not enabled():
        return None
    with _lock:
        if _client is not None:
            return _client
        if _client_disabled:
            return None
        try:
            _client = _client_factory()
        except Exception as error:  # never fail the app for telemetry
            _client_disabled = True
            print(f"Langfuse tracing disabled: {type(error).__name__}")
            return None
        return _client


def openai_client_class() -> type:
    """The OpenAI client class to construct in production transports.

    The ``langfuse.openai`` drop-in captures every chat call as a
    generation (model, tokens, errors) nested under the current step.
    Without tracing the plain OpenAI class is returned, so the offline
    test suite constructs no instrumentation at all.
    """
    if client() is not None:
        try:
            from langfuse.openai import OpenAI as InstrumentedOpenAI
            return InstrumentedOpenAI
        except Exception:
            pass
    from openai import OpenAI as PlainOpenAI
    return PlainOpenAI


@contextmanager
def turn(session_token: Optional[str], user_message: str) -> Iterator[Any]:
    """Root observation for one conversation turn (no-op when off).

    One trace per turn, grouped by ``session_id`` (the demo session
    token) so the Sessions view replays the whole conversation. No
    ``user_id`` and no customer fields — see the module docstring.
    """
    client_obj = client()
    if client_obj is None:
        yield _DISABLED
        return
    attributes: dict[str, Any] = {
        "trace_name": TRACE_NAME, "tags": [TRACE_TAG]}
    if session_token:
        attributes["session_id"] = session_token
    try:
        context = client_obj.start_as_current_observation(
            as_type="span", name=TRACE_NAME, input=user_message)
        propagation = propagate_attributes(**attributes)
    except Exception:
        yield _DISABLED
        return
    with context as observation:
        with propagation:
            yield observation


@contextmanager
def step(name: str, *, as_type: str = "span",
         input: Any = None) -> Iterator[Any]:
    """One named child observation (no-op when off).

    ``as_type`` follows Langfuse observation types: ``span`` for
    reasoning steps, ``tool`` for tool/API calls, ``retriever`` for
    corpus search. Callers set ``output``/``metadata`` on the yielded
    observation (both paths expose ``update``).
    """
    client_obj = client()
    if client_obj is None:
        yield _DISABLED
        return
    try:
        context = client_obj.start_as_current_observation(
            as_type=as_type, name=name, input=input)
    except Exception:
        yield _DISABLED
        return
    with context as observation:
        yield observation


def flush() -> None:
    """Send buffered observations (app shutdown / short-lived runs)."""
    client_obj = client()
    if client_obj is not None:
        try:
            client_obj.flush()
        except Exception:
            pass


def shutdown() -> None:
    """Flush and release the client (application shutdown)."""
    global _client
    with _lock:
        if _client is None:
            return
        client_obj, _client = _client, None
    try:
        client_obj.shutdown()
    except Exception:
        pass


def reset_for_tests() -> None:
    """Test helper: drop the cached client so env changes take effect."""
    global _client, _client_disabled
    with _lock:
        _client = None
        _client_disabled = False


def propagate_attributes(**attributes: Any) -> Any:
    """Re-export of the SDK context manager; internal building block.

    Use :func:`turn`/:func:`step` instead of calling this directly.
    """
    from langfuse import propagate_attributes as sdk_propagate
    return sdk_propagate(**attributes)