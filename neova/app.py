"""Local FastAPI application hosting the customer agent and SQLite seed."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query

from . import customer_api, db
from .config import ConfigurationError
from .dto import (
    AgentChatRequest,
    AgentChatResult,
    AppointmentRead,
    AppointmentRequest,
    AppointmentResult,
    ChatRequest,
    CustomerSummary,
    DemoSessionRequest,
    HandoffRequest,
    HandoffResult,
    IncidentRead,
    SlotRead,
)
from .db import close_session_connections, init_db
from .graph import run_conversation
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


app = FastAPI(lifespan=lifespan, title="Neova Telecom Customer Agent API")


def demo_session(x_demo_session: str | None = Header(default=None, alias="X-Demo-Session")) -> tuple[str, str]:
    return x_demo_session, customer_api.bound_customer(x_demo_session)


def optional_demo_session(x_demo_session: str | None = Header(default=None, alias="X-Demo-Session")) -> tuple[str | None, str | None]:
    """Bound the session when one is presented; anonymous otherwise.

    Anonymous turns get public-corpus answers and a generic human route
    without any private data and without stored records.
    """
    if x_demo_session is None:
        return None, None
    return x_demo_session, customer_api.bound_customer(x_demo_session)


@app.get("/customers/{customer_id}/summary", response_model=CustomerSummary)
def customer_summary(customer_id: str, session: tuple[str, str] = Depends(demo_session)):
    token, bound_id = session
    customer_api.require_customer(bound_id, customer_id)
    return customer_api.summary(token, bound_id)


@app.get("/incidents", response_model=list[IncidentRead])
def incidents(session: tuple[str, str] = Depends(demo_session)):
    token, bound_id = session
    return customer_api.incidents(token, bound_id)


@app.get("/slots", response_model=list[SlotRead])
def slots(customer_id: str = Query(...), session: tuple[str, str] = Depends(demo_session)):
    token, bound_id = session
    customer_api.require_customer(bound_id, customer_id)
    return customer_api.slots(token, bound_id)


@app.post("/appointments", response_model=AppointmentResult, status_code=201)
def book_appointment(request: AppointmentRequest, session: tuple[str, str] = Depends(demo_session)):
    token, bound_id = session
    customer_api.require_customer(bound_id, request.customer_id)
    try:
        return db.book_appointment(token, bound_id, request.slot_id, request.reason_id,
                                   request.confirmation_key)
    except db.BookingFailure as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from None


@app.get("/appointments/by-key/{key}", response_model=AppointmentRead)
def appointment_by_key(key: str = Path(min_length=1, max_length=128),
                       session: tuple[str, str] = Depends(demo_session)):
    token, bound_id = session
    return customer_api.appointment(token, bound_id, key)


@app.post("/handoffs", response_model=HandoffResult, status_code=201)
def create_handoff(request: HandoffRequest, session: tuple[str, str] = Depends(demo_session)):
    token, bound_id = session
    if request.customer_reference is not None:
        customer_api.require_customer(bound_id, request.customer_reference)
    try:
        return db.save_handoff(token, bound_id, request.category_id,
                               request.summary, request.urgency)
    except ValueError:
        raise HTTPException(status_code=422, detail="Unknown handoff category") from None


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": "customer_agent",
        "db_ready": app.state.db_ready,
        "fixture_customers": len(fixture_customers()),
    }


@app.post("/demo/sessions")
def create_demo_session(request: DemoSessionRequest):
    """Local fixture selection only; NOT authentication or authorization."""
    try:
        return {"session_token": issue_session(request.customer_id)}
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown fixture customer") from None


@app.post("/agent/chat", response_model=AgentChatResult)
def agent_chat(request: AgentChatRequest, session: tuple[str | None, str | None] = Depends(optional_demo_session)):
    """One bounded conversation turn: named routes, grounded French answer,
    deterministic booking confirmation, immediate handoffs."""
    token, customer_id = session
    return run_conversation(
        request.message, [message.model_dump() for message in request.history],
        token, customer_id)


@app.post("/models/chat")
def run_model_chat(request: ChatRequest):
    """Invoke the selected provider directly; the conversation graph does
    its own bounded model calls via ``neova.nodes.french_answer``."""
    try:
        model = chat_model(request.provider)
    except ConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None
    return {"provider": request.provider, "output": model.invoke(request.prompt).content}
