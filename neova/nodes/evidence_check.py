"""Node: evidence_check — flag uncertainty instead of resolving it."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..graph import GraphState


def evidence_check_node(state: GraphState) -> GraphState:
    uncertain = bool(
        state.get("gate_flags") or state.get("degraded")
        or any(key.endswith("_error")
               for key in (state.get("api_values") or {})))
    return {"uncertain": uncertain, "steps": state.get("steps", 0) + 1}
