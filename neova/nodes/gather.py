"""Node: gather — public search and/or local API read, bounded.

At most two customer-API reads happen here: the context summary (which
also performs the mandatory Pro-contract check) and one route-specific
read (incidents for internet). Each safe read is retried once on a
transient server failure (``neova.tools``); if the read still fails, the
failure is recorded and surfaced so the answer node gives a short French
failure with a handoff offer instead of answering on partial data. The
single retrieval pass degrades visibly to FTS5 when no embedder is
configured.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import observability, tools
from ..tools import ToolError

if TYPE_CHECKING:
    from ..graph import GraphState

MAX_PROMPT_CHARS = 300


def embedder_factory():
    """Configured embedder, or None: search degrades visibly to FTS5.

    Patch point for tests (same pattern as the retrieval FakeEmbedder).
    """
    try:
        from ..embeddings import openrouter_embedder
        return openrouter_embedder()
    except Exception:
        return None


def _citation(passage) -> dict:
    return {
        "source_id": passage.source_id,
        "source_path": passage.source_path,
        "page_start": passage.page_start,
        "page_end": passage.page_end,
        "section": passage.section,
        "text": passage.text,
    }


def gather_node(state: GraphState) -> GraphState:
    steps = state.get("steps", 0) + 1
    token = state.get("session_token")
    customer = state.get("customer_id")
    route = state["route"]
    tools_called = list(state.get("tools_called", []))
    api_values = dict(state.get("api_values", {}))
    degraded = list(state.get("degraded", []))
    updates: dict[str, Any] = {"steps": steps, "tools_called": tools_called,
                               "api_values": api_values, "degraded": degraded}

    # Context read: the mandatory Pro-contract check on business routes.
    if customer and token and route != "termination":
        tools_called.append("customer_summary.read")  # attempted, even if exhausted
        with observability.step(
                "read-customer-summary", as_type="tool") as read_obs:
            try:
                summary = tools.get_summary(token, customer)
                summary.pop("customer_id", None)  # no identifiers in the prompt
                read_obs.update(output="ok",
                                metadata={"tool": "customer_summary.read"})
                api_values["summary"] = summary
            except ToolError as error:
                read_obs.update(output=f"failed:{error.status_code}",
                                metadata={"tool": "customer_summary.read"})
                api_values["summary_error"] = error.detail
                api_values["read_failure"] = {
                    "read": "customer_summary", "status": error.status_code,
                    "detail": error.detail}
                degraded.append("api_read_failed")
            else:
                if "pro" in str(api_values.get("summary", {}).get(
                        "plan", "")).lower():
                    updates["route"] = "sensitive_pro"
                    updates["handoff"] = {
                        "topic": "client Pro",
                        "topic_label": "contrat professionnel",
                        "urgency": "normal", "category": "other",
                    }
                    return updates

    if route == "internet" and token and customer:
        tools_called.append("incidents.read")  # attempted, even if exhausted
        with observability.step("read-incidents", as_type="tool") as read_obs:
            try:
                api_values["incidents"] = tools.get_incidents(token)
                read_obs.update(output="ok", metadata={"tool": "incidents.read"})
            except ToolError as error:
                read_obs.update(output=f"failed:{error.status_code}",
                                metadata={"tool": "incidents.read"})
                api_values["incidents_error"] = error.detail
                api_values["read_failure"] = {
                    "read": "incidents", "status": error.status_code,
                    "detail": error.detail}
                degraded.append("api_read_failed")

    # A failed essential read ends the turn here: the retrieval pass is
    # skipped entirely so no embedding credit is spent on a turn that
    # will end in the short French failure + handoff offer.
    if "read_failure" in api_values:
        updates["degraded"] = degraded + ["search_skipped_read_failure"]
        return updates

    # At most one retrieval pass; unconfigured embedder degrades to FTS5.
    embed = embedder_factory()
    retrieval_mode = "hybrid" if embed else "fts"
    with observability.step("retrieve-context", as_type="retriever",
                            input=state["input"][:MAX_PROMPT_CHARS]) as retrieval_obs:
        try:
            outcome = tools.search_public(
                state["input"][:MAX_PROMPT_CHARS], embed_fn=embed,
                mode=retrieval_mode)
        except Exception:
            outcome = None
            degraded.append("search_unavailable")
        if outcome is not None:
            retrieval_obs.update(
                output=str(len(outcome.results)),
                metadata={"mode": retrieval_mode,
                          "degraded": ",".join(outcome.degraded) or "none",
                          "sources": ",".join(
                              passage.source_id for passage in outcome.results)})
    if outcome is not None:
        updates["citations"] = [_citation(passage) for passage in outcome.results]
        updates["gate_flags"] = outcome.gate_flags
        updates["degraded"] = degraded + list(outcome.degraded)
    return updates
