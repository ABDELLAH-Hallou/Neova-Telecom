"""Offline Issue #2 foundation contract tests."""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from neova import clock, config, db, session
from neova.app import app
from neova.graph import NOT_CUSTOMER_FACING

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "neova_data.json"


@pytest.fixture
def database(monkeypatch, tmp_path):
    path = tmp_path / "state" / "neova.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    return path


def test_seed_and_restart(database):
    before = hashlib.sha256(FIXTURE.read_bytes()).digest()
    db.init_db()
    with db.connect() as conn:
        for table, count in {
            "customers": 6, "incidents": 3, "slots": 12,
            "reasons": 4, "categories": 5,
            "appointments": 0, "handoffs": 0,
        }.items():
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count
        assert conn.execute("SELECT reason_id FROM reasons ORDER BY reason_id").fetchall() == [
            (reason,) for reason in sorted(db.load_fixture()["appointment_reasons"])
        ]
        assert conn.execute("SELECT category_id FROM categories ORDER BY category_id").fetchall() == [
            (category,) for category in sorted(db.load_fixture()["escalation_categories"])
        ]
        assert type(conn.execute("SELECT affected_customers FROM incidents LIMIT 1").fetchone()[0]) is int
        conn.execute("UPDATE customers SET full_name = ? WHERE customer_id = ?", ("Local edit", "NEO-88213"))
        conn.execute("UPDATE slots SET available = 0 WHERE slot_id = ?", ("SLOT-7A31",))
        conn.execute(
            "INSERT INTO appointments (slot_id, customer_id, reason_id, confirmation_key, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("SLOT-7A31", "NEO-88213", "installation", "test-key", "2026-08-26T12:00:00+02:00"),
        )
        conn.execute(
            "INSERT INTO handoffs (category_id, summary, urgency, created_at) VALUES (?, ?, ?, ?)",
            ("technical", "Test only", "normal", "2026-08-26T12:00:00+02:00"),
        )
    db.init_db()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 1
        assert conn.execute("SELECT full_name FROM customers WHERE customer_id = 'NEO-88213'").fetchone()[0] == "Local edit"
        assert conn.execute("SELECT available FROM slots WHERE slot_id = 'SLOT-7A31'").fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO appointments (slot_id, customer_id, reason_id, confirmation_key, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("NOT-A-SLOT", "NEO-88213", "installation", "bad-fk", "2026-08-26T12:00:00+02:00"),
            )
    assert hashlib.sha256(FIXTURE.read_bytes()).digest() == before


def test_fixture_starts_with_empty_mutations():
    fixture = db.load_fixture()
    assert fixture["appointments"] == fixture["tickets"] == []


def test_live_clock_rejects_past_and_naive(monkeypatch):
    monkeypatch.setenv("CLOCK_MODE", "live")
    assert clock.now_utc().tzinfo is not None
    assert not clock.is_future_slot(datetime(2026, 8, 27, 9, tzinfo=timezone(timedelta(hours=2))))
    assert not clock.is_future_slot(datetime(2099, 8, 27, 9))
    assert clock.is_future_slot(datetime.now(timezone.utc) + timedelta(days=1))


def test_frozen_clock_requires_explicit_time(monkeypatch):
    monkeypatch.setenv("CLOCK_MODE", "frozen")
    monkeypatch.delenv("DEMO_TIMESTAMP", raising=False)
    with pytest.raises(config.ConfigurationError, match="DEMO_TIMESTAMP"):
        clock.now_utc()
    monkeypatch.setenv("DEMO_TIMESTAMP", "2026-08-26T12:00:00+02:00")
    assert clock.now_utc() == datetime(2026, 8, 26, 10, tzinfo=timezone.utc)
    slot = datetime.fromisoformat(db.load_fixture()["technician_slots"][0]["start"])
    assert clock.is_future_slot(slot)
    assert not clock.is_future_slot(clock.now_utc())
    assert not clock.is_future_slot(datetime(2026, 8, 27, 9))


def test_invalid_clock_configuration_is_sanitized(monkeypatch):
    monkeypatch.setenv("CLOCK_MODE", "wrong-mode")
    with pytest.raises(config.ConfigurationError, match="CLOCK_MODE"):
        clock.now_utc()
    monkeypatch.setenv("CLOCK_MODE", "frozen")
    secret = "private-invalid-timestamp"
    monkeypatch.setenv("DEMO_TIMESTAMP", secret)
    with pytest.raises(config.ConfigurationError) as error:
        clock.now_utc()
    assert secret not in str(error.value)


def test_missing_key_does_not_prevent_foundation(monkeypatch, database):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("CHAT_MODEL", raising=False)
    db.init_db()
    with pytest.raises(config.ConfigurationError) as error:
        config.require_openrouter_api_key()
    assert "OPENROUTER_API_KEY" in str(error.value)
    assert "sk-or" not in str(error.value)


def test_session_boundary():
    store = session.SessionStore()
    first, second = session.fixture_customers()[:2]
    token = store.issue(first)
    assert store.validate(token) == first
    assert store.is_for_customer(token, first)
    assert not store.is_for_customer(token, second)
    assert first not in token and second not in token
    assert store.issue(first) != token
    for bad in (first, second, "", "a.b", "not base64!", token + "!", None):
        assert store.validate(bad) is None
    with pytest.raises(ValueError):
        store.issue("UNKNOWN")
    assert session.SessionStore().validate(token) is None  # process-local store


def test_health_graph_and_demo_selection(monkeypatch, database):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DEMO_SESSION_SECRET", raising=False)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok", "mode": "foundation", "db_ready": True, "fixture_customers": 6,
        }
        graph = client.post("/foundation/graph", json={"prompt": "Bonjour, ma facture ?"})
        assert graph.status_code == 200
        assert graph.json() == {"classification": "foundation_only", "output": NOT_CUSTOMER_FACING}
        first, second = session.fixture_customers()[:2]
        issued = client.post("/foundation/sessions", json={"customer_id": first})
        assert issued.status_code == 200
        token = issued.json()["session_token"]
        assert session.validate_session(token) == first
        assert not session.is_session_for_customer(token, second)
        assert session.validate_session(first) is None
        assert first not in token
        assert client.post("/foundation/sessions", json={"customer_id": "UNKNOWN"}).status_code == 404
        assert first not in graph.text and first not in health.text
        assert "OPENROUTER_API_KEY" not in graph.text + health.text
    assert app.state.db_ready is False
    assert database.exists()


def test_launcher_is_loopback_and_does_not_print_secrets(monkeypatch, capsys):
    import main

    calls = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "private-test-value")
    monkeypatch.setattr(main.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    main.main()
    assert calls == [(("neova.app:app",), {"host": "127.0.0.1", "port": 8000, "reload": False})]
    output = capsys.readouterr()
    assert "private-test-value" not in output.out + output.err
    assert "NEO-" not in output.out + output.err
