"""Local FastAPI application hosting the customer agent and SQLite seed."""

from contextlib import asynccontextmanager, closing

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query

from . import chunking, customer_api, db, observability, usage
from .dto import (
    AgentChatRequest,
    AgentChatResult,
    AppointmentRead,
    AppointmentRequest,
    AppointmentResult,
    CustomerSummary,
    DemoSessionRequest,
    HandoffRequest,
    HandoffResult,
    IncidentRead,
    SlotRead,
)
from .db import close_session_connections, init_db
from .graph import run_conversation
from .session import fixture_customers, issue_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    usage.configure_from_env()  # persist redacted usage when USAGE_LOG is set
    init_db()  # startup
    # A clean checkout must have a searchable public corpus after this one
    # server command. Build the local FTS5 index without paid embedding calls;
    # the explicit retrieval index command remains the budget-gated vector path.
    with closing(db.connect()) as conn:
        db.init_retrieval_schema(conn)
        if db.chunk_count(conn) == 0:
            chunks = chunking.public_chunks()
            if not chunks:
                raise RuntimeError("No public corpus chunks available")
            db.sync_sources_table(conn)
            db.replace_chunk_index(conn, chunks)
    app.state.db_ready = True
    try:
        yield # running
    finally:
        close_session_connections()
        observability.shutdown()  # flush and release any Langfuse client
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
