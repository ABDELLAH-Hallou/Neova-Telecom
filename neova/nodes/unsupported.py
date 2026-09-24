"""Node: unsupported — polite bounded refusal, no handoff POST."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..graph import GraphState


def unsupported_node(state: GraphState) -> GraphState:
    return {"reply": ("Je ne peux pas traiter cette demande dans le cadre de "
                      "l'assistant Néova. Souhaitez-vous être mis en relation "
                      "avec un conseiller via nos canaux officiels ?"),
            "steps": state.get("steps", 0) + 1}
