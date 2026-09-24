"""Node: clarify — explicit out-of-scope refusal and ambiguity handling.

The semantic classifier sets ``out_of_scope`` (unrelated to Neova
customer service) or ``ambiguous`` (not routable). Both stop here: no
tool calls, no data access, one polite French reply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..graph import GraphState

OUT_OF_SCOPE_REPLY = (
    "Cette demande ne relève pas du service client Néova Télécom. "
    "Je peux vous aider sur : les pannes internet, la facturation, le "
    "déménagement, les rendez-vous technicien et la résiliation.")

_AMBIGUOUS_REPLY = (
    "Je n'ai pas bien compris votre demande. Pouvez-vous la reformuler ? "
    "Par exemple : une panne internet, une question de facturation, un "
    "rendez-vous technicien ou une résiliation.")


def clarify_node(state: GraphState) -> GraphState:
    reply = (OUT_OF_SCOPE_REPLY if state.get("route") == "out_of_scope"
             else _AMBIGUOUS_REPLY)
    return {"reply": reply, "steps": state.get("steps", 0) + 1}
