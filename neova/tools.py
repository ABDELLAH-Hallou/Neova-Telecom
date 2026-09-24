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

There is deliberately no open-ended tool loop here: the graph decides
which tool runs at which named node. Failures raise :class:`ToolError`
carrying the HTTP status and a message that is safe to surface.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from . import retrieval

TOOL_TIMEOUT_SECONDS = 10.0


class ToolError(RuntimeError):
    """A tool call failed with an HTTP status; the detail is customer-safe."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


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


def _request(method: str, path: str, token: str | None, json_body=None):
    headers = {"X-Demo-Session": token} if token else {}
    try:
        response = _transport_client().request(
            method, path, json=json_body, headers=headers)
    except RuntimeError:
        # Portals/ASGI failures surface as a bounded service-unavailable.
        raise ToolError(503, "Service local momentanément indisponible") from None
    if response.status_code >= 400:
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
