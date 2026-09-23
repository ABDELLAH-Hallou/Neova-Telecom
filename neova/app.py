"""FastAPI app factory and lifespan with LangGraph entry point."""

import os
from contextlib import asynccontextmanager
from typing import TypedDict

from fastapi import FastAPI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from .config import API_BASE_URL, CHAT_MODEL, OPENROUTER_API_KEY
from .db import init_db
from .session import FIXTURE_CUSTOMERS


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB and verify config before serving."""
    init_db()
    # Verify required OpenRouter key exists (errors are redacted in config)
    if not OPENROUTER_API_KEY or OPENROUTER_API_KEY.startswith("sk-or-v1-replace"):
        raise RuntimeError("OPENROUTER_API_KEY must be set to a real key")
    if not CHAT_MODEL:
        raise RuntimeError("CHAT_MODEL must be set")
    app.state.clients = {}
    try:
        yield
    finally:
        app.state.clients.clear()


app = FastAPI(lifespan=lifespan, title="Néova Telecom Foundation API")


# Minimal, non-customer-facing graph: input → classify → end
# Intentionally does not answer customer questions; marks foundation-only.


class GraphState(TypedDict, total=False):
    input: str
    history: list
    classification: str
    output: str


def classify(state: GraphState) -> GraphState:
    """Classify the request type (placeholder)."""
    # In a real implementation, this would route to tools
    return {"classification": "foundation_test", "output": "foundation mode active"}


def route(state: GraphState) -> str:
    """Route to the appropriate next node."""
    return END


workflow = StateGraph(GraphState)
workflow.add_node("classify", classify)
workflow.add_edge(START, "classify")
workflow.add_edge("classify", END)

compiled = workflow.compile()


@app.get("/health")
def health():
    """Health check returning basic app metadata."""
    return {
        "status": "ok",
        "mode": "foundation",
        "db_ready": True,
        "fixture_customers": len(FIXTURE_CUSTOMERS),
        "api_base_url": API_BASE_URL,
    }


class GraphRequest(BaseModel):
    prompt: str
@app.post("/foundation/graph")
def run_graph(graph_request: GraphRequest):
    """Run the minimal graph for testing; not a customer endpoint."""
    result = compiled.invoke({"input": graph_request.prompt, "history": []})
    return result
