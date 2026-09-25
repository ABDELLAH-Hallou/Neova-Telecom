"""One cheap OpenRouter classification call per conversation turn.

The workflow is: user message → this classification call (intent,
out_of_scope, ambiguity, injection risk, reason candidate) → Pydantic
and policy validation → deterministic LangGraph routing → model-assisted
answer → deterministic confirmation and database writes.

The classifier is strictly a classifier: the system prompt forbids it
from following instructions contained in the customer message, and the
answer is validated against fixed enums with Pydantic. Any failure
(missing configuration, network error, bad JSON, enum violation)
degrades to the deterministic keyword router in ``neova.conversation`` —
never to a guessed route. One bounded classification call per turn; the
wait/fallback policy for that call lives in ``neova.provider`` (issue #6).
"""

from __future__ import annotations

import json
from typing import Literal

import openai  # exception classes for the OpenRouter transport
from openai import OpenAI  # default client class; tests patch this seam

from pydantic import BaseModel, ConfigDict, ValidationError

from . import observability, provider, usage
from .config import (
    get_chat_fallback_model,
    get_classifier_model,
    get_openrouter_base_url,
    require_openrouter_api_key,
)
from .prompt import load

CLASSIFIER_TIMEOUT_SECONDS = 15.0
MAX_TOKENS = 150

INTENTS = (
    "internet", "billing", "billing_dispute", "moving", "booking",
    "termination", "sensitive", "unsupported_mutation", "unsupported",
)
REASON_CANDIDATES = ("no_internet", "slow_internet", "installation",
                     "equipment_swap", "unknown")

Intent = Literal["internet", "billing", "billing_dispute", "moving",
                 "booking", "termination", "sensitive",
                 "unsupported_mutation", "unsupported"]
ReasonCandidate = Literal["no_internet", "slow_internet", "installation", "equipment_swap", "unknown"]

class Classification(BaseModel):
    """Validated classifier output; enums are fixed by policy."""

    model_config = ConfigDict(extra="forbid", strict=True)

    intent: Intent
    out_of_scope: bool
    ambiguous: bool
    prompt_injection: bool
    reason_candidate: ReasonCandidate


class ClassifierError(RuntimeError):
    """The classifier could not produce a valid classification."""


def trace(classification: Classification | None, *, source: str,
          degraded: bool) -> dict:
    """Trace-safe payload for the conversation result."""
    if classification is None:
        return {"intent": None, "out_of_scope": False, "ambiguous": False,
                "prompt_injection": False, "reason_candidate": None,
                "source": source, "degraded": degraded}
    return {
        "intent": classification.intent,
        "out_of_scope": classification.out_of_scope,
        "ambiguous": classification.ambiguous,
        "prompt_injection": classification.prompt_injection,
        "reason_candidate": classification.reason_candidate,
        "source": source, "degraded": degraded,
    }


def _extract_json(raw: str) -> dict:
    """Parse the model output into a JSON object; never guess."""
    text = (raw or "").strip()
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise ClassifierError(f"Invalid classifier JSON: {error}") from None
    if not isinstance(payload, dict):
        raise ClassifierError("Classifier JSON is not an object")
    return payload


def classify_message(message: str, invoke) -> Classification:
    """Call the injected classifier and validate its output with Pydantic."""
    raw = invoke(message)
    try:
        return Classification.model_validate(_extract_json(raw))
    except ValidationError as error:
        raise ClassifierError(f"Invalid classification: {error}") from None


def openrouter_classifier():
    """Build the OpenRouter classifier; fails closed on missing config.

    Returns an ``invoke(message) -> str`` callable using one cheap call:
    temperature 0, max_tokens 150, reasoning disabled. The call runs
    through the shared bounded retry/fallback policy
    (``neova.provider``): the underlying client keeps ``max_retries=0``
    so this policy is the only retry layer.

    With Langfuse tracing enabled the client class is the
    ``langfuse.openai`` drop-in, so the classification call is captured
    as a ``classify-intent`` generation; otherwise the module-level
    ``OpenAI`` class is used (the test patch point).
    """
    client = (observability.openai_client_class() if observability.enabled()
              else OpenAI)(
        api_key=require_openrouter_api_key(),
        base_url=get_openrouter_base_url(),
        timeout=CLASSIFIER_TIMEOUT_SECONDS,
        max_retries=0,  # bounded; the shared policy owns retries
    )
    model = get_classifier_model()

    def _transport(request: dict) -> dict:
        try:
            response = client.chat.completions.create(**request)
        except openai.APIStatusError as error:
            headers = getattr(error, "headers", None) or {}
            raise provider.RouteError(
                getattr(error, "status_code", None),
                provider.retry_after_seconds(headers.get("retry-after")),
                str(error),
            ) from None
        except openai.APIConnectionError as error:
            raise provider.RouteError(None, None, str(error)) from None
        return provider.normalize_chat_response(response, request["model"])

    def invoke(message: str) -> str:
        result = provider.chat_invoke(
            [
                {"role": "system", "content": load("classifier.md")},
                {"role": "user", "content": message},
            ],
            kind=usage.KIND_CLASSIFIER,
            model=model,
            fallback_model=get_chat_fallback_model(),
            temperature=0,
            max_tokens=MAX_TOKENS,
            name="classify-intent",
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "neova_classification",
                    "strict": True,
                    "schema": Classification.model_json_schema(),
                },
            },
            extra_body={
                "reasoning": {"enabled": False},
                "provider": {"require_parameters": True},
            },
            transport=_transport,
        )
        return result.content

    return invoke
