"""Bounded LangGraph conversation graph: named routes, confirmation, handoff.

Routes (issue #5): classify → (public search / local API read tool /
both) → evidence check → French answer, ask one missing detail, or
handoff. The FastAPI reads and the state-changing booking/handoff are
wrapped as bounded tools (``neova.tools``); the graph decides which tool
runs at which named node — there is no open-ended agent loop.

This module owns the graph only: the state definition, the bounded
conditional wiring and the ``run_conversation`` entry point used by
``POST /agent/chat``. Each node lives in its own module under
``neova/nodes/``.

Every turn is a finite DAG with a hard step bound: at most one retrieval
pass, at most two customer-API reads (context summary + one route read),
at most one state-changing call. The booking confirmation gate is
deterministic (``neova.conversation``): only an explicit French
affirmative in the *user's* message to the exact slot+reason pair arms a
booking; a model-generated "yes" can never do so.
"""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .nodes import (
    booking_flow_node,
    classify_node,
    clarify_node,
    evidence_check_node,
    french_answer_node,
    gather_node,
    handoff_node,
    injection_guard_node,
    unsupported_node,
)

MAX_STEPS = 6


# ---------------------------------------------------------------------------
# Conversation graph state


class GraphState(TypedDict, total=False):
    input: str                       # latest user message (raw)
    history: list                    # prior turns [{"role", "content"}]
    session_token: str | None
    customer_id: str | None          # None = anonymous (no demo session)
    route: str
    handoff: dict | None             # topic/urgency/category parameters
    classifier: dict                 # classification trace (source, flags)
    reason_candidate: str            # enum-validated reason from classifier
    tools_called: list
    api_values: dict                 # authorized API values for the prompt
    citations: list                  # public passages (incl. text) for prompt
    gate_flags: list
    degraded: list
    uncertain: bool
    reply: str
    handoff_id: int | None
    steps: int


# ---------------------------------------------------------------------------
# Wiring: bounded conditional edges, no loop


def _after_classify(state: GraphState) -> str:
    route = state.get("route")
    if route == "injection":
        return "injection_guard"
    if route in ("sensitive", "billing_dispute", "unsupported_mutation"):
        return "handoff"
    if route in ("clarify", "out_of_scope"):
        return "clarify"
    if route == "booking":
        return "booking_flow"
    if route == "unsupported":
        return "unsupported"
    return "gather"  # internet, billing, moving, termination


def _after_gather(state: GraphState) -> str:
    route = state.get("route")
    if route == "sensitive_pro":
        return "handoff"
    if route == "moving":
        return "booking_flow"
    return "evidence_check"


def _after_answer(state: GraphState) -> str:
    return "handoff" if state.get("route") == "termination" else "end"


def _after_booking(state: GraphState) -> str:
    return "handoff" if state.get("route") == "sensitive_pro" else "end"


def build_conversation():
    workflow = StateGraph(GraphState)
    workflow.add_node("classify", classify_node)
    workflow.add_node("gather", gather_node)
    workflow.add_node("evidence_check", evidence_check_node)
    workflow.add_node("french_answer", french_answer_node)
    workflow.add_node("booking_flow", booking_flow_node)
    workflow.add_node("handoff", handoff_node)
    workflow.add_node("unsupported", unsupported_node)
    workflow.add_node("clarify", clarify_node)
    workflow.add_node("injection_guard", injection_guard_node)
    workflow.add_edge(START, "classify")
    workflow.add_conditional_edges("classify", _after_classify, {
        "gather": "gather", "booking_flow": "booking_flow",
        "handoff": "handoff", "unsupported": "unsupported",
        "clarify": "clarify", "injection_guard": "injection_guard"})
    workflow.add_conditional_edges("gather", _after_gather, {
        "evidence_check": "evidence_check", "booking_flow": "booking_flow",
        "handoff": "handoff"})
    workflow.add_edge("evidence_check", "french_answer")
    workflow.add_conditional_edges("french_answer", _after_answer, {
        "handoff": "handoff", "end": END})
    workflow.add_conditional_edges("booking_flow", _after_booking, {
        "handoff": "handoff", "end": END})
    workflow.add_edge("handoff", END)
    workflow.add_edge("unsupported", END)
    workflow.add_edge("clarify", END)
    workflow.add_edge("injection_guard", END)
    return workflow.compile()


conversation_compiled = build_conversation()


# ---------------------------------------------------------------------------
# Entry point used by POST /agent/chat


def run_conversation(message: str, history: list | None,
                     session_token: str | None,
                     customer_id: str | None) -> dict:
    """Run one bounded turn and return the trace-safe result payload."""
    initial: GraphState = {
        "input": message,
        "history": [dict(turn) for turn in (history or [])],
        "session_token": session_token,
        "customer_id": customer_id,
        "classifier": {},
        "reason_candidate": "",
        "tools_called": [],
        "api_values": {},
        "citations": [],
        "gate_flags": [],
        "degraded": [],
        "steps": 0,
    }
    result = conversation_compiled.invoke(initial)
    if result.get("steps", 0) > MAX_STEPS:
        raise RuntimeError("Conversation graph exceeded its bounded step count")
    return {
        "reply": result.get("reply", ""),
        "route": result.get("route", "unknown"),
        "classification": dict(result.get("classifier") or {}),
        "tools_called": result.get("tools_called", []),
        "pending_booking": _pending_view(session_token),
        "handoff_id": result.get("handoff_id"),
        "citations": [
            {key: citation[key] for key in
             ("source_id", "source_path", "page_start", "page_end", "section")}
            for citation in result.get("citations", [])
        ],
        "gate_flags": [dict(flag) for flag in result.get("gate_flags", [])],
        "degraded": list(result.get("degraded", [])),
    }


def _pending_view(session_token: str | None) -> dict | None:
    from . import conversation  # lazy: keeps graph import light

    return conversation.pending_view(session_token)
