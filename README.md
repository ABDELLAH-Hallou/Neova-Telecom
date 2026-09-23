# Néova Local API — Issues #2–#3

Local foundation and fixture-backed customer API for the Néova customer-relations agent.

This version provides SQLite persistence, demo sessions, a configurable clock, scoped reads, atomic booking and handoff storage, and a minimal LangGraph. It does not provide customer conversations yet.

## Run the application

Requirements:

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

Create your local configuration:

```bash
cp .env.example .env
```

The foundation requires `DATABASE_URL`, which is already defined in `.env.example`:

```dotenv
DATABASE_URL=sqlite:///./neova.db
```

The optional `/models/chat` endpoint requires `OPENROUTER_API_KEY` and `CHAT_MODEL` for OpenRouter, or `OPENAI_API_KEY` and `OPENAI_CHAT_MODEL` for OpenAI. The foundation endpoints run without either key.

Start the server:

```bash
uv run --locked --env-file .env -- python main.py
```

The API runs locally at:

```text
http://127.0.0.1:8000
```

Swagger documentation is available at:

```text
http://127.0.0.1:8000/docs
```

Stop the server with `Ctrl+C`.

## Current endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Check API and database startup |
| `POST` | `/demo/sessions` | Create a local session for a fixture customer |
| `POST` | `/foundation/graph` | Exercise the placeholder LangGraph |
| `POST` | `/models/chat` | Call either configured chat provider |
| `GET` | `/customers/{id}/summary` | Session customer's minimal account summary |
| `GET` | `/incidents` | Linked and area-only incidents (scope clearly labeled) |
| `GET` | `/slots?customer_id=...` | Future, available slots covering the session customer's postcode |
| `POST` | `/appointments` | Atomically save a booking with an idempotency key |
| `GET` | `/appointments/by-key/{key}` | Look up only the session customer's saved booking |
| `POST` | `/handoffs` | Save a minimal human-handoff record and return its ID |

To call a model, POST `{"provider":"openrouter","prompt":"Bonjour"}` or `{"provider":"openai","prompt":"Bonjour"}` to `/models/chat`.

Test the health endpoint:

```bash
curl -i http://127.0.0.1:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "mode": "foundation",
  "db_ready": true,
  "fixture_customers": 6
}
```

Create a demo session:

```bash
curl -i -X POST \
  http://127.0.0.1:8000/demo/sessions \
  -H 'Content-Type: application/json' \
  -d '{"customer_id":"NEO-88213"}'
```

The server returns a random process-local token:

```json
{
  "session_token": "random-token"
}
```

Selecting a fixture customer is not real authentication. The token only keeps local demo sessions separate. Tokens expire when the application stops.

All six customer API routes require `X-Demo-Session: <session_token>`. A typed customer ID alone cannot read or write account data. For example, after creating a session, use its token in these requests:

```bash
curl -H "X-Demo-Session: $TOKEN" http://127.0.0.1:8000/customers/NEO-88213/summary
curl -H "X-Demo-Session: $TOKEN" http://127.0.0.1:8000/incidents
curl -H "X-Demo-Session: $TOKEN" 'http://127.0.0.1:8000/slots?customer_id=NEO-88213'
curl -X POST -H "X-Demo-Session: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"customer_id":"NEO-88213","slot_id":"SLOT-7A31","reason_id":"no_internet","confirmation_key":"demo-claim-1"}' \
  http://127.0.0.1:8000/appointments
curl -H "X-Demo-Session: $TOKEN" http://127.0.0.1:8000/appointments/by-key/demo-claim-1
curl -X POST -H "X-Demo-Session: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"category_id":"technical","summary":"Diagnostic nécessaire","urgency":"normal"}' \
  http://127.0.0.1:8000/handoffs
```

Booking the supplied dates requires the **explicit frozen demo clock** below. The API returns a saved appointment ID on success; replaying the same key for the same customer/slot/reason returns that ID. An invalid, unavailable, past, cross-customer or conflicting request never confirms a booking. Explicit **user** confirmation before making this API call belongs to the later conversation graph. Handoff IDs represent stored local records, not an accepted advisor queue. `GET /incidents` labels postcode-only coverage `area_only`, never as proof a customer is affected.

Test the placeholder graph:

```bash
curl -i -X POST \
  http://127.0.0.1:8000/foundation/graph \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"Bonjour, je souhaite comprendre ma facture"}'
```

Expected response:

```json
{
  "classification": "foundation_only",
  "output": "Foundation mode: the customer agent is not implemented yet."
}
```

## Architecture

```text
main.py
   │
   ▼
FastAPI
   ├── health endpoint
   ├── demo-session endpoint
   └── minimal LangGraph
           │
           ▼
        SQLite
```

The application runs in one local process.

`neova/db.py`:

- Seeds SQLite from the supplied JSON and persists bookings and handoffs.
- Does not modify the source fixture.
- Preserves appointments and handoffs after restart; one slot cannot be booked twice.
- Reuses one locked SQLite connection for each active demo session.
- Closes cached connections during application shutdown.
- Uses temporary connections for initialization and maintenance.

`neova/session.py` maps random tokens to fixture customers. Customer IDs are not accepted as session tokens.

`neova/graph.py` contains a bounded placeholder graph. It makes no model calls and does not return customer information.

## Clock

The clock defaults to real time:

```dotenv
CLOCK_MODE=live
DEMO_TIMESTAMP=
```

The supplied technician slots are dated August–September 2026. To demonstrate them as future slots, use:

```dotenv
CLOCK_MODE=frozen
DEMO_TIMESTAMP=2026-08-26T12:00:00+02:00
```

The booking implementation uses this rule:

```text
slot.start > current time
```

Timestamps without timezone information are rejected for booking validation.

## Tests

Run the offline tests:

```bash
uv run --locked --extra dev python -m pytest tests/test_customer_api.py tests/test_foundation.py tests/test_models.py -q
```

The tests cover:

- Idempotent database initialization
- Persistence after restart
- Foreign-key enforcement
- Concurrent initialization
- Demo-session separation
- SQLite connection reuse and cleanup
- Live and frozen clock behavior
- FastAPI lifespan
- Health and graph endpoints
- Secret-safe configuration errors
- Scoped customer reads, transactional booking, replay/concurrent claims and durable handoffs

The tests use temporary databases and make no network or OpenRouter requests.

## Current limitations

This foundation does not yet implement:

- Knowledge-base retrieval
- Conversational confirmation and human queue integration
- Model-backed customer agent (the chat endpoint is a direct model call)
- Conversation evaluation

The demo session is not production authentication. A real system would use an external identity provider and create a trusted principal after verifying the customer.

Detailed verification evidence is available in `docs/issue-2-foundation-evidence.md`.
