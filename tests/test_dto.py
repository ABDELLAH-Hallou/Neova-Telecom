"""Offline validation contracts for the request DTOs."""

import pytest
from pydantic import ValidationError

from neova.dto import (
    MAX_IDENTIFIER_LENGTH,
    MAX_PROMPT_LENGTH,
    AgentChatRequest,
    AppointmentRequest,
    DemoSessionRequest,
    HandoffRequest,
)

APPOINTMENT = {
    "customer_id": "NEO-88213",
    "slot_id": "SLOT-7A31",
    "reason_id": "no_internet",
    "confirmation_key": "claim-1",
}
HANDOFF = {
    "category_id": "technical",
    "summary": "Diagnostic nécessaire",
    "urgency": "normal",
}


def test_request_models_reject_extra_fields():
    with pytest.raises(ValidationError):
        AppointmentRequest.model_validate({**APPOINTMENT, "extra": "field"})
    with pytest.raises(ValidationError):
        HandoffRequest.model_validate({**HANDOFF, "extra": "field"})
    with pytest.raises(ValidationError):
        AgentChatRequest.model_validate({"message": "Bonjour", "extra": "injected instruction"})
    with pytest.raises(ValidationError):
        DemoSessionRequest.model_validate({"customer_id": "NEO-88213", "extra": 1})


@pytest.mark.parametrize("identifier_field", ["customer_id", "slot_id", "reason_id"])
@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_appointment_identifiers_reject_blank_and_whitespace(identifier_field, blank):
    with pytest.raises(ValidationError):
        AppointmentRequest.model_validate({**APPOINTMENT, identifier_field: blank})


@pytest.mark.parametrize("blank", ["", "   ", "\t\n "])
def test_other_identifier_fields_reject_blank_and_whitespace(blank):
    with pytest.raises(ValidationError):
        HandoffRequest.model_validate({**HANDOFF, "category_id": blank})
    with pytest.raises(ValidationError):
        HandoffRequest.model_validate({**HANDOFF, "customer_reference": blank})
    with pytest.raises(ValidationError):
        DemoSessionRequest.model_validate({"customer_id": blank})


def test_identifiers_reject_padded_and_oversized_values():
    with pytest.raises(ValidationError):
        AppointmentRequest.model_validate({**APPOINTMENT, "customer_id": " NEO-88213 "})
    longest = "x" * MAX_IDENTIFIER_LENGTH
    accepted = AppointmentRequest.model_validate({**APPOINTMENT, "slot_id": longest})
    assert accepted.slot_id == longest
    with pytest.raises(ValidationError):
        AppointmentRequest.model_validate({**APPOINTMENT, "slot_id": "x" * (MAX_IDENTIFIER_LENGTH + 1)})
    with pytest.raises(ValidationError):
        HandoffRequest.model_validate({**HANDOFF, "category_id": "x" * (MAX_IDENTIFIER_LENGTH + 1)})


def test_confirmation_key_rejects_empty_blank_and_oversized():
    for bad_key in ("", " ", " \t "):
        with pytest.raises(ValidationError):
            AppointmentRequest.model_validate({**APPOINTMENT, "confirmation_key": bad_key})
    with pytest.raises(ValidationError):
        AppointmentRequest.model_validate({**APPOINTMENT, "confirmation_key": "x" * 129})
    accepted = AppointmentRequest.model_validate(
        {**APPOINTMENT, "confirmation_key": "x" * 128}
    )
    assert accepted.confirmation_key == "x" * 128


def test_prompts_are_bounded():
    with pytest.raises(ValidationError):
        AgentChatRequest.model_validate({"message": "x" * (MAX_PROMPT_LENGTH + 1)})
    assert AgentChatRequest.model_validate({"message": "x" * MAX_PROMPT_LENGTH}).message


def test_valid_requests_still_validate():
    assert AppointmentRequest.model_validate(APPOINTMENT).confirmation_key == "claim-1"
    assert HandoffRequest.model_validate(HANDOFF).urgency == "normal"
    assert AgentChatRequest.model_validate({"message": "Bonjour"}).message == "Bonjour"
    assert DemoSessionRequest.model_validate({"customer_id": "NEO-88213"}).customer_id == "NEO-88213"