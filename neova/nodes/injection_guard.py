"""Node: injection_guard — hard boundary for prompt-injection attempts.

Reached when the semantic classifier flags ``prompt_injection`` or when
the code-side heuristic matches an override attempt. The boundary is
enforced by wiring, not by trust: this node runs no tools, reads no
customer data, performs no database write, and never quotes the
customer's injection text back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..graph import GraphState

INJECTION_REPLY = (
    "Je ne peux pas traiter cette demande. Pour des raisons de sécurité, "
    "je n'exécute que les demandes client standard. Vous pouvez contacter "
    "un conseiller Néova via nos canaux officiels.")


def injection_guard_node(state: GraphState) -> GraphState:
    return {"reply": INJECTION_REPLY,
            "tools_called": list(state.get("tools_called", [])),
            "steps": state.get("steps", 0) + 1}
