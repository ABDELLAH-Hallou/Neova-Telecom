"""Node: classify — deterministic named routing plus booking continuation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import conversation

if TYPE_CHECKING:
    from ..graph import GraphState


def classify_node(state: GraphState) -> GraphState:
    route, handoff = conversation.classify_request(state["input"])
    token = state.get("session_token")
    if route != "sensitive" and token:
        pending = conversation.pending(token)
        if pending is not None:
            message = state["input"]
            # Deterministic continuation: while a booking is pending, a
            # message that only supplies slot/reason or confirms/declines
            # stays in the booking flow (never re-routed elsewhere).
            if (conversation.is_affirmation(message)
                    or conversation.is_refusal(message)
                    or conversation.extract_slot(
                        message, conversation.offered(token)) is not None
                    or conversation.extract_reason(message) is not None):
                route, handoff = "booking", None
    return {"route": route, "handoff": handoff,
            "steps": state.get("steps", 0) + 1}
