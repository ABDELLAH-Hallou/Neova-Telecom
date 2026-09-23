"""Clock abstraction: live time or an opt-in frozen demo time.

The fixture slots are dated August–September 2026, so the demo uses an
explicitly configured frozen ``Europe/Paris`` timestamp to exercise them.
The default is ``live``: real aware time, under which past slots are
refused. Only the ``slot.start > now`` policy lives here; booking
rejection over HTTP belongs to a later issue.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .config import ConfigurationError, get_clock_mode, get_demo_timestamp

PARIS = ZoneInfo("Europe/Paris")


def _parse_demo_timestamp() -> datetime:
    """Return the configured frozen demo time as an aware UTC datetime."""
    raw = get_demo_timestamp()
    if not raw:
        raise ConfigurationError(
            "CLOCK_MODE=frozen requires an explicit DEMO_TIMESTAMP "
            "(ISO-8601, e.g. 2026-08-26T12:00:00+02:00)."
        )
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        raise ConfigurationError(
            "DEMO_TIMESTAMP is not a valid ISO-8601 timestamp."
        ) from None
    if moment.tzinfo is None:
        # The fixture declares Europe/Paris; treat a naive frozen
        # timestamp in that zone rather than guessing UTC.
        moment = moment.replace(tzinfo=PARIS)
    return moment.astimezone(timezone.utc)


def now_utc() -> datetime:
    """Current aware UTC time: live system clock or frozen demo time."""
    if get_clock_mode() == "frozen":
        return _parse_demo_timestamp()
    return datetime.now(timezone.utc)


def is_future_slot(slot_start: datetime) -> bool:
    """Return True only if an aware slot start is strictly after now.

    A naive ``slot_start`` has no trustworthy time zone, so it is never
    considered bookable. The fixture's ``available`` flag is ignored
    here on purpose: recency comes from the clock, not the flag.
    """
    if slot_start.tzinfo is None:
        return False
    return slot_start.astimezone(timezone.utc) > now_utc()


def format_slot_time(slot_start: datetime) -> str:
    """Format an aware slot start in Europe/Paris for customer display."""
    if slot_start.tzinfo is None:
        slot_start = slot_start.replace(tzinfo=PARIS)
    return slot_start.astimezone(PARIS).strftime("%Y-%m-%d %H:%M")
