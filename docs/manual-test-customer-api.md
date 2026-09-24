# Manual test documentation — customer API (issue #3)

Run from the repository root with `uv` installed. Bash syntax (Linux/macOS/Git Bash).

## Terminal 1 — start the server (frozen demo clock)

```bash
export DATABASE_URL='sqlite:///./manual-neova.db'
export CLOCK_MODE='frozen'
export DEMO_TIMESTAMP='2026-08-26T12:00:00+02:00'

uv run --locked --extra dev python main.py
```

`manual-neova.db` is disposable; delete it after the run. Stop with Ctrl+C.

## Terminal 2 — exercise the routes

### 1. Health (no session needed)

```bash
curl -i http://127.0.0.1:8000/health
```

Expected: `200`, `"status": "ok"`, `"mode": "customer_agent"`, `"db_ready": true`, `"fixture_customers": 6`.

### 2. Create a demo session

```bash
curl -sS -X POST \
  http://127.0.0.1:8000/demo/sessions \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"NEO-88213"}'
```

Expected: `{"session_token":"<random>"}`. Copy it into the variable:

```bash
export TOKEN='paste-the-session_token-value-here'
```

### 3. Summary — only minimal account fields

```bash
curl -i -H "X-Demo-Session: $TOKEN" \
  http://127.0.0.1:8000/customers/NEO-88213/summary
```

Expected: `200` with only `customer_id`, `plan`, `monthly_price`, `balance_due`, `open_incident_id` (`"INC-4471"`). No phone, address, full name or invoices.

### 4. Incidents for the session customer

```bash
curl -i -H "X-Demo-Session: $TOKEN" http://127.0.0.1:8000/incidents
```

Expected: `200` with the single linked incident `INC-4471`, `"scope": "linked"`.

### 5. Eligible slots

```bash
curl -i -H "X-Demo-Session: $TOKEN" 'http://127.0.0.1:8000/slots?customer_id=NEO-88213'
```

Expected: `200` with `SLOT-7A31`, `SLOT-7A33`, `SLOT-7A34` (future under the frozen clock, postcode `75019`, unclaimed).

### 6. Book a slot (state change)

```bash
curl -i -X POST http://127.0.0.1:8000/appointments \
  -H "X-Demo-Session: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "customer_id":"NEO-88213",
    "slot_id":"SLOT-7A31",
    "reason_id":"no_internet",
    "confirmation_key":"manual-test-1"
  }'
```

Expected: `201` with a saved `appointment_id` and `"replayed": false`.

### 7. Look the booking up by key

```bash
curl -i -H "X-Demo-Session: $TOKEN" \
  http://127.0.0.1:8000/appointments/by-key/manual-test-1
```

Expected: `200` with the same appointment as step 3 (no `replayed` field).

### 8. Save a handoff

```bash
curl -i -X POST http://127.0.0.1:8000/handoffs \
  -H "X-Demo-Session: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "category_id":"technical",
    "summary":"Coupure signalée, diagnostic nécessaire",
    "urgency":"urgent"
  }'
```

Expected: `201` with `handoff_id: 1`, bound `customer_reference: NEO-88213`.

### 9. Rejections (negative cases)

No session token — expected `401`:

```bash
curl -i http://127.0.0.1:8000/incidents
```

Customer ID typed as a token — expected `401` (an ID is not a credential):

```bash
curl -i -H 'X-Demo-Session: NEO-88213' http://127.0.0.1:8000/incidents
```

Cross-customer read — expected `403`:

```bash
curl -i -H "X-Demo-Session: $TOKEN" \
  http://127.0.0.1:8000/customers/NEO-10467/summary
```

Slot already booked (same slot, new key) — expected `409`, never a success body:

```bash
curl -i -X POST http://127.0.0.1:8000/appointments \
  -H "X-Demo-Session: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "customer_id":"NEO-88213",
    "slot_id":"SLOT-7A31",
    "reason_id":"no_internet",
    "confirmation_key":"manual-test-2"
  }'
```

Blank identifier — expected `422` (DTO validation):

```bash
curl -i -X POST http://127.0.0.1:8000/appointments \
  -H "X-Demo-Session: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "customer_id":"   ",
    "slot_id":"SLOT-7A33",
    "reason_id":"no_internet",
    "confirmation_key":"manual-test-3"
  }'
```

### 10. Replay check (idempotency)

Re-send the exact step 3 request (same key, customer, slot, reason):

```bash
curl -i -X POST http://127.0.0.1:8000/appointments \
  -H "X-Demo-Session: $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "customer_id":"NEO-88213",
    "slot_id":"SLOT-7A31",
    "reason_id":"no_internet",
    "confirmation_key":"manual-test-1"
  }'
```

Expected: `201` with the **same** `appointment_id` as before and `"replayed": true`. The slot is now unavailable, but the replay still returns the saved booking.

### 11. Restart persistence (optional)

1. Stop terminal 1 with Ctrl+C.
2. Re-run terminal 1 exactly as above (`DATABASE_URL` unchanged).
3. Create a fresh session for `NEO-88213` (new `TOKEN`).
4. Re-run step 10's curl.

Expected: same `appointment_id`, `"replayed": true`. Booking and handoff survived the restart.

## Cleanup

```bash
rm -f manual-neova.db
```

The supplied `data/neova_data.json` and `corpus/` stay untouched; bookings/handoffs live only in `manual-neova.db`.