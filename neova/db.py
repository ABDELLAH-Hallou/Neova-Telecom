"""SQLite schema and idempotent fixture seed."""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .config import get_database_url


def _db_path() -> str:
    """Return the database path from URL (support SQLite only for now)."""
    url = get_database_url()
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///","")
    raise RuntimeError("Only SQLite DATABASE_URL supported in foundation")


def _seed_hash() -> str:
    """Return SHA-256 hex digest of the raw fixture JSON."""
    path = Path(__file__).parent.parent / "data" / "neova_data.json"
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


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
                json.dumps(incident["affected_customers"]),
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
            (slot_id, postal_codes, start, end, available)
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
    """Insert appointment reasons if table is empty; return count."""
    existing = conn.execute("SELECT COUNT(*) FROM reasons").fetchone()[0]
    if existing > 0:
        return existing
    for i, reason_id in enumerate(data["appointment_reasons"], start=1):
        conn.execute(
            """
            INSERT INTO reasons (reason_id, label, description)
            VALUES (?, ?, ?)
            """,
            (f"reason_{i}", reason_id, reason_id),
        )
    return len(data["appointment_reasons"])


def _seed_categories(conn: sqlite3.Connection, data: dict[str, Any]) -> int:
    """Insert escalation categories if table is empty; return count."""
    existing = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    if existing > 0:
        return existing
    for i, category_id in enumerate(data["escalation_categories"], start=1):
        conn.execute(
            """
            INSERT INTO categories (category_id, label, description)
            VALUES (?, ?, ?)
            """,
            (f"category_{i}", category_id, category_id),
        )
    return len(data["escalation_categories"])


def init_db() -> None:
    """Create schema and seed fixture if empty; idempotent on restart."""
    db_path = _db_path()
    Path(db_path).parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
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
                affected_customers TEXT NOT NULL,
                started_at TEXT NOT NULL,
                estimated_resolution TEXT
            );
            CREATE TABLE IF NOT EXISTS slots (
                slot_id TEXT PRIMARY KEY,
                postal_codes TEXT NOT NULL,
                start TEXT NOT NULL,
                end TEXT NOT NULL,
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
        # Store seed hash for verification
        existing_hash = conn.execute(
            "SELECT value FROM _meta WHERE key = 'seed_hash'"
        ).fetchone()
        new_hash = _seed_hash()
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


def load_fixture() -> dict[str, Any]:
    """Load the fixture JSON for tests; does not touch the DB."""
    path = Path(__file__).parent.parent / "data" / "neova_data.json"
    return json.loads(path.read_text())


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
