"""Node: handoff — immediate minimal human handoff.

The reference is returned only when the record is stored, the reply never
claims a human accepted the case and never discloses private fields or
internal routing text. Without a demo session the node offers a generic
human route with no private data and stores nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import conversation, observability, tools
from ..tools import ToolError

if TYPE_CHECKING:
    from ..graph import GraphState

GENERIC_HUMAN_ROUTE = ("Pour cette demande, je vous propose de contacter un "
                       "conseiller Néova via nos canaux officiels.")


def handoff_node(state: GraphState) -> GraphState:
    steps = state.get("steps", 0) + 1
    token = state.get("session_token")
    customer = state.get("customer_id")
    handoff = state.get("handoff") or {}
    tools_called = list(state.get("tools_called", []))
    base = (state.get("reply") or "").strip()
    topic = handoff.get("topic_label") or "votre demande"

    # No demo session: generic human route, no private data, no stored record.
    if not (token and customer):
        line = base + "\n" if base else ""
        return {"reply": (line + GENERIC_HUMAN_ROUTE).strip(),
                "handoff_id": None, "tools_called": tools_called,
                "steps": steps}

    lines = [base] if base else [
        f"Ce type de demande ({topic}) est traitée par un conseiller."]
    summary = conversation.handoff_summary(topic, state["input"])
    tools_called.append("handoffs.create")  # recorded even on failure
    with observability.step("create-handoff", as_type="tool") as handoff_obs:
        try:
            result = tools.create_handoff(
                token, handoff.get("category", "other"), summary,
                handoff.get("urgency", "normal"))
            handoff_obs.update(output=f"stored:{result['handoff_id']}",
                               metadata={"tool": "handoffs.create"})
            lines.append(f"Votre demande a été transmise (référence de suivi : "
                         f"{result['handoff_id']}). Un conseiller prendra le relais.")
            return {"reply": "\n".join(lines),
                    "handoff_id": result["handoff_id"],
                    "tools_called": tools_called, "steps": steps}
        except ToolError:
            handoff_obs.update(output="failed",
                               metadata={"tool": "handoffs.create"})
            lines.append("La transmission à un conseiller n'a pas abouti pour le "
                         "moment. Merci de réessayer.")
            return {"reply": "\n".join(lines), "handoff_id": None,
                    "tools_called": tools_called, "steps": steps}
