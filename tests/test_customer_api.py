"""Offline issue #3 route, transaction and privacy contracts."""

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from neova import db
from neova.app import app


FIXTURE = Path(__file__).resolve().parents[1] / "data" / "neova_data.json"
CUSTOMER = "NEO-88213"
OTHER = "NEO-10467"
SLOT = "SLOT-7A31"


@pytest.fixture
def local_api(monkeypatch, tmp_path):
    path = tmp_path / "neova.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("CLOCK_MODE", "frozen")
    monkeypatch.setenv("DEMO_TIMESTAMP", "2026-08-26T12:00:00+02:00")
    with TestClient(app) as client:
        first = client.post("/demo/sessions", json={"customer_id": CUSTOMER}).json()["session_token"]
        second = client.post("/demo/sessions", json={"customer_id": OTHER}).json()["session_token"]
        yield client, path, {"X-Demo-Session": first}, {"X-Demo-Session": second}


def booking(key="claim-1", slot=SLOT, reason="no_internet", customer=CUSTOMER):
    return {"customer_id": customer, "slot_id": slot,
            "reason_id": reason, "confirmation_key": key}


def test_reads_are_scoped_minimal_and_clock_filtered(local_api, monkeypatch):
    client, _, first, second = local_api
    routes = [
        ("get", f"/customers/{CUSTOMER}/summary", None),
        ("get", "/incidents", None),
        ("get", f"/slots?customer_id={CUSTOMER}", None),
        ("post", "/appointments", booking()),
        ("get", "/appointments/by-key/no-such-key", None),
        ("post", "/handoffs", {"category_id": "technical", "summary": "Diagnostic requis", "urgency": "normal"}),
    ]
    for method, route, body in routes:
        call = getattr(client, method)
        arguments = {"json": body} if body is not None else {}
        assert call(route, **arguments).status_code == 401
        assert call(route, headers={"X-Demo-Session": CUSTOMER}, **arguments).status_code == 401

    for route in (f"/customers/{CUSTOMER}/summary", f"/slots?customer_id={CUSTOMER}"):
        assert client.get(route, headers=second).status_code == 403
    assert client.post("/appointments", headers=second, json=booking()).status_code == 403
    assert client.post("/handoffs", headers=second, json={
        "category_id": "technical", "customer_reference": CUSTOMER,
        "summary": "Besoin de diagnostic", "urgency": "normal",
    }).status_code == 403

    summary = client.get(f"/customers/{CUSTOMER}/summary", headers=first).json()
    assert set(summary) == {"customer_id", "plan", "monthly_price", "balance_due", "open_incident_id"}
    assert summary["open_incident_id"] == "INC-4471"
    assert "NEO-" not in client.get("/incidents", headers=second).text
    linked = client.get("/incidents", headers=first).json()
    assert [incident["scope"] for incident in linked] == ["linked"]
    area = client.get("/incidents", headers=second).json()
    assert [incident["scope"] for incident in area] == ["area_only"]
    assert set(area[0]) == {"incident_id", "status", "cause", "started_at", "estimated_resolution", "scope"}
    slots = client.get(f"/slots?customer_id={CUSTOMER}", headers=first).json()
    assert [slot["slot_id"] for slot in slots] == ["SLOT-7A31", "SLOT-7A33", "SLOT-7A34"]
    assert all(set(slot) == {"slot_id", "start", "end"} for slot in slots)
    assert client.get(f"/slots?customer_id={CUSTOMER}", headers=second).status_code == 403
    monkeypatch.setenv("CLOCK_MODE", "live")
    assert client.get(f"/slots?customer_id={CUSTOMER}", headers=first).json() == []


def test_booking_replay_privacy_restart_and_fixture_integrity(local_api):
    client, path, first, second = local_api
    original_hash = hashlib.sha256(FIXTURE.read_bytes()).digest()
    assert client.get("/appointments/by-key/claim-1", headers=first).status_code == 404
    saved = client.post("/appointments", headers=first, json=booking())
    assert saved.status_code == 201
    assert saved.json()["appointment_id"] > 0
    assert saved.json()["replayed"] is False
    assert client.get("/appointments/by-key/claim-1", headers=first).json() == {
        k: v for k, v in saved.json().items() if k != "replayed"
    }
    assert client.get("/appointments/by-key/claim-1", headers=second).status_code == 404
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 1
        assert conn.execute("SELECT available FROM slots WHERE slot_id = ?", (SLOT,)).fetchone()[0] == 0
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO appointments (slot_id, customer_id, reason_id, confirmation_key, created_at) "
                "VALUES (?, ?, ?, ?, ?)", (SLOT, CUSTOMER, "installation", "duplicate", "2026-08-26T12:00:00+02:00")
            )
    db.init_db()
    replay = client.post("/appointments", headers=first, json=booking())
    assert replay.status_code == 201
    assert replay.json() == {**saved.json(), "replayed": True}
    assert client.post("/appointments", headers=first, json=booking(reason="installation")).status_code == 409
    assert client.post("/appointments", headers=second, json=booking(customer=OTHER)).status_code == 409
    assert client.post("/appointments", headers=first, json=booking(key="new-key")).status_code == 409
    assert client.get(f"/slots?customer_id={CUSTOMER}", headers=first).json()[0]["slot_id"] == "SLOT-7A33"
    assert hashlib.sha256(FIXTURE.read_bytes()).digest() == original_hash


def test_booking_validation_live_clock_and_atomic_rollback(local_api, monkeypatch):
    client, path, first, _ = local_api
    attempts = [
        (booking("unknown", reason="invented"), 422),
        (booking("wrong-area", slot="SLOT-6B10"), 422),
        (booking("already-unavailable", slot="SLOT-7A32"), 409),
        (booking("missing", slot="not-a-slot"), 404),
    ]
    for payload, status in attempts:
        response = client.post("/appointments", headers=first, json=payload)
        assert response.status_code == status
        assert "appointment_id" not in response.text
    monkeypatch.setenv("CLOCK_MODE", "live")
    assert client.post("/appointments", headers=first, json=booking("past")).status_code == 409
    monkeypatch.setenv("CLOCK_MODE", "frozen")
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TRIGGER deny_claim BEFORE UPDATE OF available ON slots '
                     "BEGIN SELECT RAISE(ABORT, 'test failure'); END")
    assert client.post("/appointments", headers=first, json=booking("rolled-back")).status_code == 409
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 0
        assert conn.execute("SELECT available FROM slots WHERE slot_id = ?", (SLOT,)).fetchone()[0] == 1


def test_simultaneous_sessions_cannot_claim_same_slot(local_api):
    client, path, first, _ = local_api
    other_token = client.post("/demo/sessions", json={"customer_id": CUSTOMER}).json()["session_token"]
    def claim(item):
        key, headers = item
        return client.post("/appointments", headers=headers, json=booking(key)).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        codes = list(executor.map(claim, [("race-1", first), ("race-2", {"X-Demo-Session": other_token})]))
    assert sorted(codes) == [201, 409]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM appointments WHERE slot_id = ?", (SLOT,)).fetchone()[0] == 1


def test_existing_database_unique_slot_migration_preserves_data(local_api):
    _, path, _, _ = local_api
    with sqlite3.connect(path) as conn:
        conn.execute("DROP INDEX appointments_unique_slot")
        for key in ("legacy-1", "legacy-2"):
            conn.execute(
                "INSERT INTO appointments (slot_id, customer_id, reason_id, confirmation_key, created_at) "
                "VALUES (?, ?, ?, ?, ?)", (SLOT, CUSTOMER, "installation", key, "2026-08-26T12:00:00+02:00")
            )
    with pytest.raises(RuntimeError, match="duplicate slots"):
        db.init_db()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 2
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM appointments WHERE confirmation_key = 'legacy-2'")
    db.init_db()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 1
        assert conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' "
                            "AND name = 'appointments_unique_slot'").fetchone()


def test_dto_limits_return_422_over_http(local_api):
    client, _, first, _ = local_api
    base = booking()
    for field in ("customer_id", "slot_id", "reason_id"):
        assert client.post("/appointments", headers=first, json={**base, field: "   "}).status_code == 422
        assert client.post("/appointments", headers=first, json={**base, field: "x" * 65}).status_code == 422
    for key in ("", " \t "):
        assert client.post("/appointments", headers=first, json={**base, "confirmation_key": key}).status_code == 422
    assert client.post("/appointments", headers=first, json={**base, "extra": "field"}).status_code == 422
    assert client.post("/handoffs", headers=first, json={
        "category_id": "  ", "summary": "Diagnostic nécessaire", "urgency": "normal",
    }).status_code == 422
    assert client.post("/handoffs", headers=first, json={
        **{"category_id": "technical", "summary": "Diagnostic nécessaire", "urgency": "normal"},
        "extra": "field",
    }).status_code == 422
    assert client.post("/demo/sessions", json={"customer_id": "   "}).status_code == 422
    assert client.post("/foundation/graph", json={"prompt": "x" * 5001}).status_code == 422
    assert client.post("/foundation/graph", json={"prompt": "x" * 5000}).status_code == 200
    assert client.post("/models/chat", json={"prompt": "x" * 5001, "provider": "openrouter"}).status_code == 422


def test_handoff_validation_and_restart(local_api):
    client, path, first, second = local_api
    payload = {"category_id": "technical", "summary": "  Coupure signalée, diagnostic nécessaire  ",
               "urgency": "urgent"}
    response = client.post("/handoffs", headers=first, json=payload)
    assert response.status_code == 201
    assert response.json() == {"handoff_id": 1, "category_id": "technical",
                               "customer_reference": CUSTOMER, "urgency": "urgent"}
    db.init_db()
    with sqlite3.connect(path) as conn:
        assert conn.execute(
            "SELECT category_id, customer_reference, summary, urgency FROM handoffs WHERE id = 1"
        ).fetchone() == ("technical", CUSTOMER, "Coupure signalée, diagnostic nécessaire", "urgent")
    for bad in (
        {**payload, "category_id": "not-a-category"},
        {**payload, "urgency": "critical"},
        {**payload, "summary": "   "},
        {**payload, "summary": "x" * 501},
    ):
        assert client.post("/handoffs", headers=first, json=bad).status_code == 422
    assert client.post("/handoffs", headers=second, json={**payload, "customer_reference": CUSTOMER}).status_code == 403
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 1


def test_new_lifespan_reads_durable_writes(monkeypatch, tmp_path):
    path = tmp_path / "restart.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("CLOCK_MODE", "frozen")
    monkeypatch.setenv("DEMO_TIMESTAMP", "2026-08-26T12:00:00+02:00")
    with TestClient(app) as client:
        token = client.post("/demo/sessions", json={"customer_id": CUSTOMER}).json()["session_token"]
        headers = {"X-Demo-Session": token}
        saved = client.post("/appointments", headers=headers, json=booking("restart-claim")).json()
        handoff = client.post("/handoffs", headers=headers, json={
            "category_id": "technical", "summary": "Diagnostic nécessaire", "urgency": "normal",
        }).json()
    with TestClient(app) as restarted:
        token = restarted.post("/demo/sessions", json={"customer_id": CUSTOMER}).json()["session_token"]
        headers = {"X-Demo-Session": token}
        assert restarted.get("/appointments/by-key/restart-claim", headers=headers).json() == {
            key: value for key, value in saved.items() if key != "replayed"
        }
        assert restarted.post("/appointments", headers=headers, json=booking("restart-claim")).json() == {
            **saved, "replayed": True,
        }
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT id FROM handoffs").fetchone()[0] == handoff["handoff_id"]
