"""SQLite schema and idempotent fixture seed."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Iterator

from .config import get_database_url
from .clock import is_future_slot, now_utc
from .session import validate_session
from .utils import fixture_hash, load_fixture


def _db_path() -> str:
    """Return the database path from URL (support SQLite only for now)."""
    url = get_database_url()
    if url.startswith("sqlite:///"):
        return url.removeprefix("sqlite:///")
    raise RuntimeError("Only SQLite DATABASE_URL supported in foundation")


_init_lock = Lock()
_session_lock = Lock()


class BookingFailure(ValueError):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code


class _SessionConnection:
    def __init__(self, path: Path) -> None:
        # FastAPI requests may run on different worker threads. All access to
        # this connection is serialized by the per-session lock below.
        self.connection = sqlite3.connect(path, check_same_thread=False, timeout=5)
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.lock = Lock()


_session_connections: dict[tuple[Path, str], _SessionConnection] = {}


def connect(db_path: str | None = None) -> sqlite3.Connection:
    """Open a short-lived connection for initialization or maintenance."""
    conn = sqlite3.connect(db_path if db_path is not None else _db_path(), timeout=5)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _session_key(token: str) -> tuple[Path, str]:
    if validate_session(token) is None:
        raise ValueError("Invalid demo session")
    return (Path(_db_path()).resolve(), hashlib.sha256(token.encode("utf-8")).hexdigest())


@contextmanager
def session_connection(token: str) -> Iterator[sqlite3.Connection]:
    """Reuse exactly one connection per issued session and database file.

    Hold a per-session lock for the entire operation; commit on success and
    roll back on error. Never share a live connection between sessions.
    This is a foundation primitive, not a customer-read or booking endpoint.
    """
    key = _session_key(token)
    with _session_lock:
        entry = _session_connections.get(key)
        if entry is None:
            entry = _SessionConnection(key[0])
            _session_connections[key] = entry
        entry.lock.acquire()
    try:
        with entry.connection:
            yield entry.connection
    finally:
        entry.lock.release()


def release_session_connection(token: str) -> None:
    """Close a session's connection when its demo session ends."""
    key = _session_key(token)
    with _session_lock:
        entry = _session_connections.pop(key, None)
        if entry is not None:
            with entry.lock:
                entry.connection.close()


def close_session_connections() -> None:
    """Close all cached connections on application shutdown."""
    with _session_lock:
        for entry in _session_connections.values():
            with entry.lock:
                entry.connection.close()
        _session_connections.clear()


def _seed_customers(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    """Insert customers if table is empty; return count."""
    existing = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    if existing > 0:
        return existing
    for customer in data["customers"]:
        conn.execute(
            """
            INSERT INTO customers
            (customer_id, full_name, phone, address, postal_code, plan, monthly_price,
             contract_start_date, engagement_months, balance_due, open_incident_id,
             equipment, last_invoices)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                customer["customer_id"],
                customer["full_name"],
                customer["phone"],
                customer["address"],
                customer["postal_code"],
                customer["plan"],
                customer["monthly_price"],
                customer["contract_start_date"],
                customer["engagement_months"],
                customer["balance_due"],
                customer["open_incident_id"],
                json.dumps(customer["equipment"]),
                json.dumps(customer["last_invoices"]),
            ),
        )
    return len(data["customers"])


def _seed_incidents(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    """Insert network incidents if table is empty; return count."""
    existing = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
    if existing > 0:
        return existing
    for incident in data["network_incidents"]:
        conn.execute(
            """
            INSERT INTO incidents
            (incident_id, postal_codes, status, cause, affected_customers,
             started_at, estimated_resolution)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident["incident_id"],
                json.dumps(incident["postal_codes"]),
                incident["status"],
                incident["cause"],
                incident["affected_customers"],
                incident["started_at"],
                incident["estimated_resolution"],
            ),
        )
    return len(data["network_incidents"])


def _seed_slots(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    """Insert technician slots if table is empty; return count."""
    existing = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    if existing > 0:
        return existing
    for slot in data["technician_slots"]:
        conn.execute(
            """
            INSERT INTO slots
            (slot_id, postal_codes, "start", "end", available)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                slot["slot_id"],
                json.dumps(slot["postal_codes"]),
                slot["start"],
                slot["end"],
                slot["available"],
            ),
        )
    return len(data["technician_slots"])


def _seed_reasons(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    """Insert appointment reasons if table is empty; return count.

    The fixture reason strings are themselves the stable unique IDs, so
    later booking rows can reference them directly as foreign keys.
    """
    existing = conn.execute("SELECT COUNT(*) FROM reasons").fetchone()[0]
    if existing > 0:
        return existing
    for reason_id in data["appointment_reasons"]:
        conn.execute(
            """
            INSERT INTO reasons (reason_id, label, description)
            VALUES (?, ?, ?)
            """,
            (reason_id, reason_id, reason_id),
        )
    return len(data["appointment_reasons"])


def _seed_categories(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    """Insert escalation categories if table is empty; return count.

    As with reasons, the fixture category strings are the stable IDs.
    """
    existing = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    if existing > 0:
        return existing
    for category_id in data["escalation_categories"]:
        conn.execute(
            """
            INSERT INTO categories (category_id, label, description)
            VALUES (?, ?, ?)
            """,
            (category_id, category_id, category_id),
        )
    return len(data["escalation_categories"])


def init_db() -> None:
    """Create schema and seed fixture if empty; idempotent on restart."""
    with _init_lock:
        _initialize(Path(_db_path()).resolve())


def _initialize(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                full_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                address TEXT NOT NULL,
                postal_code TEXT NOT NULL,
                plan TEXT NOT NULL,
                monthly_price REAL NOT NULL,
                contract_start_date TEXT NOT NULL,
                engagement_months INTEGER NOT NULL,
                balance_due REAL NOT NULL,
                open_incident_id TEXT,
                equipment TEXT NOT NULL,
                last_invoices TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS incidents (
                incident_id TEXT PRIMARY KEY,
                postal_codes TEXT NOT NULL,
                status TEXT NOT NULL,
                cause TEXT NOT NULL,
                affected_customers INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                estimated_resolution TEXT
            );
            CREATE TABLE IF NOT EXISTS slots (
                slot_id TEXT PRIMARY KEY,
                postal_codes TEXT NOT NULL,
                "start" TEXT NOT NULL,
                "end" TEXT NOT NULL,
                available INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reasons (
                reason_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                description TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS categories (
                category_id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                description TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                reason_id TEXT NOT NULL,
                confirmation_key TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (slot_id) REFERENCES slots(slot_id),
                FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
                FOREIGN KEY (reason_id) REFERENCES reasons(reason_id)
            );
            CREATE TABLE IF NOT EXISTS handoffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category_id TEXT NOT NULL,
                customer_reference TEXT,
                summary TEXT NOT NULL,
                urgency TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (category_id) REFERENCES categories(category_id)
            );
            CREATE TABLE IF NOT EXISTS _meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        # Existing foundation databases need the same guarantee as fresh ones.
        # Never silently discard duplicate historical appointments.
        duplicate = conn.execute(
            "SELECT slot_id FROM appointments GROUP BY slot_id HAVING COUNT(*) > 1 LIMIT 1"
        ).fetchone()
        if duplicate:
            raise RuntimeError("Existing appointments contain duplicate slots; migration requires review")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS appointments_unique_slot ON appointments(slot_id)"
        )
        # Store seed hash for verification
        existing_hash = conn.execute(
            "SELECT value FROM _meta WHERE key = 'seed_hash'"
        ).fetchone()
        new_hash = fixture_hash()
        if existing_hash is None:
            conn.execute(
                "INSERT INTO _meta (key, value) VALUES ('seed_hash', ?)", (new_hash,)
            )
        data = load_fixture()
        _seed_customers(conn, data)
        _seed_incidents(conn, data)
        _seed_slots(conn, data)
        _seed_reasons(conn, data)
        _seed_categories(conn, data)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_customer_by_id(conn: sqlite3.Connection, customer_id: str) -> dict[str, Any] | None:
    """Return minimal customer fields for API, or None."""
    row = conn.execute(
        """
        SELECT customer_id, full_name, phone, address, postal_code, plan,
               monthly_price, balance_due, open_incident_id
        FROM customers WHERE customer_id = ?
        """,
        (customer_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "customer_id": row[0],
        "full_name": row[1],
        "phone": row[2],
        "address": row[3],
        "postal_code": row[4],
        "plan": row[5],
        "monthly_price": row[6],
        "balance_due": row[7],
        "open_incident_id": row[8],
    }


def customer_summary(conn: sqlite3.Connection, customer_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT customer_id, plan, monthly_price, balance_due, open_incident_id "
        "FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(zip(
        ("customer_id", "plan", "monthly_price", "balance_due", "open_incident_id"), row
    ))


def customer_incidents(conn: sqlite3.Connection, customer_id: str) -> list[dict[str, Any]]:
    customer = conn.execute(
        "SELECT postal_code, open_incident_id FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    if customer is None:
        return []
    postcode, linked_id = customer
    results = []
    for row in conn.execute(
        "SELECT incident_id, postal_codes, status, cause, started_at, estimated_resolution "
        "FROM incidents ORDER BY incident_id"
    ):
        incident_id, postal_codes, status, cause, started_at, estimated_resolution = row
        if incident_id != linked_id and postcode not in json.loads(postal_codes):
            continue
        results.append({
            "incident_id": incident_id, "status": status, "cause": cause,
            "started_at": started_at, "estimated_resolution": estimated_resolution,
            "scope": "linked" if incident_id == linked_id else "area_only",
        })
    return results


def customer_slots(conn: sqlite3.Connection, customer_id: str) -> list[dict[str, Any]]:
    row = conn.execute(
        "SELECT postal_code FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    if row is None:
        return []
    postcode = row[0]
    slots = []
    for slot_id, postal_codes, start, end in conn.execute(
        'SELECT s.slot_id, s.postal_codes, s."start", s."end" FROM slots s '
        'LEFT JOIN appointments a ON a.slot_id = s.slot_id '
        'WHERE s.available = 1 AND a.id IS NULL ORDER BY s."start", s.slot_id'
    ):
        if postcode in json.loads(postal_codes) and is_future_slot(datetime.fromisoformat(start)):
            slots.append({"slot_id": slot_id, "start": start, "end": end})
    return slots


def _saved_appointment(conn: sqlite3.Connection, key: str) -> tuple | None:
    return conn.execute(
        'SELECT a.id, a.customer_id, a.slot_id, a.reason_id, s."start", s."end" '
        'FROM appointments a JOIN slots s ON a.slot_id = s.slot_id '
        'WHERE a.confirmation_key = ?', (key,)
    ).fetchone()


def appointment_by_key(conn: sqlite3.Connection, customer_id: str, key: str) -> dict[str, Any] | None:
    row = _saved_appointment(conn, key)
    if row is None or row[1] != customer_id:
        return None
    return dict(zip(
        ("appointment_id", "customer_id", "slot_id", "reason_id", "start", "end"), row
    ))


def book_appointment(token: str, customer_id: str, slot_id: str, reason_id: str,
                     confirmation_key: str) -> dict[str, Any]:
    if validate_session(token) != customer_id:
        raise BookingFailure(403, "Session does not match customer")
    try:
        with session_connection(token) as conn:
            # Serialize writers across *different* demo sessions before replay checks.
            conn.execute("BEGIN IMMEDIATE")
            previous = _saved_appointment(conn, confirmation_key)
            if previous is not None:
                if (previous[1], previous[2], previous[3]) != (customer_id, slot_id, reason_id):
                    raise BookingFailure(409, "Idempotency key already used")
                return {**appointment_by_key(conn, customer_id, confirmation_key), "replayed": True}
            if conn.execute("SELECT 1 FROM reasons WHERE reason_id = ?", (reason_id,)).fetchone() is None:
                raise BookingFailure(422, "Unknown appointment reason")
            postcode = conn.execute(
                "SELECT postal_code FROM customers WHERE customer_id = ?", (customer_id,)
            ).fetchone()
            if postcode is None:
                raise BookingFailure(404, "Customer not found")
            slot = conn.execute(
                'SELECT postal_codes, "start", available FROM slots WHERE slot_id = ?', (slot_id,)
            ).fetchone()
            if slot is None:
                raise BookingFailure(404, "Slot not found")
            postal_codes, start, available = slot
            if postcode[0] not in json.loads(postal_codes):
                raise BookingFailure(422, "Slot does not cover customer postcode")
            if not is_future_slot(datetime.fromisoformat(start)):
                raise BookingFailure(409, "Slot is in the past or has no valid timezone")
            if not available or conn.execute(
                "SELECT 1 FROM appointments WHERE slot_id = ?", (slot_id,)
            ).fetchone():
                raise BookingFailure(409, "Slot is unavailable")
            conn.execute(
                "INSERT INTO appointments (slot_id, customer_id, reason_id, confirmation_key, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (slot_id, customer_id, reason_id, confirmation_key, now_utc().isoformat()),
            )
            conn.execute("UPDATE slots SET available = 0 WHERE slot_id = ?", (slot_id,))
            return {**appointment_by_key(conn, customer_id, confirmation_key), "replayed": False}
    except sqlite3.IntegrityError:
        raise BookingFailure(409, "Slot or idempotency key is unavailable") from None
    except sqlite3.OperationalError:
        raise BookingFailure(503, "Booking store is temporarily unavailable") from None


def save_handoff(token: str, customer_id: str, category_id: str,
                 summary: str, urgency: str) -> dict[str, Any]:
    if validate_session(token) != customer_id:
        raise ValueError("Invalid demo session")
    with session_connection(token) as conn:
        if conn.execute("SELECT 1 FROM categories WHERE category_id = ?", (category_id,)).fetchone() is None:
            raise ValueError("Unknown handoff category")
        cursor = conn.execute(
            "INSERT INTO handoffs (category_id, customer_reference, summary, urgency, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (category_id, customer_id, summary, urgency, now_utc().isoformat()),
        )
        return {"handoff_id": cursor.lastrowid, "category_id": category_id,
                "customer_reference": customer_id, "urgency": urgency}
