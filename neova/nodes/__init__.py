"""Conversation-graph nodes, one module per node.

Each node is a pure function of the graph state (``neova.graph.GraphState``);
``neova/graph.py`` owns the state, the bounded conditional wiring and the
``POST /agent/chat`` entry point. Nodes never call each other directly —
the routing helpers in ``neova.graph`` decide what runs next, so every
turn stays a finite DAG with no agent loop.
"""

from .booking_flow import booking_flow_node
from .classify import classify_node
from .evidence_check import evidence_check_node
from .french_answer import french_answer_node
from .gather import gather_node
from .handoff import handoff_node
from .unsupported import unsupported_node

__all__ = [
    "booking_flow_node",
    "classify_node",
    "evidence_check_node",
    "french_answer_node",
    "gather_node",
    "handoff_node",
    "unsupported_node",
]
