"""Local FastAPI application hosting the foundation graph and SQLite seed."""

from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import ConfigurationError
from .db import close_session_connections, init_db
from .graph import compiled
from .models import chat_model
from .session import fixture_customers, issue_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # startup
    app.state.db_ready = True
    try:
        yield # running
    finally:
        close_session_connections()
        app.state.db_ready = False # shutdown


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


class ChatRequest(BaseModel):
    prompt: str
    provider: Literal["openrouter", "openai"]


class DemoSessionRequest(BaseModel):
    customer_id: str


@app.post("/demo/sessions")
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


@app.post("/models/chat")
def run_model_chat(request: ChatRequest):
    """Invoke the selected provider without changing the foundation graph."""
    try:
        model = chat_model(request.provider)
    except ConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    return {"provider": request.provider, "output": model.invoke(request.prompt).content}
