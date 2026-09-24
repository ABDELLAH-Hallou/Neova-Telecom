"""Typed request/response contracts for the local API routes.

Request bodies inherit :class:`RequestModel`, which rejects unexpected JSON
fields. Identifier-like fields never accept blank or whitespace-padded
values; prompts and other free text are bounded.
"""

from typing import Annotated, Literal

from pydantic import BeforeValidator, BaseModel, ConfigDict, Field, field_validator

MAX_IDENTIFIER_LENGTH = 64
MAX_CONFIRMATION_KEY_LENGTH = 128
MAX_SUMMARY_LENGTH = 500
MAX_PROMPT_LENGTH = 5000


def _reject_blank_or_padded(value: str) -> str:
    """Reject blank values and values with leading/trailing whitespace."""
    if not isinstance(value, str):
        return value
    if not value.strip():
        raise ValueError("must not be blank")
    if value != value.strip():
        raise ValueError("must not start or end with whitespace")
    return value


Identifier = Annotated[
    str,
    BeforeValidator(_reject_blank_or_padded),
    Field(min_length=1, max_length=MAX_IDENTIFIER_LENGTH),
]

ConfirmationKey = Annotated[
    str,
    BeforeValidator(_reject_blank_or_padded),
    Field(min_length=1, max_length=MAX_CONFIRMATION_KEY_LENGTH),
]

Prompt = Annotated[str, Field(min_length=1, max_length=MAX_PROMPT_LENGTH)]


class RequestModel(BaseModel):
    """Base class for all request bodies."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CustomerSummary(BaseModel):
    customer_id: str
    plan: str
    monthly_price: float
    balance_due: float
    open_incident_id: str | None


class IncidentRead(BaseModel):
    incident_id: str
    status: str
    cause: str
    started_at: str
    estimated_resolution: str | None
    scope: Literal["linked", "area_only"]


class SlotRead(BaseModel):
    slot_id: str
    start: str
    end: str


class AppointmentRequest(RequestModel):
    customer_id: Identifier
    slot_id: Identifier
    reason_id: Identifier
    confirmation_key: ConfirmationKey


class AppointmentRead(BaseModel):
    appointment_id: int
    customer_id: str
    slot_id: str
    reason_id: str
    start: str
    end: str


class AppointmentResult(AppointmentRead):
    replayed: bool


class HandoffRequest(RequestModel):
    category_id: Identifier
    customer_reference: Identifier | None = None
    summary: str = Field(min_length=1, max_length=MAX_SUMMARY_LENGTH)
    urgency: Literal["normal", "urgent"]

    @field_validator("summary")
    @classmethod
    def factual_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Summary must not be blank")
        return value.strip()


class HandoffResult(BaseModel):
    handoff_id: int
    category_id: str
    customer_reference: str
    urgency: Literal["normal", "urgent"]


class GraphRequest(RequestModel):
    prompt: Prompt


class ChatRequest(RequestModel):
    prompt: Prompt
    provider: Literal["openrouter", "openai"]


class ChatMessage(RequestModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)


class AgentChatRequest(RequestModel):
    message: Prompt
    history: list[ChatMessage] = Field(default_factory=list, max_length=20)


class Citation(BaseModel):
    source_id: str
    source_path: str
    page_start: int
    page_end: int
    section: str


class GateFlag(BaseModel):
    code: str
    message: str


class PendingBookingView(BaseModel):
    customer_id: str
    slot_id: str | None
    slot_label: str | None
    reason_id: str | None
    confirmation_pending: bool


class AgentChatResult(BaseModel):
    reply: str
    route: str
    tools_called: list[str]
    pending_booking: PendingBookingView | None
    handoff_id: int | None
    citations: list[Citation]
    gate_flags: list[GateFlag]
    degraded: list[str]


class DemoSessionRequest(RequestModel):
    customer_id: Identifier