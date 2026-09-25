"""Bounded tool adapters wrapping the local FastAPI service and public search.

The conversation graph never touches the database or the HTTP routes
directly for customer actions: reads, the state-changing booking and
handoffs go through this module, which issues real HTTP requests against
the FastAPI app over an in-process ASGI transport (same code path as an
external client, no loopback server dependency). ``httpx`` is already a
runtime dependency of the stack (via langchain-openai → openai) and is
pinned by the dev extra.

Sync transport note: ``httpx.ASGITransport`` is async-only in httpx 0.28,
so the sync bridge is starlette's portal-based client (``TestClient``)
used *outside* any test context: no lifespan is run and no server is
started — each call executes the ASGI app in-process.

Every call runs on a worker thread behind a hard wait bound
(``TOOL_TIMEOUT_SECONDS``): the in-process ASGI transport cannot enforce
httpx per-request timeouts, so the bound is enforced here. On expiry the
caller gets ``ToolError(504, …)`` while the worker may still finish in
the background — which is exactly why a timed-out *write* is decided by
the by-key check in the graph, never by a blind replay.

There is deliberately no open-ended tool loop here: the graph decides
which tool runs at which named node. Failures raise :class:`ToolError`
carrying the HTTP status and a message that is safe to surface.
"""

from __future__ import annotations

import concurrent.futures

from fastapi.testclient import TestClient

from . import retrieval

TOOL_TIMEOUT_SECONDS = 10.0


class ToolError(RuntimeError):
    """A tool call failed with an HTTP status; the detail is customer-safe."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class _RequestTimeout(RuntimeError):
    """Internal: the bounded call exceeded ``TOOL_TIMEOUT_SECONDS``."""


# Calls are sequential in the graph; the pool only needs room for the one
# live call plus any worker still finishing after a timeout expiry.
_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="neova-tool")


def _call_bounded(call):
    """Run ``call`` with a hard wait bound; raise ``_RequestTimeout``."""
    future = _executor.submit(call)
    try:
        return future.result(timeout=TOOL_TIMEOUT_SECONDS)
    except (concurrent.futures.TimeoutError, TimeoutError) as error:
        raise _RequestTimeout(
            f"tool call exceeded {TOOL_TIMEOUT_SECONDS}s") from None


_client: TestClient | None = None


def _transport_client() -> TestClient:
    from .app import app  # lazy import avoids an import cycle at load time

    global _client
    if _client is None:
        _client = TestClient(app, raise_server_exceptions=False)
    return _client


def reset_transport() -> None:
    """Drop the cached in-process transport (used between test apps)."""
    global _client
    _client = None


# Safe-read retry (issue #6): a read is retried exactly once on a
# transient server-side failure (5xx, ASGI failure or timeout); 4xx are
# never retried and the state-changing POST calls are never retried
# here — booking recovery is the by-key check in the graph, never a
# blind write replay.
READ_RETRY_STATUSES = {500, 502, 503, 504}


def _request(method: str, path: str, token: str | None, json_body=None):
    headers = {"X-Demo-Session": token} if token else {}
    attempts = 2 if method == "GET" else 1
    for attempt in range(attempts):
        try:
            response = _call_bounded(lambda: _transport_client().request(
                method, path, json=json_body, headers=headers))
        except _RequestTimeout:
            # Timeout = outcome unknown, never a silent failure: GET gets
            # its single retry; POST surfaces 504 so the graph can verify
            # a booking by key instead of claiming or replaying anything.
            if attempt < attempts - 1:
                continue
            raise ToolError(
                504, "Le service local n'a pas répondu dans le délai imparti") from None
        except RuntimeError:
            # Portals/ASGI failures surface as a bounded service-unavailable;
            # a safe read gets its single retry before giving up.
            if attempt < attempts - 1:
                continue
            raise ToolError(503, "Service local momentanément indisponible") from None
        if response.status_code >= 400:
            if (attempt < attempts - 1
                    and response.status_code in READ_RETRY_STATUSES):
                continue
            try:
                detail = response.json().get("detail", "Requête refusée")
            except ValueError:
                detail = "Requête refusée"
            raise ToolError(response.status_code, str(detail))
        return response.json()


# ---------------------------------------------------------------------------
# Customer-API read tools


def get_summary(token: str, customer_id: str) -> dict:
    return _request("GET", f"/customers/{customer_id}/summary", token)


def get_incidents(token: str) -> list[dict]:
    return _request("GET", "/incidents", token)


def get_slots(token: str, customer_id: str) -> list[dict]:
    return _request("GET", f"/slots?customer_id={customer_id}", token)


def appointment_by_key(token: str, customer_id: str, key: str) -> dict:
    """Safe read by idempotency key; 404 means no saved appointment."""
    return _request("GET", f"/appointments/by-key/{key}", token)


# ---------------------------------------------------------------------------
# State-changing tools (booking, handoff)


def book_appointment(token: str, customer_id: str, slot_id: str,
                     reason_id: str, confirmation_key: str) -> dict:
    """State-changing: only called by the graph after the deterministic
    explicit user yes to the exact slot+reason pair."""
    return _request("POST", "/appointments", token, json_body={
        "customer_id": customer_id,
        "slot_id": slot_id,
        "reason_id": reason_id,
        "confirmation_key": confirmation_key,
    })


def create_handoff(token: str, category_id: str, summary: str,
                   urgency: str) -> dict:
    """State-changing handoff record; the reference is returned only when
    the row is actually stored."""
    return _request("POST", "/handoffs", token, json_body={
        "category_id": category_id, "summary": summary, "urgency": urgency,
    })


# ---------------------------------------------------------------------------
# Public-corpus search (retrieval wrapped as a tool; no HTTP surface)


def search_public(query: str, embed_fn=None, model: str | None = None,
                  mode: str = "hybrid") -> retrieval.SearchOutcome:
    """Hybrid search over the public corpus. Passages are data, never
    instructions; degradation and gate flags are preserved for the
    evidence-check node."""
    from . import db

    conn = db.connect()
    try:
        return retrieval.search(conn, query, embed_fn=embed_fn,
                                model=model, mode=mode)
    finally:
        conn.close()
