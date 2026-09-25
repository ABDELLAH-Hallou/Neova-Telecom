"""Langfuse tracing (bonus, optional): opt-in behavior, offline-safe.

The suite always runs with no Langfuse keys (``tests/conftest.py``
strips them), so tracing degrades to no-ops and no client is ever
constructed. The enabled-path wiring is exercised with a recording
fake client injected through the module's factory seam.
"""

import pytest

from neova import observability
from openai import OpenAI as PlainOpenAI


class FakeObservation:
    """Recording double for a real observation object."""

    def __init__(self, name, as_type, input_value, trace):
        self.name = name
        self.as_type = as_type
        self.input_value = input_value
        self.trace = trace
        self.updates: list[dict] = []

    def update(self, **kwargs) -> "FakeObservation":
        self.updates.append(kwargs)
        return self


class FakeClient:
    """Records observation creation order and propagate attributes."""

    def __init__(self):
        self.observations: list[FakeObservation] = []
        self.propagations: list[dict] = []
        self.flushed = False
        self.shutdown_called = False

    def start_as_current_observation(self, *, as_type="span", name="",
                                     input=None, **kwargs):
        observation = FakeObservation(name, as_type, input, self)
        self.observations.append(observation)
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            yield observation

        return _ctx()

    def flush(self):
        self.flushed = True

    def shutdown(self):
        self.shutdown_called = True
        self.flushed = True


@pytest.fixture
def fake_tracing(monkeypatch):
    """Enable tracing with a fake client factory."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    client = FakeClient()
    monkeypatch.setattr(observability, "_client_factory", lambda: client)
    monkeypatch.setattr(
        observability, "propagate_attributes",
        lambda **attributes: _RecordingPropagation(client, attributes))
    observability.reset_for_tests()
    yield client
    observability.reset_for_tests()


class _RecordingPropagation:
    def __init__(self, client, attributes):
        self.client = client
        self.attributes = attributes

    def __enter__(self):
        self.client.propagations.append(self.attributes)
        return self

    def __exit__(self, *exc):
        return False


def test_tracing_is_off_without_keys():
    assert observability.enabled() is False
    assert observability.client() is None


def test_disabled_turn_and_step_are_noops():
    with observability.turn("token-1", "bonjour") as turn_obs:
        assert isinstance(turn_obs, observability._DisabledObservation)
        with observability.step("classify-intent") as step_obs:
            step_obs.update(output="x")  # must never raise


def test_turn_creates_root_observation_with_session(fake_tracing):
    with observability.turn("token-42", "Bonjour") as root:
        assert root.name == "agent-chat"
        assert root.as_type == "span"
        assert root.input_value == "Bonjour"
    assert fake_tracing.observations[0].name == "agent-chat"


def test_propagates_session_and_trace_name_but_no_user_or_customer(
        fake_tracing):
    with observability.turn("token-42", "Bonjour") as root:
        root.update(output="réponse")
    attributes = fake_tracing.propagations[-1]
    assert attributes["trace_name"] == "agent-chat"
    assert attributes["session_id"] == "token-42"
    assert attributes["tags"] == ["conversation"]
    assert "user_id" not in attributes
    assert "customer_id" not in attributes


def test_step_nests_under_the_open_turn(fake_tracing):
    with observability.turn("token-1", "Bonjour"):
        with observability.step(
                "retrieve-context", as_type="retriever", input="q") as obs:
            obs.update(output="2", metadata={"mode": "hybrid"})
    assert [o.name for o in fake_tracing.observations] == [
        "agent-chat", "retrieve-context"]
    assert fake_tracing.observations[1].as_type == "retriever"


def test_client_failure_disables_gracefully(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    def broken_factory():
        raise RuntimeError("boom")

    monkeypatch.setattr(observability, "_client_factory", broken_factory)
    assert observability.client() is None
    # The conversation path keeps working as a no-op.
    with observability.turn("token-1", "Bonjour") as root:
        root.update(output="ok")


def test_shutdown_flushes_and_releases(fake_tracing):
    assert observability.client() is fake_tracing
    observability.shutdown()
    assert fake_tracing.shutdown_called


def test_openai_client_class_is_plain_when_tracing_off():
    assert observability.openai_client_class() is PlainOpenAI


def test_generation_name_only_sent_when_tracing_enabled(monkeypatch):
    # Tracing off (conftest strips the keys): no "name" in the request.
    from neova import provider, usage

    monkeypatch.setenv("CHAT_MODEL", "chat-model")
    captured: list[dict] = []

    def fake_transport(request: dict) -> dict:
        captured.append(dict(request))
        return {"content": "ok", "model": "m", "provider": "p", "usage": {}}

    result = provider.chat_invoke(
        [{"role": "user", "content": "bonjour"}],
        kind=usage.KIND_CHAT, name="generate-response",
        transport=fake_transport)
    assert result.content == "ok"
    assert "name" not in captured[0]