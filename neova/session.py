"""Process-local demo sessions, not identity verification.

Tokens are random bearer values; only their hashes are stored server-side.
They expire when the process exits. No customer details are exposed by the
foundation routes, and a typed customer ID is never a credential.
"""

import hashlib
import secrets
from functools import lru_cache

from .utils import load_fixture


@lru_cache(maxsize=1)
def fixture_customers() -> tuple[str, ...]:
    return tuple(c["customer_id"] for c in load_fixture()["customers"])


class SessionStore:
    def __init__(self) -> None:
        self._bindings: dict[str, str] = {}

    def issue(self, customer_id: str) -> str:
        if customer_id not in fixture_customers():
            raise ValueError("Unknown fixture customer")
        token = secrets.token_urlsafe(32)
        self._bindings[hashlib.sha256(token.encode("ascii")).hexdigest()] = customer_id
        return token

    def validate(self, token: str) -> str | None:
        if not isinstance(token, str):
            return None
        return self._bindings.get(hashlib.sha256(token.encode("utf-8")).hexdigest())

    def is_for_customer(self, token: str, customer_id: str) -> bool:
        return self.validate(token) == customer_id


_sessions = SessionStore()


def issue_session(customer_id: str) -> str:
    return _sessions.issue(customer_id)


def validate_session(token: str) -> str | None:
    return _sessions.validate(token)


def is_session_for_customer(token: str, customer_id: str) -> bool:
    return _sessions.is_for_customer(token, customer_id)
