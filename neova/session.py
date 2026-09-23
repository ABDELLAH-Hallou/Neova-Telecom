"""Demo session management: opaque tokens bound to fixture customers."""

import secrets
from typing import Optional

from .config import DEMO_SESSION_SECRET


# Fixture customers available for demo isolation
FIXTURE_CUSTOMERS = [
    "NEO-88213",
    "NEO-10467",
    "NEO-53190",
    "NEO-27604",
    "NEO-71925",
    "NEO-40318",
]


def issue_session(customer_id: str) -> str:
    """Issue an opaque session token bound to a fixture customer."""
    if customer_id not in FIXTURE_CUSTOMERS:
        raise ValueError(f"Unknown fixture customer: {customer_id}")
    payload = f"{DEMO_SESSION_SECRET}|{customer_id}|{secrets.token_hex(16)}"
    return payload


def validate_session(token: str) -> Optional[str]:
    """Validate session token and return bound customer_id, or None."""
    if not token.startswith(f"{DEMO_SESSION_SECRET}|"):
        return None
    parts = token.split("|")
    if len(parts) != 3:
        return None
    _, customer_id, _ = parts
    if customer_id not in FIXTURE_CUSTOMERS:
        return None
    return customer_id


def is_session_for_customer(token: str, customer_id: str) -> bool:
    """Check if token is bound to the given customer_id."""
    bound = validate_session(token)
    return bound == customer_id
