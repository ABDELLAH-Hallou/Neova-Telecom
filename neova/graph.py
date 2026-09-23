"""Minimal, bounded LangGraph for the foundation.

Input → classify → END. It makes no model calls, touches no customer
data, and its output is explicitly not a customer-facing answer; later
issues replace the classification node with real routing and tools.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph


class GraphState(TypedDict, total=False):
    input: str
    history: list
    classification: str
    output: str


def classify(state: GraphState) -> GraphState:
    """Placeholder routing node; real classification comes later."""
    return {"classification": "foundation_only", "output": NOT_CUSTOMER_FACING}


NOT_CUSTOMER_FACING = (
    "Foundation mode: the customer agent is not implemented yet."
)


def build():
    """Compile the bounded foundation graph."""
    workflow = StateGraph(GraphState)
    workflow.add_node("classify", classify)
    workflow.add_edge(START, "classify")
    workflow.add_edge("classify", END)
    return workflow.compile()


compiled = build()
