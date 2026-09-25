"""Offline issue #6 resilience tests: deterministic fault injection.

No network calls and no real sleeps: the OpenRouter chat policy
(``neova.provider``), the embeddings retry loop and the in-process API
transport are replaced by scripted fakes, and the sleeper is a list
collector — every wait is asserted exactly. Fault matrix (issue #6):

- chat 429/529: Retry-After honored and clamped, exponential backoff
  with jitter, at most two retries on the primary, verified fallback,
  terminal ``ProviderUnavailable`` → French unavailability + handoff;
- embeddings: bounded 429 exhaustion → FTS5-degraded search; budget
  gate blocks before any paid call;
- API reads: one retry on 500/ASGI failure, never on 4xx, never for
  POST; exhausted read → short French failure + handoff offer;
- booking 500: by-key check (saved → confirmed, absent → unconfirmed
  with voided code, failed check → unconfirmed), never a blind replay;
- spend: known chat+embedding costs sum, unknown usage labeled unknown,
  ledger redacted (never the key, never customer data).
"""

import json
import threading
import time
import urllib.error
from types import SimpleNamespace

import openai
import pytest

from neova import classifier, conversation, db, provider, retrieval, tools, usage
from neova.chunking import chunk_hash_for
from neova.classifier import ClassifierError, classify_message
from neova.config import ConfigurationError
from neova.embeddings import BudgetExceeded, EmbeddingError, openrouter_embedder
from neova.graph import run_conversation
from neova.nodes.french_answer import MODEL_UNAVAILABLE_REPLY
from neova.nodes.booking_flow import _UNCONFIRMED_REPLY
from neova.provider import ProviderUnavailable, RouteError

CUSTOMER = "NEO-88213"

SUMMARY = {"customer_id": CUSTOMER, "plan": "Fibre Sérénité",
           "monthly_price": 29.99, "balance_due": 0.0, "open_incident_id": None}
SLOTS = [{"slot_id": "SLOT-1", "start": "2026-08-26T14:00:00+02:00",
          "end": "2026-08-26T16:00:00+02:00"}]
SAVED_APPOINTMENT = {"appointment_id": 42, "customer_id": CUSTOMER,
                     "slot_id": "SLOT-1", "reason_id": "no_internet",
                     "start": SLOTS[0]["start"], "end": SLOTS[0]["end"]}


# ---------------------------------------------------------------------------
# Fakes


def ok_response(content="ok", model="fake-model", provider_name="primary-upstream",
                chat_usage=None):
    return {"content": content, "model": model, "provider": provider_name,
            "usage": chat_usage}


class ScriptedChat:
    """Fake chat transport: items are dicts (success) or RouteError."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, request: dict) -> dict:
        self.calls.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class ScriptedAPI:
    """Fake in-process transport: items are (status, payload) or exceptions.

    A callable item simulates a slow handler: it runs to completion
    (e.g. sleeps past the tool timeout) and then answers 200.
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def request(self, method, url, json=None, headers=None):
        self.calls.append((method, url))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item()
            return FakeResponse(200, {})
        return FakeResponse(*item)


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"retry-after": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://openrouter.ai/api/v1/embeddings", code,
                                  "fault", headers, None)


class ControlledSleeper:
    """Records every wait instead of sleeping."""

    def __init__(self):
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


# ---------------------------------------------------------------------------
# Fixtures


@pytest.fixture(autouse=True)
def clean_ledger():
    usage.reset()
    yield
    usage.reset()


@pytest.fixture
def policy_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-never-real")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    monkeypatch.setenv("CHAT_MODEL", "primary-a")
    monkeypatch.setenv("CHAT_FALLBACK_MODEL", "fallback-b")
    return monkeypatch


@pytest.fixture
def agent_env(monkeypatch, tmp_path):
    """Temporary database + frozen demo clock + one issued demo session."""
    path = tmp_path / "neova.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("CLOCK_MODE", "frozen")
    monkeypatch.setenv("DEMO_TIMESTAMP", "2026-08-26T12:00:00+02:00")
    for name in ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "CLASSIFIER_MODEL",
                 "CHAT_MODEL", "CHAT_FALLBACK_MODEL", "EMBEDDING_MODEL"):
        monkeypatch.delenv(name, raising=False)
    conversation.reset()
    db.init_db()
    from neova.session import issue_session
    token = issue_session(CUSTOMER)
    yield {"token": token}
    db.close_session_connections()
    tools.reset_transport()
    conversation.reset()


def turn(agent, message: str, script) -> tuple[dict, ScriptedAPI]:
    """One graph turn with a scripted in-process API transport."""
    scripted = ScriptedAPI(script)
    tools._transport_client = lambda: scripted  # injected transport seam
    try:
        return run_conversation(message, [], agent["token"], CUSTOMER), scripted
    finally:
        tools.reset_transport()


# ---------------------------------------------------------------------------
# Chat policy: Retry-After, backoff+jitter, bounded retries, fallback


def test_retry_after_header_is_honored(policy_env):
    sleeper = ControlledSleeper()
    transport = ScriptedChat([RouteError(429, retry_after=3.0), ok_response()])
    result = provider.chat_invoke(
        [{"role": "user", "content": "bonjour"}],
        transport=transport, sleeper=sleeper, jitter=lambda: 1.0)
    assert sleeper.waits == [3.0]
    assert [call["model"] for call in transport.calls] == ["primary-a", "primary-a"]
    assert result.route == "primary" and result.attempts == 2


def test_exponential_backoff_with_jitter_when_no_header(policy_env):
    sleeper = ControlledSleeper()
    transport = ScriptedChat([
        RouteError(529), RouteError(429), ok_response()])
    result = provider.chat_invoke(
        [{"role": "user", "content": "bonjour"}],
        transport=transport, sleeper=sleeper, jitter=lambda: 1.0)
    assert sleeper.waits == [0.5, 1.0]  # 0.5s then 1.0s, no jitter inflation
    assert result.attempts == 3 and result.route == "primary"


def test_retry_after_is_clamped(policy_env):
    sleeper = ControlledSleeper()
    transport = ScriptedChat([RouteError(429, retry_after=30.0), ok_response()])
    provider.chat_invoke([{"role": "user", "content": "x"}],
                         transport=transport, sleeper=sleeper, jitter=lambda: 1.0)
    assert sleeper.waits == [8.0]  # valid header → capped at MAX_RETRY_AFTER_SECONDS


def test_retry_after_accepts_http_dates_and_rejects_garbage():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    now = datetime.now(timezone.utc)
    # HTTP-date 45 s in the future → parsed, then capped at 8 s by retry_delay.
    future = format_datetime(now + timedelta(seconds=45), usegmt=True)
    parsed = provider.retry_after_seconds(future, now=now)
    assert parsed is not None and 40 < parsed <= 45
    assert provider.retry_delay(parsed, 0, lambda: 1.0) == 8.0
    # A past date is not a delay; garbage and naive values are not guessed.
    past = format_datetime(now - timedelta(seconds=10), usegmt=True)
    assert provider.retry_after_seconds(past, now=now) is None
    naive = format_datetime(now + timedelta(seconds=30)).replace("+0000", "")
    assert provider.retry_after_seconds(naive.replace("GMT", ""), now=now) is None
    assert provider.retry_after_seconds("soon", now=now) is None
    assert provider.retry_after_seconds("", now=now) is None
    assert provider.retry_after_seconds(None) is None
    # Plain seconds still parse exactly.
    assert provider.retry_after_seconds("3", now=now) == 3.0


def test_two_retries_on_primary_then_verified_fallback(policy_env):
    sleeper = ControlledSleeper()
    transport = ScriptedChat([
        RouteError(429), RouteError(529), RouteError(429),
        ok_response(model="fallback-b", provider_name="other-upstream",
                    chat_usage={"prompt_tokens": 10, "completion_tokens": 5,
                                "cost_usd": 0.002}),
    ])
    result = provider.chat_invoke(
        [{"role": "user", "content": "bonjour"}],
        transport=transport, sleeper=sleeper, jitter=lambda: 1.0)
    assert [call["model"] for call in transport.calls] == [
        "primary-a", "primary-a", "primary-a", "fallback-b"]
    assert result.route == "fallback" and result.provider == "other-upstream"
    routes = [(r.route, r.status, r.retries) for r in usage.records()]
    assert routes == [("primary", "failed", 0), ("primary", "failed", 1),
                      ("primary", "failed", 2), ("fallback", "ok", 0)]
    known = usage.records()[-1]
    assert (known.prompt_tokens, known.completion_tokens, known.cost_usd) == (10, 5, 0.002)


def test_non_retryable_status_switches_to_fallback_immediately(policy_env):
    sleeper = ControlledSleeper()
    transport = ScriptedChat([
        RouteError(500), ok_response(model="fallback-b")])
    result = provider.chat_invoke([{"role": "user", "content": "x"}],
                                  transport=transport, sleeper=sleeper,
                                  jitter=lambda: 1.0)
    assert sleeper.waits == []  # no wait: the failure is not 429/529
    assert result.route == "fallback"
    assert [call["model"] for call in transport.calls] == ["primary-a", "fallback-b"]


def test_both_routes_failing_is_typed_and_recorded(policy_env):
    sleeper = ControlledSleeper()
    transport = ScriptedChat([RouteError(429)] * 4)
    with pytest.raises(ProviderUnavailable):
        provider.chat_invoke([{"role": "user", "content": "x"}],
                             transport=transport, sleeper=sleeper,
                             jitter=lambda: 1.0)
    assert sleeper.waits == [0.5, 1.0]  # 2 primary retries; fallback never waits
    assert [(r.route, r.status) for r in usage.records()] == [
        ("primary", "failed"), ("primary", "failed"), ("primary", "failed"),
        ("fallback", "failed")]


def test_missing_fallback_model_degrades_to_primary_only(policy_env, monkeypatch):
    monkeypatch.delenv("CHAT_FALLBACK_MODEL")
    transport = ScriptedChat([RouteError(429)] * 3)
    with pytest.raises(ProviderUnavailable):
        provider.chat_invoke([{"role": "user", "content": "x"}],
                             transport=transport, sleeper=lambda s: None,
                             jitter=lambda: 1.0)
    assert len(transport.calls) == 3  # no fallback attempt: none configured


def test_missing_usage_is_unknown_never_zero(policy_env):
    transport = ScriptedChat([{"content": "réponse", "model": "primary-a",
                               "provider": "primary-upstream", "usage": None}])
    provider.chat_invoke([{"role": "user", "content": "x"}],
                         transport=transport, sleeper=lambda s: None)
    record = usage.records()[0]
    assert record.prompt_tokens is None and record.cost_usd is None
    summary = usage.spend_summary()
    assert summary["kinds"]["chat"]["calls_unknown_cost"] == 1
    assert summary["kinds"]["chat"]["known_usd"] == 0.0
    assert summary["total_known_usd"] == 0.0


def test_ledger_is_redacted(policy_env):
    transport = ScriptedChat([ok_response(chat_usage={
        "prompt_tokens": 1, "completion_tokens": 1, "cost_usd": 0.01})])
    provider.chat_invoke([{"role": "user", "content": "x"}],
                         transport=transport, sleeper=lambda s: None)
    dumped = json.dumps([r.__dict__ for r in usage.records()])
    assert "test-key-never-real" not in dumped
    assert set(usage.records()[0].__dict__) == {
        "kind", "model", "route", "provider", "status", "prompt_tokens",
        "completion_tokens", "total_tokens", "cost_usd", "retries", "error"}


def test_answer_model_requests_usage_accounting(policy_env):
    transport = ScriptedChat([ok_response()])
    model = provider.PolicyChatModel(
        max_tokens=123, extra_body={"usage": {"include": True}},
        transport=transport, sleeper=lambda s: None)
    assert model.invoke("question ?").content == "ok"
    assert transport.calls[0]["extra_body"] == {"usage": {"include": True}}
    assert transport.calls[0]["max_tokens"] == 123


def test_answer_model_output_is_bounded(policy_env):
    from neova.nodes import french_answer

    model = french_answer.answer_model()
    assert isinstance(model, provider.PolicyChatModel)
    assert model._kwargs["max_tokens"] == french_answer.ANSWER_MAX_TOKENS


def test_route_verification_needs_distinct_observed_providers(policy_env):
    report = provider.fallback_verification()
    assert report["fallback_configured"] and not report["distinct_upstreams_verified"]
    usage.record(kind=usage.KIND_CHAT, model="primary-a", route="primary",
                 provider="upstream-one", status="ok")
    usage.record(kind=usage.KIND_CHAT, model="fallback-b", route="fallback",
                 provider="upstream-two", status="ok")
    assert provider.fallback_verification()["distinct_upstreams_verified"] is True
    usage.reset()
    usage.record(kind=usage.KIND_CHAT, model="primary-a", route="primary",
                 provider="same-upstream", status="ok")
    usage.record(kind=usage.KIND_CHAT, model="fallback-b", route="fallback",
                 provider="same-upstream", status="ok")
    assert provider.fallback_verification()["distinct_upstreams_verified"] is False


def test_verify_routes_probes_each_route_live_and_reports_providers(policy_env,
                                                                    monkeypatch):
    monkeypatch.setattr("neova.embeddings.key_status",
                        lambda: {"usage": 0.0, "limit": 10.0, "limit_remaining": 8.0})
    transport = ScriptedChat([
        ok_response(model="primary-a", provider_name="upstream-one",
                    chat_usage={"prompt_tokens": 1, "completion_tokens": 0,
                                "total_tokens": 1, "cost_usd": 0.00001}),
        ok_response(model="fallback-b", provider_name="upstream-two",
                    chat_usage={"prompt_tokens": 1, "completion_tokens": 0,
                                "total_tokens": 1, "cost_usd": 0.00001}),
    ])
    report = provider.verify_routes(transport=transport)
    assert report["distinct_upstreams_verified"] is True
    assert report["primary"]["provider"] == "upstream-one"
    assert report["fallback"]["provider"] == "upstream-two"
    # One minimal probe per route: bounded cost, real responses.
    assert [c["model"] for c in transport.calls] == ["primary-a", "fallback-b"]
    assert all(c["max_tokens"] == provider.VERIFY_MAX_TOKENS for c in transport.calls)
    assert [(r.route, r.status, r.provider) for r in usage.records()] == [
        ("primary", "ok", "upstream-one"), ("fallback", "ok", "upstream-two")]


def test_verify_routes_same_upstream_is_not_verified(policy_env, monkeypatch):
    monkeypatch.setattr("neova.embeddings.key_status",
                        lambda: {"usage": 0.0, "limit": 10.0, "limit_remaining": 8.0})
    transport = ScriptedChat([
        ok_response(model="primary-a", provider_name="same-upstream"),
        ok_response(model="fallback-b", provider_name="same-upstream"),
    ])
    report = provider.verify_routes(transport=transport)
    assert report["distinct_upstreams_verified"] is False


def test_verify_routes_failed_probe_is_reported_not_guessed(policy_env, monkeypatch):
    monkeypatch.setattr("neova.embeddings.key_status",
                        lambda: {"usage": 0.0, "limit": 10.0, "limit_remaining": 8.0})
    transport = ScriptedChat([RouteError(500),
                              ok_response(model="fallback-b",
                                          provider_name="upstream-two")])
    report = provider.verify_routes(transport=transport)
    assert report["primary"]["status"] == "failed"
    assert report["distinct_upstreams_verified"] is False


def test_verify_routes_is_budget_gated(policy_env, monkeypatch):
    monkeypatch.setattr("neova.embeddings.key_status",
                        lambda: {"usage": 9.99, "limit": 10.0, "limit_remaining": 0.01})
    attempted = []
    monkeypatch.setattr(provider, "openrouter_transport",
                        lambda: (lambda request: attempted.append(request)))
    with pytest.raises(BudgetExceeded):
        provider.verify_routes()
    assert attempted == []  # gate fires before any probe call


def test_usage_log_persists_and_reloads(tmp_path):
    path = tmp_path / "usage.jsonl"
    usage.set_log_path(str(path))
    try:
        usage.record(kind=usage.KIND_CHAT, model="primary-a", route="primary",
                     provider="upstream-one", status="ok", prompt_tokens=3,
                     completion_tokens=2, total_tokens=5, cost_usd=0.01)
        usage.record(kind=usage.KIND_EMBEDDINGS, model="embedder-a",
                     status="ok", prompt_tokens=32, total_tokens=32)
    finally:
        usage.set_log_path(None)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    loaded = usage.load_records(str(path))
    assert len(loaded) == 2
    assert loaded[0].total_tokens == 5 and loaded[0].cost_usd == 0.01
    assert loaded[1].completion_tokens is None and loaded[1].total_tokens == 32
    summary = usage.spend_summary(loaded)
    assert summary["total_known_usd"] == pytest.approx(0.01)
    assert summary["kinds"]["embeddings"]["calls_unknown_cost"] == 1


def test_load_records_skips_corrupt_lines(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text(
        '{"kind": "chat", "model": "a", "status": "ok", "cost_usd": 0.02}\n'
        'not json at all\n'
        '{"kind": "embeddings", "model": "e", "prompt_tokens": 7}\n', encoding="utf-8")
    loaded = usage.load_records(str(path))
    assert [r.kind for r in loaded] == ["chat", "embeddings"]
    assert loaded[0].cost_usd == 0.02
    assert loaded[1].completion_tokens is None  # absent fields stay unknown


def test_configure_from_env_wires_the_usage_log(monkeypatch, tmp_path):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("USAGE_LOG", str(path))
    usage.configure_from_env()
    try:
        usage.record(kind=usage.KIND_CHAT, model="primary-a", route="primary",
                     provider="upstream-one", status="ok", cost_usd=0.03)
        assert path.exists()
        assert usage.load_records(str(path))[0].cost_usd == 0.03
    finally:
        usage.set_log_path(None)


def test_usage_log_creates_missing_directories(tmp_path):
    path = tmp_path / "evidence" / "nested" / "usage.jsonl"
    usage.set_log_path(str(path))
    try:
        usage.record(kind=usage.KIND_CHAT, model="a", route="primary",
                     status="ok", cost_usd=0.01)
        assert path.exists()
        assert len(usage.load_records(str(path))) == 1
    finally:
        usage.set_log_path(None)


def test_concurrent_records_never_interleave_the_log(tmp_path):
    path = tmp_path / "usage.jsonl"
    usage.set_log_path(str(path))
    threads = []
    try:
        for index in range(8):
            def worker(seed=index):
                for _ in range(10):
                    usage.record(kind=usage.KIND_CHAT, model=f"m{seed}",
                                 route="primary", status="ok", cost_usd=0.001)
            threads.append(threading.Thread(target=worker))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        usage.set_log_path(None)
    assert len(usage.records()) == 80
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 80  # every append landed, none interleaved
    loaded = usage.load_records(str(path))
    assert len(loaded) == 80  # every line is valid JSON
    assert usage.spend_summary(loaded)["total_known_usd"] == pytest.approx(0.08)


def test_provider_cli_reads_persisted_providers(policy_env, tmp_path, capsys):
    path = tmp_path / "usage.jsonl"
    usage.set_log_path(str(path))
    try:
        usage.record(kind=usage.KIND_CHAT, model="primary-a", route="primary",
                     provider="upstream-one", status="ok")
        usage.record(kind=usage.KIND_CHAT, model="fallback-b", route="fallback",
                     provider="upstream-two", status="ok")
    finally:
        usage.set_log_path(None)
    assert provider.main(["--usage-log", str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["observed_providers"] == {"primary": "upstream-one",
                                            "fallback": "upstream-two"}
    assert report["distinct_upstreams_verified"] is True
    assert report["usage_log"] == str(path)


def test_usage_cli_summarizes_a_persisted_log(tmp_path, capsys):
    path = tmp_path / "usage.jsonl"
    usage.set_log_path(str(path))
    try:
        usage.record(kind=usage.KIND_CHAT, model="a", route="primary",
                     status="ok", prompt_tokens=10, completion_tokens=5,
                     total_tokens=15, cost_usd=0.01)
        usage.record(kind=usage.KIND_EMBEDDINGS, model="e", status="ok",
                     prompt_tokens=7, total_tokens=7)  # cost unknown
    finally:
        usage.set_log_path(None)
    assert usage.main([str(path)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["total_known_usd"] == pytest.approx(0.01)
    assert summary["kinds"]["chat"]["calls_unknown_cost"] == 0
    assert summary["kinds"]["embeddings"]["calls_unknown_cost"] == 1


def test_classifier_runs_through_the_shared_policy(policy_env, monkeypatch):
    monkeypatch.setenv("CLASSIFIER_MODEL", "classifier-a")
    payload = json.dumps({"intent": "internet", "out_of_scope": False,
                          "ambiguous": False, "prompt_injection": False,
                          "reason_candidate": "no_internet"})

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            item = script.pop(0)
            if isinstance(item, Exception):
                raise item
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=payload))])

    def status_429():
        response = SimpleNamespace(status_code=429, headers={}, request=None)
        return openai.APIStatusError("overloaded", response=response, body=None)

    script = [status_429(), status_429(), status_429(), None]
    monkeypatch.setattr(classifier, "OpenAI", FakeOpenAI)
    invoke = classifier.openrouter_classifier()
    classification = classify_message("Ma connexion est coupée", invoke)
    assert classification.intent == "internet"
    assert [(r.kind, r.route, r.status) for r in usage.records()] == [
        ("classifier", "primary", "failed"), ("classifier", "primary", "failed"),
        ("classifier", "primary", "failed"), ("classifier", "fallback", "ok")]


def test_classifier_still_fails_closed_on_invalid_output(policy_env, monkeypatch):
    monkeypatch.setenv("CLASSIFIER_MODEL", "classifier-a")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="not json"))])

    monkeypatch.setattr(classifier, "OpenAI", FakeOpenAI)
    invoke = classifier.openrouter_classifier()
    with pytest.raises(ClassifierError):
        classify_message("question", invoke)


# ---------------------------------------------------------------------------
# Embeddings: bounded 429/529 retries, Retry-After, budget gate


def test_embedding_retry_after_is_honored(policy_env, monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "embedder-a")
    monkeypatch.setattr("neova.embeddings.key_status",
                        lambda: {"usage": 0.0, "limit": 10.0, "limit_remaining": 8.0})
    sleeper = ControlledSleeper()
    responses = [http_error(429, retry_after="2"),
                 {"data": [{"index": 0, "embedding": [0.1, 0.2]}],
                  "usage": {"prompt_tokens": 7, "total_tokens": 7}}]

    def fake_post(url, payload, key):
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr("neova.embeddings._post_json", fake_post)
    embed = openrouter_embedder(sleeper=sleeper, jitter=lambda: 1.0)
    assert embed(["bonjour"]) == [[0.1, 0.2]]
    assert sleeper.waits == [2.0]
    assert [(r.kind, r.status) for r in usage.records()] == [
        ("embeddings", "failed"), ("embeddings", "ok")]
    record = usage.records()[-1]
    assert record.prompt_tokens == 7
    assert record.completion_tokens is None  # embeddings never complete tokens
    assert record.total_tokens == 7


def test_embedding_429_exhaustion_is_bounded(policy_env, monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "embedder-a")
    monkeypatch.setattr("neova.embeddings.key_status",
                        lambda: {"usage": 0.0, "limit": 10.0, "limit_remaining": 8.0})
    sleeper = ControlledSleeper()

    def always_429(url, payload, key):
        raise http_error(429)

    monkeypatch.setattr("neova.embeddings._post_json", always_429)
    embed = openrouter_embedder(sleeper=sleeper, jitter=lambda: 1.0)
    with pytest.raises(EmbeddingError):
        embed(["bonjour"])
    assert sleeper.waits == [0.5, 1.0]  # initial call + exactly two retries
    assert len(usage.records()) == 3
    assert all(r.status == "failed" for r in usage.records())


def test_budget_gate_blocks_before_any_paid_call(policy_env, monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "embedder-a")
    monkeypatch.setattr("neova.embeddings.key_status",
                        lambda: {"usage": 9.6, "limit": 10.0, "limit_remaining": 0.4})
    attempted = []
    monkeypatch.setattr("neova.embeddings._post_json",
                        lambda url, payload, key: attempted.append(url))
    embed = openrouter_embedder(sleeper=lambda s: None)
    with pytest.raises(BudgetExceeded):
        embed(["bonjour"])
    assert attempted == []  # exhausted cap: no optional call was attempted
    assert usage.records() == []


def test_malformed_embedding_response_is_never_logged_ok(policy_env, monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "embedder-a")
    monkeypatch.setattr("neova.embeddings.key_status",
                        lambda: {"usage": 0.0, "limit": 10.0, "limit_remaining": 8.0})

    def malformed(url, payload, key):
        return {"usage": {"prompt_tokens": 5, "total_tokens": 5}}  # no "data"

    monkeypatch.setattr("neova.embeddings._post_json", malformed)
    embed = openrouter_embedder(sleeper=lambda s: None, jitter=lambda: 1.0)
    with pytest.raises(EmbeddingError):
        embed(["bonjour"])
    statuses = [r.status for r in usage.records()]
    assert statuses == ["failed", "failed", "failed"]  # never a phantom success


def test_search_degrades_to_fts_when_query_embedding_fails(monkeypatch, tmp_path):
    conn = db.connect(str(tmp_path / "retrieval.db"))
    try:
        db.init_retrieval_schema(conn)
        text = "Résiliation : préavis de 10 jours."
        chunk_hash = chunk_hash_for("faq-resiliation", 1, text)
        conn.execute(
            "INSERT INTO chunks (chunk_hash, source_id, source_path, page_start, "
            "page_end, section, access, word_count, text) VALUES (?, ?, ?, 1, 1, "
            "'faq', 'public', ?, ?)",
            (chunk_hash, "faq-resiliation", "faq-resiliation.pdf", len(text.split()), text))
        conn.execute(
            "INSERT INTO chunk_fts (text, source_id, chunk_hash) VALUES (?, ?, ?)",
            (text, "faq-resiliation", chunk_hash))
        conn.commit()

        def failing_embed(texts):
            raise EmbeddingError("embedding unavailable")

        outcome = retrieval.search(conn, "préavis résiliation",
                                   embed_fn=failing_embed, mode="hybrid")
        assert "query_embedding_failed_fts_fallback" in outcome.degraded
        assert [p.source_id for p in outcome.results] == ["faq-resiliation"]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# In-process API reads: one retry on transient failure, never for POST


def test_read_500_is_retried_once_then_succeeds(agent_env, monkeypatch):
    scripted = ScriptedAPI([(500, {}), (200, SUMMARY)])
    monkeypatch.setattr(tools, "_transport_client", lambda: scripted)
    assert tools.get_summary("token", CUSTOMER) == SUMMARY
    assert len(scripted.calls) == 2


def test_read_500_twice_raises_with_final_status(agent_env, monkeypatch):
    scripted = ScriptedAPI([(500, {}), (500, {})])
    monkeypatch.setattr(tools, "_transport_client", lambda: scripted)
    with pytest.raises(tools.ToolError) as error:
        tools.get_summary("token", CUSTOMER)
    assert error.value.status_code == 500
    assert len(scripted.calls) == 2


def test_post_is_never_retried(agent_env, monkeypatch):
    scripted = ScriptedAPI([(500, {})])
    monkeypatch.setattr(tools, "_transport_client", lambda: scripted)
    with pytest.raises(tools.ToolError):
        tools.book_appointment("token", CUSTOMER, "SLOT-1", "no_internet", "key")
    assert len(scripted.calls) == 1  # the write is never blindly replayed


def test_asgi_failure_retried_once_for_get_only(agent_env, monkeypatch):
    scripted = ScriptedAPI([RuntimeError("portal closed"), (200, SUMMARY)])
    monkeypatch.setattr(tools, "_transport_client", lambda: scripted)
    assert tools.get_incidents("token") == SUMMARY
    scripted = ScriptedAPI([RuntimeError("portal closed")])
    monkeypatch.setattr(tools, "_transport_client", lambda: scripted)
    with pytest.raises(tools.ToolError) as error:
        tools.create_handoff("token", "technical", "summary", "normal")
    assert error.value.status_code == 503


def test_exhausted_read_gives_french_failure_and_handoff_offer(agent_env,
                                                               monkeypatch):
    spent: list[list[str]] = []

    class CountingEmbedder:
        def __call__(self, texts):
            spent.append(list(texts))
            raise RuntimeError("the embedder must never be reached here")

    monkeypatch.setattr("neova.nodes.gather.embedder_factory",
                        lambda: CountingEmbedder())
    (result, _) = turn(agent_env, "Ma connexion internet ne marche plus",
                       [(500, {}), (500, {}), (500, {}), (500, {})])
    assert "Je n'ai pas pu consulter votre dossier" in result["reply"]
    assert "conseiller" in result["reply"]
    assert "api_read_failed" in result["degraded"]
    assert "api_read_failed_essential" in result["degraded"]
    assert "search_skipped_read_failure" in result["degraded"]
    assert spent == []  # no embedding credit spent after failed reads


def test_read_timeout_is_retried_once_then_504(agent_env, monkeypatch):
    monkeypatch.setattr(tools, "TOOL_TIMEOUT_SECONDS", 0.05)
    slow = lambda: time.sleep(0.3)  # noqa: E731 — slow handler, far past the bound
    scripted = ScriptedAPI([slow, (200, SUMMARY)])
    monkeypatch.setattr(tools, "_transport_client", lambda: scripted)
    assert tools.get_summary("token", CUSTOMER) == SUMMARY
    assert len(scripted.calls) == 2  # one timed-out attempt + its single retry


def test_post_timeout_surfaces_504_without_retry(agent_env, monkeypatch):
    monkeypatch.setattr(tools, "TOOL_TIMEOUT_SECONDS", 0.05)
    slow = lambda: time.sleep(0.3)  # noqa: E731
    scripted = ScriptedAPI([slow])
    monkeypatch.setattr(tools, "_transport_client", lambda: scripted)
    with pytest.raises(tools.ToolError) as error:
        tools.book_appointment("token", CUSTOMER, "SLOT-1", "no_internet", "key")
    assert error.value.status_code == 504  # 504 → by-key check in the graph
    assert len(scripted.calls) == 1  # the write is never blindly replayed


# ---------------------------------------------------------------------------
# Booking 500/timeout: decide by idempotency key, never replay the write


def test_booking_500_with_saved_appointment_confirms(agent_env):
    turn(agent_env, "Je veux un rendez-vous technicien", [(200, SUMMARY)])
    turn(agent_env, "pas d'internet", [(200, SLOTS)])
    (proposal, _) = turn(agent_env, "créneau 1", [])
    assert "CONFIRMER RDV" in proposal["reply"]
    code = proposal["reply"].split("CONFIRMER RDV ")[1].split()[0].rstrip(".,;")
    (result, scripted) = turn(
        agent_env, f"CONFIRMER RDV {code}",
        [(500, {}), (200, SAVED_APPOINTMENT)])
    assert "Un rendez-vous existe déjà" in result["reply"]
    assert "42" in result["reply"]  # a saved ID proves the write
    # Exactly one write attempt, then the by-key verification — never a replay.
    assert [method for method, _ in scripted.calls] == ["POST", "GET"]


def test_booking_500_absent_by_key_is_unconfirmed_and_voids_code(agent_env):
    turn(agent_env, "Je veux un rendez-vous technicien", [(200, SUMMARY)])
    turn(agent_env, "pas d'internet", [(200, SLOTS)])
    (proposal, _) = turn(agent_env, "créneau 1", [])
    code = proposal["reply"].split("CONFIRMER RDV ")[1].split()[0].rstrip(".,;")
    (result, _) = turn(agent_env, f"CONFIRMER RDV {code}",
                       [(500, {}), (404, {"detail": "Appointment not found"})])
    assert result["reply"] == _UNCONFIRMED_REPLY
    assert "conseiller" in result["reply"]
    # The voided code is dead: the old phrase can never book anything now.
    (again, scripted) = turn(agent_env, f"CONFIRMER RDV {code}", [])
    assert "expiré" in again["reply"]
    assert scripted.calls == []  # no second write was attempted


def test_booking_500_with_failing_by_key_check_is_unconfirmed(agent_env):
    turn(agent_env, "Je veux un rendez-vous technicien", [(200, SUMMARY)])
    turn(agent_env, "pas d'internet", [(200, SLOTS)])
    (proposal, _) = turn(agent_env, "créneau 1", [])
    code = proposal["reply"].split("CONFIRMER RDV ")[1].split()[0].rstrip(".,;")
    # The check itself fails (GET retried once, still 500): no claim either way.
    (result, scripted) = turn(agent_env, f"CONFIRMER RDV {code}",
                              [(500, {}), (500, {}), (500, {})])
    assert result["reply"] == _UNCONFIRMED_REPLY
    assert [method for method, _ in scripted.calls] == ["POST", "GET", "GET"]


# ---------------------------------------------------------------------------
# Terminal model failure through the graph


def test_chat_provider_exhaustion_answers_french_and_offers_handoff(
        agent_env, monkeypatch):
    monkeypatch.setenv("CHAT_MODEL", "primary-a")
    monkeypatch.setenv("CHAT_FALLBACK_MODEL", "fallback-b")
    sleeper = ControlledSleeper()
    transport = ScriptedChat([RouteError(429)] * 4)
    model = provider.PolicyChatModel(transport=transport, sleeper=sleeper,
                                     jitter=lambda: 1.0)
    monkeypatch.setattr("neova.nodes.french_answer.answer_model", lambda: model)
    (result, _) = turn(agent_env, "Quelle est la grille tarifaire 2026 ?",
                       [(200, SUMMARY)])
    assert result["reply"].startswith(MODEL_UNAVAILABLE_REPLY)
    assert "conseiller" in result["reply"]
    assert "chat_model_unavailable" in result["degraded"]
    assert sleeper.waits == [0.5, 1.0]
    assert [(r.kind, r.route, r.status) for r in usage.records()] == [
        ("chat", "primary", "failed"), ("chat", "primary", "failed"),
        ("chat", "primary", "failed"), ("chat", "fallback", "failed")]


# ---------------------------------------------------------------------------
# Spend: known costs sum, unknowns labeled, estimates only from the API


def test_spend_summary_sums_known_and_labels_unknown():
    usage.record(kind=usage.KIND_CHAT, model="a", route="primary",
                 provider="p", status="ok", prompt_tokens=100,
                 completion_tokens=50, cost_usd=0.012)
    usage.record(kind=usage.KIND_CHAT, model="a", route="primary",
                 provider="p", status="ok")  # unknown usage, unknown cost
    usage.record(kind=usage.KIND_EMBEDDINGS, model="e", status="ok",
                 prompt_tokens=32, cost_usd=0.0002)
    usage.record(kind=usage.KIND_EMBEDDINGS, model="e", status="failed",
                 error="status=429")
    summary = usage.spend_summary()
    assert summary["total_known_usd"] == pytest.approx(0.0122)
    assert summary["kinds"]["chat"]["known_usd"] == pytest.approx(0.012)
    assert summary["kinds"]["chat"]["calls_unknown_cost"] == 1
    assert summary["kinds"]["embeddings"]["known_usd"] == pytest.approx(0.0002)
    assert summary["kinds"]["embeddings"]["calls_unknown_cost"] == 1


def test_config_fails_closed_without_key(policy_env, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(ConfigurationError):
        provider.openrouter_transport()
