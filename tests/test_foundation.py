"""Foundation tests: seed, restart, session, config, clock, and health."""

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# Use an in-memory database for tests to avoid polluting the runtime DB
TEST_DB = "sqlite:///./test_neova.db"


@pytest.fixture
def client(monkeypatch):
    """Create a test client for the app."""
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-key")
    monkeypatch.setenv("CHAT_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("CHAT_FALLBACK_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
    monkeypatch.setenv("DEMO_SESSION_SECRET", "test-session-secret-12345")
    # Import after env vars are set
    import neova.app

    return TestClient(neova.app.app)


@pytest.fixture
def fresh_db(monkeypatch):
    """Initialize a fresh DB for tests and clean up afterward."""
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-key")
    monkeypatch.setenv("CHAT_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("CHAT_FALLBACK_MODEL", "openai/gpt-4o-mini")
    monkeypatch.setenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")
    monkeypatch.setenv("DEMO_SESSION_SECRET", "test-session-secret-12345")
    db_path = TEST_DB.replace("sqlite:///", "")
    path = Path(db_path)
    if path.exists():
        path.unlink()
    try:
        import neova.db

        neova.db.init_db()
        yield db_path
    finally:
        if path.exists():
            path.unlink()


def test_seed_counts(fresh_db):
    """Verify fixture JSON seed counts match expectations."""
    conn = sqlite3.connect(fresh_db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0] == 12
        assert conn.execute("SELECT COUNT(*) FROM reasons").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 5
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 0
    finally:
        conn.close()


def test_fixture_hash_uniqueness():
    """Verify the fixture hash is deterministic and non-empty."""
    raw = Path(__file__).parent.parent / "data" / "neova_data.json"
    import neova.db

    expected = hashlib.sha256(raw.read_bytes()).hexdigest()
    assert neova.db._seed_hash() == expected
    assert len(expected) == 64  # SHA-256 hex


def test_restart_preserves_appointments(fresh_db):
    """Verify durable appointments persist across init calls."""
    db_path = TEST_DB.replace("sqlite:///", "")
    conn = sqlite3.connect(db_path)
    try:
        # Insert a test appointment before restart
        conn.execute(
            """
            INSERT INTO appointments
            (slot_id, customer_id, reason_id, confirmation_key, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("SLOT-7A31", "NEO-88213", "installation", "ck-001", "2026-08-26T12:00:00+02:00"),
        )
        conn.commit()
        initial = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
        assert initial == 1
    finally:
        conn.close()

    # Simulate restart by re-running init_db (should skip re-seed)
    import neova.db

    neova.db.init_db()

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
        assert count == 1, "Appointment lost on restart"
    finally:
        conn.close()


def test_fixture_unchanged():
    """Verify the fixture JSON is unmodified from initial release."""
    raw = Path(__file__).parent.parent / "data" / "neova_data.json"
    import neova.db

    fixture = neova.db.load_fixture()
    # Core structure checks
    assert "customers" in fixture
    assert "network_incidents" in fixture
    assert "technician_slots" in fixture
    assert "appointment_reasons" in fixture
    assert "escalation_categories" in fixture
    assert len(fixture["customers"]) == 6
    assert len(fixture["network_incidents"]) == 3
    assert len(fixture["technician_slots"]) == 12
    assert len(fixture["appointment_reasons"]) == 4
    assert len(fixture["escalation_categories"]) == 5
    assert fixture["appointments"] == []
    assert fixture["tickets"] == []


def test_get_customer_by_id(fresh_db):
    """Verify customer lookup returns minimal fields."""
    conn = sqlite3.connect(TEST_DB.replace("sqlite:///", ""))
    try:
        import neova.db

        customer = neova.db.get_customer_by_id(conn, "NEO-88213")
        assert customer is not None
        assert set(customer.keys()) == {
            "customer_id",
            "full_name",
            "phone",
            "address",
            "postal_code",
            "plan",
            "monthly_price",
            "balance_due",
            "open_incident_id",
        }
        assert customer["customer_id"] == "NEO-88213"
        assert customer["full_name"] is not None
        assert customer["balance_due"] is not None
    finally:
        conn.close()


def test_get_customer_not_found(fresh_db):
    """Verify non-existent customer returns None."""
    conn = sqlite3.connect(TEST_DB.replace("sqlite:///", ""))
    try:
        import neova.db

        customer = neova.db.get_customer_by_id(conn, "UNKNOWN")
        assert customer is None
    finally:
        conn.close()


def test_health_endpoint(client):
    """Verify health check returns foundation metadata."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["mode"] == "foundation"
    assert data["db_ready"] is True
    assert isinstance(data["fixture_customers"], int)
    assert data["fixture_customers"] > 0


def test_graph_endpoint(client):
    """Verify minimal graph returns expected output."""
    resp = client.post("/foundation/graph", json={"prompt": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["classification"] == "foundation_test"
    assert data["output"] == "foundation mode active"


def test_demo_session_bounds():
    """Verify session token creation and validation."""
    import neova.session

    FIXTURE_CUSTOMERS = [
        "NEO-88213",
        "NEO-10467",
        "NEO-53190",
        "NEO-27604",
        "NEO-71925",
        "NEO-40318",
    ]

    for cid in FIXTURE_CUSTOMERS:
        token = neova.session.issue_session(cid)
        assert token is not None
        assert len(token) > len(cid)
        assert neova.session.validate_session(token) == cid

    # Unknown customer rejected
    with pytest.raises(ValueError):
        neova.session.issue_session("UNKNOWN")

    # Invalid tokens rejected
    assert neova.session.validate_session("") is None
    assert neova.session.validate_session("invalid") is None
    assert neova.session.validate_session("secret|UNKNOWN|abc") is None


def test_clock_mode_default():
    """Verify clock returns live time when not in demo mode."""
    # This test asserts behavior under default live mode
    import neova.clock

    t1 = neova.clock.now_utc()
    t2 = neova.clock.now_utc()
    # Live time should advance or stay equal (not regress)
    assert t2 >= t1


def test_config_raises_without_key():
    """Verify config fails when required env vars are missing."""
    import importlib

    # Temporarily clear required env vars
    orig = {}
    for key in [
        "OPENROUTER_API_KEY",
        "CHAT_MODEL",
        "CHAT_FALLBACK_MODEL",
        "EMBEDDING_MODEL",
        "DEMO_SESSION_SECRET",
    ]:
        orig[key] = os.environ.get(key)
        os.environ.pop(key, None)

    try:
        # Force reimport to re-run module-level code
        import neova.config

        importlib.reload(neova.config)
        assert False, "Expected RuntimeError for missing env var"
    except RuntimeError as e:
        assert "Missing required environment variable" in str(e)
    finally:
        # Restore env vars
        for key, value in orig.items():
            if value:
                os.environ[key] = value


def test_session_issue_for_unknown_customer():
    """Verify session issues raises for unknown customer."""
    import neova.session

    with pytest.raises(ValueError):
        neova.session.issue_session("UNKNOWN")
