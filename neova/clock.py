"""Clock abstraction: live or frozen demo time."""

import os
from datetime import datetime, timezone

from zoneinfo import ZoneInfo

from .config import CLOCK_MODE, DEMO_TIMESTAMP


def _parse_demo_timestamp() -> datetime:
    """Parse the configured DEMO_TIMESTAMP to an aware UTC datetime."""
    raw = DEMO_TIMESTAMP
    if not raw:
        raise RuntimeError("DEMO_TIMESTAMP not set in demo mode")
    # Accept ISO 8601 with or without timezone (treat as Europe/Paris)
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid DEMO_TIMESTAMP format: {raw}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("Europe/Paris"))
    return dt.astimezone(timezone.utc)


def now_utc() -> datetime:
    """Current UTC time: live from system or frozen demo time."""
    if CLOCK_MODE == "demo":
        return _parse_demo_timestamp()
    return datetime.now(timezone.utc)


def is_future_slot(slot_start: datetime) -> bool:
    """Return True if slot_start is strictly after now_utc."""
    # Ensure slot_start is aware UTC
    if slot_start.tzinfo is None:
        slot_start = slot_start.replace(tzinfo=ZoneInfo("Europe/Paris"))
    slot_utc = slot_start.astimezone(timezone.utc)
    return slot_utc > now_utc()


def format_slot_time(slot_start: datetime) -> str:
    """Format a slot start in Europe/Paris for display."""
    if slot_start.tzinfo is None:
        slot_start = slot_start.replace(tzinfo=ZoneInfo("Europe/Paris"))
    paris = slot_start.astimezone(ZoneInfo("Europe/Paris"))
    return paris.strftime("%Y-%m-%d %H:%M")
