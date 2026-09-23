"""Session-bound, customer-safe projections for the local fixture API."""

from fastapi import HTTPException

from . import db
from .session import validate_session


def bound_customer(token: str | None) -> str:
    customer_id = validate_session(token)
    if customer_id is None:
        raise HTTPException(status_code=401, detail="Valid demo session required")
    return customer_id


def require_customer(bound_id: str, requested_id: str) -> None:
    if requested_id != bound_id:
        raise HTTPException(status_code=403, detail="Session does not match customer")


def summary(token: str, customer_id: str) -> dict:
    with db.session_connection(token) as conn:
        result = db.customer_summary(conn, customer_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return result


def incidents(token: str, customer_id: str) -> list[dict]:
    with db.session_connection(token) as conn:
        return db.customer_incidents(conn, customer_id)


def slots(token: str, customer_id: str) -> list[dict]:
    with db.session_connection(token) as conn:
        return db.customer_slots(conn, customer_id)


def appointment(token: str, customer_id: str, key: str) -> dict:
    with db.session_connection(token) as conn:
        result = db.appointment_by_key(conn, customer_id, key)
    if result is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return result
