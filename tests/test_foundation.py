"""Offline Issue #2 foundation contract tests."""

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from neova import clock, config, db, retrieval, session
from neova.app import app
from neova.utils import load_fixture

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
            (reason,) for reason in sorted(load_fixture()["appointment_reasons"])
        ]
        assert conn.execute("SELECT category_id FROM categories ORDER BY category_id").fetchall() == [
            (category,) for category in sorted(load_fixture()["escalation_categories"])
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


def test_one_command_startup_indexes_public_fts_without_paid_calls(database, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    with TestClient(app) as client:
        assert client.get("/health").json()["db_ready"] is True
        with db.connect() as conn:
            outcome = retrieval.search(conn, "frais de rejet", mode="fts")
            count = db.chunk_count(conn)
            assert count > 0
            assert outcome.results and "index_empty" not in outcome.degraded
            assert all(p.source_id not in {"politique-geste-commercial", "procedure-escalade-n2"}
                       for p in outcome.results)
    # Even with live credentials present, a restart must not invoke an
    # embedder or spend credit while preparing the local search index.
    monkeypatch.setenv("OPENROUTER_API_KEY", "placeholder")
    monkeypatch.setenv("EMBEDDING_MODEL", "placeholder")
    from neova import embeddings
    monkeypatch.setattr(embeddings, "openrouter_embedder", lambda: pytest.fail(
        "startup attempted a paid embedding call"))
    with TestClient(app):
        with db.connect() as conn:
            assert db.chunk_count(conn) == count  # restart does not reindex or erase data
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database.parent / 'with-key.db'}")
    with TestClient(app):
        with db.connect() as conn:
            assert db.chunk_count(conn) == count  # fresh start with a key is also free


def test_session_singleton_connections_are_isolated(database, monkeypatch, tmp_path):
    db.init_db()
    first_token = session.issue_session(session.fixture_customers()[0])
    second_token = session.issue_session(session.fixture_customers()[1])
    another_first_customer_token = session.issue_session(session.fixture_customers()[0])
    first = db.connect()
    second = db.connect()
    try:
        assert first is not second
        assert first.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert second.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        first.close()
        second.close()

    try:
        with db.session_connection(first_token) as first:
            assert first.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with db.session_connection(first_token) as again:
            assert again is first
        with db.session_connection(second_token) as second:
            assert second is not first
            assert second.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with db.session_connection(another_first_customer_token) as another:
            assert another is not first  # separate sessions, even for the same customer

        with pytest.raises(RuntimeError, match="rollback test"):
            with db.session_connection(first_token) as conn:
                conn.execute("UPDATE customers SET full_name = 'Rolled back' WHERE customer_id = 'NEO-88213'")
                raise RuntimeError("rollback test")
        with db.session_connection(first_token) as conn:
            assert conn is first
            assert conn.execute("SELECT full_name FROM customers WHERE customer_id = 'NEO-88213'").fetchone()[0] != "Rolled back"

        for invalid in (session.fixture_customers()[0], "a.b", ""):
            with pytest.raises(ValueError, match="Invalid demo session"):
                with db.session_connection(invalid):
                    pass

        other_path = tmp_path / "other.db"
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{other_path}")
        db.init_db()
        with db.session_connection(first_token) as other:
            assert other is not first
        assert other_path.exists() and database.exists()
    finally:
        db.close_session_connections()
    with pytest.raises(sqlite3.ProgrammingError):
        first.execute("SELECT 1")


def test_concurrent_initialization_is_idempotent(database):
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: db.init_db(), range(8)))
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM reasons").fetchone()[0] == 4


def test_session_connection_serializes_threads_and_releases(database):
    db.init_db()
    token = session.issue_session(session.fixture_customers()[0])

    def query(_):
        with db.session_connection(token) as conn:
            return conn, conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]

    try:
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(query, range(8)))
        assert all(conn is results[0][0] and count == 6 for conn, count in results)
        db.release_session_connection(token)
        with pytest.raises(sqlite3.ProgrammingError):
            results[0][0].execute("SELECT 1")
        with db.session_connection(token) as replacement:
            assert replacement is not results[0][0]
    finally:
        db.close_session_connections()


def test_fixture_starts_with_empty_mutations():
    fixture = load_fixture()
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
    slot = datetime.fromisoformat(load_fixture()["technician_slots"][0]["start"])
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


def test_health_and_demo_selection(monkeypatch, database):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DEMO_SESSION_SECRET", raising=False)
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {
            "status": "ok", "mode": "customer_agent", "db_ready": True, "fixture_customers": 6,
        }
        first, second = session.fixture_customers()[:2]
        issued = client.post("/demo/sessions", json={"customer_id": first})
        assert issued.status_code == 200
        token = issued.json()["session_token"]
        assert session.validate_session(token) == first
        assert not session.is_session_for_customer(token, second)
        assert session.validate_session(first) is None
        assert first not in token
        assert client.post("/demo/sessions", json={"customer_id": "UNKNOWN"}).status_code == 404
        assert first not in health.text
        assert "OPENROUTER_API_KEY" not in health.text
        with db.session_connection(token) as session_conn:
            assert session_conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 6
    assert app.state.db_ready is False
    with pytest.raises(sqlite3.ProgrammingError):
        session_conn.execute("SELECT 1")
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
