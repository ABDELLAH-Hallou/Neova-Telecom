"""Shared OpenRouter chat policy: bounded retries, provider fallback, usage.

Both chat call sites (the grounded answer and the cheap classifier) go
through :func:`chat_invoke`, which owns the whole issue-#6 policy:

- route list = ``CHAT_MODEL`` (primary) then ``CHAT_FALLBACK_MODEL``
  (fallback, optional at runtime; must be a verified different upstream
  provider before publication — ``verify_routes``/``python -m
  neova.provider --verify``);
- on 429/529: honor ``Retry-After`` when present (capped at
  ``MAX_RETRY_AFTER_SECONDS``), otherwise exponential backoff with
  jitter; **at most two retries** on the primary route;
- any other failure aborts the current route immediately and moves to
  the fallback (one attempt); every route exhausted raises the typed
  :class:`ProviderUnavailable` — callers answer with the French
  unavailability/handoff shape, never an invented reply;
- every attempt is recorded in ``neova.usage`` (model, upstream
  provider, route, tokens, cost, retries); usage the API did not return
  stays ``unknown``, never zero.

The transport and the sleeper are plain injectable callables so tests
run fully offline and deterministically (no clock, no network). The
default transport builds the OpenAI client with ``max_retries=0``:
this policy is the *only* retry layer.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from types import SimpleNamespace
from typing import Callable

import openai
from openai import OpenAI  # offline fallback; see observability.openai_client_class

from . import observability, usage
from .config import (
    get_chat_fallback_model,
    get_openrouter_base_url,
    require_openrouter_api_key,
)

CHAT_TIMEOUT_SECONDS = 30.0

RETRYABLE_STATUSES = {429, 529}
MAX_RETRIES_PER_ROUTE = 2       # retries on the primary route (attempts = 3)
FALLBACK_ATTEMPTS = 1           # one attempt on the fallback route
BACKOFF_BASE_SECONDS = 0.5      # 0.5s, then 1.0s (before jitter)
MAX_RETRY_AFTER_SECONDS = 8.0   # honor Retry-After only up to this clamp
JITTER_LOW, JITTER_HIGH = 0.8, 1.2

Transport = Callable[[dict], dict]
Sleeper = Callable[[float], None]
Jitter = Callable[[], float]


class ProviderUnavailable(RuntimeError):
    """Every chat route is exhausted; callers must answer the safe way."""


class RouteError(RuntimeError):
    """A transport-level failure carrying the HTTP status when known."""

    def __init__(self, status_code: int | None, retry_after: float | None = None,
                 message: str = "") -> None:
        super().__init__(message or f"provider route error (status={status_code})")
        self.status_code = status_code
        self.retry_after = retry_after


@dataclass(frozen=True)
class ChatResult:
    """Normalized successful chat result."""

    content: str
    model: str
    route: str                      # primary | fallback
    provider: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    attempts: int = 1


# ---------------------------------------------------------------------------
# Response normalization (shared by the built-in and classifier transports)


def normalize_chat_response(response, default_model: str) -> dict:
    """Flatten an OpenAI-SDK chat response into a plain dict.

    Anything the response does not carry stays ``None`` (unknown), never
    zero: usage and the upstream provider name are OpenRouter extras.
    """
    usage_obj = getattr(response, "usage", None)
    usage_dict: dict | None = None
    if usage_obj is not None:
        extras = getattr(usage_obj, "model_extra", None) or {}
        usage_dict = {
            "prompt_tokens": getattr(usage_obj, "prompt_tokens", None),
            "completion_tokens": getattr(usage_obj, "completion_tokens", None),
            "total_tokens": getattr(usage_obj, "total_tokens", None),
            "cost_usd": extras.get("cost"),
        }
    provider = getattr(response, "provider", None)
    if provider is None:
        extras = getattr(response, "model_extra", None) or {}
        provider = extras.get("provider")
    try:
        content = response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError):
        raise RouteError(None, None, "malformed chat response") from None
    return {
        "content": content,
        "model": getattr(response, "model", None) or default_model,
        "provider": provider,
        "usage": usage_dict,
    }


def retry_after_seconds(value, *, now=None) -> float | None:
    """Parse a ``Retry-After`` value; seconds and HTTP-dates supported.

    Numeric seconds parse directly. An HTTP-date (RFC 7231, e.g.
    ``Wed, 21 Oct 2026 07:28:00 GMT``) is converted to the positive
    delay it represents from ``now`` (live clock unless injected for
    tests). Invalid, naive or past dates yield ``None`` — the caller
    then uses the exponential backoff.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if moment is None or moment.tzinfo is None:
        return None  # unparsable or naive: not a trustworthy delay
    reference = now or datetime.now(timezone.utc)
    delay = (moment.astimezone(timezone.utc) - reference).total_seconds()
    return delay if delay > 0 else None


def retry_delay(retry_after: float | None, attempt: int,
                jitter: Jitter) -> float:
    """Wait for this retry: a valid ``Retry-After`` capped at the
    configured maximum, else exponential backoff with jitter."""
    if retry_after is not None and retry_after > 0:
        return min(retry_after, MAX_RETRY_AFTER_SECONDS)
    return BACKOFF_BASE_SECONDS * (2 ** attempt) * jitter()


# ---------------------------------------------------------------------------
# Default transport (OpenAI SDK against OpenRouter, max_retries=0)


def openrouter_transport() -> Transport:
    """Build the OpenRouter chat transport used in production.

    The client class is the ``langfuse.openai`` drop-in when tracing is
    enabled (each call is captured as a generation nested under the
    active step) and the plain OpenAI SDK otherwise — offline tests
    never construct instrumentation.
    """
    client = observability.openai_client_class()(
        api_key=require_openrouter_api_key(),
        base_url=get_openrouter_base_url(),
        timeout=CHAT_TIMEOUT_SECONDS,
        max_retries=0,  # bounded: this module owns the retry policy
    )

    def call(request: dict) -> dict:
        try:
            response = client.chat.completions.create(**request)
        except openai.APIStatusError as error:
            headers = getattr(error, "headers", None) or {}
            raise RouteError(
                getattr(error, "status_code", None),
                retry_after_seconds(headers.get("retry-after")),
                str(error),
            ) from None
        except openai.APIConnectionError as error:
            raise RouteError(None, None, str(error)) from None
        return normalize_chat_response(response, request["model"])

    return call


class PolicyChatModel:
    """``.invoke(prompt)``-shaped wrapper so node code stays unchanged."""

    def __init__(self, *, kind: str = usage.KIND_CHAT, model: str | None = None,
                 fallback_model: str | None = None, temperature: float | None = None,
                 max_tokens: int | None = None, extra_body: dict | None = None,
                 name: str | None = None,
                 transport: Transport | None = None,
                 sleeper: Sleeper | None = None, jitter: Jitter | None = None) -> None:
        self._kwargs = dict(
            kind=kind, model=model, fallback_model=fallback_model,
            temperature=temperature, max_tokens=max_tokens,
            extra_body=extra_body, name=name, transport=transport,
            sleeper=sleeper, jitter=jitter)

    def invoke(self, prompt: str) -> SimpleNamespace:
        result = chat_invoke(
            [{"role": "user", "content": prompt}], **self._kwargs)
        return SimpleNamespace(content=result.content)


# ---------------------------------------------------------------------------
# The policy


def chat_routes(model: str | None = None,
                fallback_model: str | None = None) -> list[tuple[str, str]]:
    """Ordered routes: (primary, CHAT_MODEL) then (fallback, …) if set."""
    from .config import _require  # keep config surface explicit at call time

    routes = [("primary", model or _require("CHAT_MODEL", "OpenRouter chat model"))]
    resolved_fallback = fallback_model if fallback_model is not None \
        else get_chat_fallback_model()
    if resolved_fallback:
        routes.append(("fallback", resolved_fallback))
    return routes


def chat_invoke(
    messages: list[dict], *, kind: str = usage.KIND_CHAT,
    model: str | None = None, fallback_model: str | None = None,
    temperature: float | None = 0.0, max_tokens: int | None = None,
    response_format: dict | None = None, extra_body: dict | None = None,
    name: str | None = None,
    transport: Transport | None = None, sleeper: Sleeper | None = None,
    jitter: Jitter | None = None,
) -> ChatResult:
    """Run one chat request under the bounded retry/fallback policy.

    ``name`` labels the Langfuse generation (e.g. ``generate-response``)
    when tracing is enabled; the ``langfuse.openai`` drop-in consumes it
    client-side so it never reaches the OpenRouter API body.
    """
    routes = chat_routes(model, fallback_model)
    call = transport or openrouter_transport()
    wait = sleeper or time.sleep
    wobble = jitter or (lambda: random.uniform(JITTER_LOW, JITTER_HIGH))
    request: dict = {"messages": list(messages)}
    if temperature is not None:
        request["temperature"] = temperature
    if max_tokens is not None:
        request["max_tokens"] = max_tokens
    if response_format is not None:
        request["response_format"] = response_format
    if extra_body is not None:
        request["extra_body"] = dict(extra_body)
    # Only with the instrumented client class: a plain OpenAI request
    # would reject the extra field, so tracing-off sends nothing.
    if name and observability.enabled():
        request["name"] = name

    attempts_total = 0
    for route_name, route_model in routes:
        tries = (MAX_RETRIES_PER_ROUTE + 1) if route_name == "primary" \
            else FALLBACK_ATTEMPTS
        for attempt in range(tries):
            attempts_total += 1
            try:
                raw = call({**request, "model": route_model})
            except RouteError as error:
                usage.record(kind=kind, model=route_model, route=route_name,
                             status="failed", retries=attempt,
                             error=f"status={error.status_code}")
                retryable = error.status_code in RETRYABLE_STATUSES
                if retryable and attempt < tries - 1:
                    wait(retry_delay(error.retry_after, attempt, wobble))
                    continue
                break  # non-retryable or route exhausted: next route
            except Exception as error:  # transport bug/network: next route
                usage.record(kind=kind, model=route_model, route=route_name,
                             status="failed", retries=attempt, error=type(error).__name__)
                break
            payload_usage = raw.get("usage") or {}
            result = ChatResult(
                content=raw.get("content", ""),
                model=raw.get("model") or route_model,
                route=route_name, provider=raw.get("provider"),
                prompt_tokens=payload_usage.get("prompt_tokens"),
                completion_tokens=payload_usage.get("completion_tokens"),
                total_tokens=payload_usage.get("total_tokens"),
                cost_usd=payload_usage.get("cost_usd"),
                attempts=attempts_total,
            )
            usage.record(
                kind=kind, model=result.model, route=route_name,
                provider=result.provider, status="ok",
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                cost_usd=result.cost_usd, retries=attempt)
            return result
    raise ProviderUnavailable(
        f"all chat routes exhausted after {attempts_total} attempt(s)")


# ---------------------------------------------------------------------------
# Route verification (issue #6: different upstream providers, before publication)


def upstream_providers(records=None) -> dict[str, str | None]:
    """Upstream provider last seen per route, from ledger records.

    Reads the given records (e.g. reloaded from a persisted usage log)
    or the current process ledger. A fresh process starts empty — the
    *live* proof is :func:`verify_routes`.
    """
    seen: dict[str, str | None] = {}
    for entry in reversed(records if records is not None else usage.records()):
        if entry.status == "ok" and entry.route and entry.route not in seen:
            seen[entry.route] = entry.provider
    return seen


def fallback_verification(records=None) -> dict:
    """Offline route report over ledger/persisted records.

    ``distinct_upstreams_verified`` is True only when both routes have a
    recorded successful call and the reported upstream providers differ.
    Records may be reloaded from a persisted usage log so evidence
    survives process restarts; for a fresh live proof use
    :func:`verify_routes`.
    """
    routes = chat_routes()
    providers = upstream_providers(records)
    configured_fallback = any(name == "fallback" for name, _ in routes)
    primary = providers.get("primary")
    fallback = providers.get("fallback")
    distinct = bool(primary and fallback and primary != fallback)
    return {
        "primary_model": next(m for name, m in routes if name == "primary"),
        "fallback_configured": configured_fallback,
        "fallback_model": next((m for name, m in routes if name == "fallback"), None),
        "observed_providers": providers,
        "distinct_upstreams_verified": distinct,
    }


# One bounded live probe per route: a 1-token answer is enough to reveal
# the upstream provider, and the budget gate stops the probe when the
# remaining key cap cannot cover it.
VERIFY_PROMPT = "OK"
VERIFY_MAX_TOKENS = 1
VERIFY_MIN_REMAINING_USD = 0.10


def verify_routes(transport: Transport | None = None) -> dict:
    """Real route verification: one minimal live call per route.

    This is the proof an in-memory ledger cannot give from another
    process: each configured route is called once on the supplied key
    with a 1-token answer, the upstream provider is read from the
    response itself, and every probe is recorded in the usage ledger
    (persisted when ``USAGE_LOG`` is configured). Budget-gated before
    any call; a failed probe is reported, never guessed.
    """
    from .embeddings import BudgetExceeded, ensure_budget  # lazy: avoids a cycle

    ensure_budget(VERIFY_MIN_REMAINING_USD)
    call = transport or openrouter_transport()
    report: dict = {}
    for route_name, model in chat_routes():
        try:
            raw = call({
                "model": model,
                "messages": [{"role": "user", "content": VERIFY_PROMPT}],
                "temperature": 0,
                "max_tokens": VERIFY_MAX_TOKENS,
            })
        except RouteError as error:
            usage.record(kind=usage.KIND_CHAT, model=model, route=route_name,
                         status="failed", retries=0,
                         error=f"status={error.status_code}")
            report[route_name] = {"model": model, "provider": None,
                                  "status": "failed",
                                  "error": f"status={error.status_code}"}
            continue
        except Exception as error:  # transport bug/network: probe failed
            usage.record(kind=usage.KIND_CHAT, model=model, route=route_name,
                         status="failed", retries=0,
                         error=type(error).__name__)
            report[route_name] = {"model": model, "provider": None,
                                  "status": "failed",
                                  "error": type(error).__name__}
            continue
        payload_usage = raw.get("usage") or {}
        provider_name = raw.get("provider")
        usage.record(kind=usage.KIND_CHAT, model=raw.get("model") or model,
                     route=route_name, provider=provider_name, status="ok",
                     prompt_tokens=payload_usage.get("prompt_tokens"),
                     completion_tokens=payload_usage.get("completion_tokens"),
                     total_tokens=payload_usage.get("total_tokens"),
                     cost_usd=payload_usage.get("cost_usd"), retries=0)
        report[route_name] = {"model": model, "provider": provider_name,
                              "status": "ok"}
    primary = (report.get("primary") or {}).get("provider")
    fallback = (report.get("fallback") or {}).get("provider")
    report["distinct_upstreams_verified"] = bool(
        primary and fallback and primary != fallback)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI: offline route report (default) or live ``--verify`` probes.

    ``--usage-log <path>`` reads observed providers from a persisted
    redacted usage log so evidence survives process restarts.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m neova.provider",
        description="Route report for the OpenRouter chat policy.")
    parser.add_argument(
        "--verify", action="store_true",
        help="Run one bounded live probe per route (spends the key) and "
             "verify distinct upstream providers.")
    parser.add_argument(
        "--usage-log", default=None,
        help="Path to a persisted redacted usage JSONL to read observed "
             "providers from (instead of this process's memory).")
    arguments = parser.parse_args(argv)

    if arguments.verify:
        try:
            report = verify_routes()
        except Exception as error:  # budget gate or configuration: fail visibly
            print(json.dumps({"error": str(error)}, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    records = usage.load_records(arguments.usage_log) \
        if arguments.usage_log else None
    report = fallback_verification(records)
    if arguments.usage_log:
        report["usage_log"] = arguments.usage_log
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
