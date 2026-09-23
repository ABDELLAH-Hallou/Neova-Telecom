"""Local FastAPI application hosting the foundation graph and SQLite seed."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .db import init_db
from .graph import compiled
from .session import fixture_customers, issue_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.db_ready = True
    try:
        yield
    finally:
        app.state.db_ready = False


app = FastAPI(lifespan=lifespan, title="Neova Foundation API")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": "foundation",
        "db_ready": app.state.db_ready,
        "fixture_customers": len(fixture_customers()),
    }


class GraphRequest(BaseModel):
    prompt: str


class DemoSessionRequest(BaseModel):
    customer_id: str


@app.post("/foundation/sessions")
def create_demo_session(request: DemoSessionRequest):
    """Local fixture selection only; NOT authentication or authorization."""
    try:
        return {"session_token": issue_session(request.customer_id)}
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown fixture customer") from None


@app.post("/foundation/graph")
def run_graph(graph_request: GraphRequest):
    """Exercise the bounded graph, never a customer-facing reply."""
    result = compiled.invoke({"input": graph_request.prompt, "history": []})
    return {"classification": result["classification"], "output": result["output"]}
