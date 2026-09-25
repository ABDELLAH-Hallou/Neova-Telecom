"""OpenRouter embeddings client with a bounded budget gate and usage log.

Live calls are optional: every function accepts an injected embedder so
tests run offline. The live path uses only the supplied OpenRouter key,
its non-streaming ``/embeddings`` endpoint, bounded retries (honor
``Retry-After`` when present, else exponential backoff with jitter,
through an injectable sleeper) and a budget gate that stops optional
calls when the remaining key cap is inadequate. Every attempt is
recorded in ``neova.usage``; usage the API did not return stays
unknown, never zero.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from typing import Callable, Sequence

from . import usage
from .config import (
    get_embedding_model,
    get_openrouter_base_url,
    require_openrouter_api_key,
)
from .provider import retry_after_seconds, retry_delay

EmbedFn = Callable[[list[str]], list[list[float]]]

MAX_ATTEMPTS = 3  # initial call + at most two retries, like the chat policy
# Minimum remaining key cap (USD) required before any optional live batch.
MIN_REMAINING_USD = 1.0


class EmbeddingError(RuntimeError):
    """Raised when the embeddings endpoint stays unavailable after retries."""


class BudgetExceeded(RuntimeError):
    """Raised when the supplied key's remaining cap cannot cover the run."""


def _post_json(url: str, payload: dict, api_key: str) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_json(url: str, api_key: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def key_status() -> dict:
    """Return the supplied key's usage, limit and limit_remaining (USD)."""
    api_key = require_openrouter_api_key()
    base = get_openrouter_base_url().rstrip("/")
    data = _get_json(f"{base}/key", api_key).get("data", {})
    return {
        "usage": float(data.get("usage", 0.0)),
        "limit": float(data.get("limit", 0.0)),
        "limit_remaining": float(data.get("limit_remaining", 0.0)),
    }


def ensure_budget(min_remaining_usd: float = MIN_REMAINING_USD) -> dict:
    """Fail closed before optional live calls when the cap is inadequate."""
    status = key_status()
    if status["limit_remaining"] < min_remaining_usd:
        raise BudgetExceeded(
            f"Remaining key cap {status['limit_remaining']:.2f} USD is below "
            f"the {min_remaining_usd:.2f} USD needed for this optional run."
        )
    return status


_RETRYABLE_CODES = {408, 429, 500, 502, 503, 529}


def openrouter_embedder(min_remaining_usd: float = MIN_REMAINING_USD, *,
                        sleeper: Callable[[float], None] | None = None,
                        jitter: Callable[[], float] | None = None) -> EmbedFn:
    """Build the live non-streaming embeddings function for the corpus.

    Bounded retry (initial call + at most two retries) on 408/429/5xx/529:
    a ``Retry-After`` header is honored when present (clamped), otherwise
    exponential backoff with jitter — through the injectable sleeper so
    tests assert waits deterministically. A budget gate runs before each
    batch so optional live calls stop when the remaining supplied-key cap
    is inadequate. Every attempt is recorded in ``neova.usage``.
    """
    api_key = require_openrouter_api_key()
    base = get_openrouter_base_url().rstrip("/")
    model = get_embedding_model()
    wait = sleeper or time.sleep
    wobble = jitter or (lambda: random.uniform(0.8, 1.2))

    def embed(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        ensure_budget(min_remaining_usd)
        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = _post_json(
                    f"{base}/embeddings", {"model": model, "input": texts}, api_key
                )
                ordered = sorted(response["data"], key=lambda item: item["index"])
                vectors = [item["embedding"] for item in ordered]
            except urllib.error.HTTPError as error:
                last_error = error
                usage.record(kind=usage.KIND_EMBEDDINGS, model=model,
                             status="failed", retries=attempt,
                             error=f"status={error.code}")
                if error.code not in _RETRYABLE_CODES:
                    break
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as error:
                last_error = error
                usage.record(kind=usage.KIND_EMBEDDINGS, model=model,
                             status="failed", retries=attempt,
                             error=type(error).__name__)
            else:
                # Exactly one record per attempt, written only after the
                # response has been validated as a usable embedding batch —
                # a malformed response can never be logged as both
                # successful and failed.
                payload_usage = response.get("usage") or {}
                usage.record(
                    kind=usage.KIND_EMBEDDINGS, model=model, status="ok",
                    prompt_tokens=payload_usage.get("prompt_tokens"),
                    # Embeddings generate no completion tokens; the batch
                    # size is the API's ``total_tokens``.
                    completion_tokens=None,
                    total_tokens=payload_usage.get("total_tokens"),
                    cost_usd=payload_usage.get("cost"), retries=attempt)
                return vectors
            if attempt < MAX_ATTEMPTS - 1:
                retry_after = None
                if isinstance(last_error, urllib.error.HTTPError):
                    retry_after = retry_after_seconds(
                        last_error.headers.get("retry-after"))
                wait(retry_delay(retry_after, attempt, wobble))
        raise EmbeddingError(f"Embedding request failed after retries: {last_error}")

    return embed


def vector_to_blob(vector: list[float]) -> bytes:
    """Serialize one embedding vector for SQLite storage."""
    return json.dumps(vector).encode("utf-8")


def blob_to_vector(blob: bytes) -> list[float]:
    return json.loads(blob.decode("utf-8"))


def cosine(left: list[float], right: list[float]) -> float:
    """Plain cosine similarity; zero vector yields 0.0, never an error."""
    if len(left) != len(right):
        raise EmbeddingError("Embedding dimension mismatch")
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)