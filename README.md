# Néova Telecom Customer Agent API

Local fixture-backed customer API and bounded French conversation agent (LangGraph) for the Néova customer-relations agent.

This version provides SQLite persistence, demo sessions, a configurable clock, scoped reads, atomic booking and handoff storage, hybrid retrieval over the public corpus, and a bounded conversation graph (`POST /agent/chat`) with named routes, deterministic booking confirmation and human handoffs.

## Run the application

Requirements:

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

Create your local configuration:

```bash
cp .env.example .env
```

The API requires `DATABASE_URL`, which is already defined in `.env.example`:

```dotenv
DATABASE_URL=sqlite:///./neova.db
```

The conversation graph requires `OPENROUTER_API_KEY`, `CHAT_MODEL` and `CHAT_FALLBACK_MODEL` (verified fallback route) for the model calls; `EMBEDDING_MODEL` for corpus indexing. The customer API routes and the booking flow run without any key.

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
| `POST` | `/agent/chat` | One bounded conversation turn (French agent; optional `X-Demo-Session`) |
| `GET` | `/customers/{id}/summary` | Session customer's minimal account summary |
| `GET` | `/incidents` | Linked and area-only incidents (scope clearly labeled) |
| `GET` | `/slots?customer_id=...` | Future, available slots covering the session customer's postcode |
| `POST` | `/appointments` | Atomically save a booking with an idempotency key |
| `GET` | `/appointments/by-key/{key}` | Look up only the session customer's saved booking |
| `POST` | `/handoffs` | Save a minimal human-handoff record and return its ID |

Model calls belong to the conversation graph only (`/agent/chat`): there is no direct model endpoint.

Test the health endpoint:

```bash
curl -i http://127.0.0.1:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "mode": "customer_agent",
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

Booking the supplied dates requires the **explicit frozen demo clock** below. The API returns a saved appointment ID on success; replaying the same key for the same customer/slot/reason returns that ID. An invalid, unavailable, past, cross-customer or conflicting request never confirms a booking. In the conversation, an explicit **user** "oui" to the exact slot and reason is required before this API call is ever made. Handoff IDs represent stored local records, not an accepted advisor queue. `GET /incidents` labels postcode-only coverage `area_only`, never as proof a customer is affected.

## Talk to the agent

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/demo/sessions \
  -H 'Content-Type: application/json' -d '{"customer_id":"NEO-88213"}' | cut -d'"' -f4)

curl -X POST http://127.0.0.1:8000/agent/chat \
  -H "X-Demo-Session: $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message":"Je veux prendre rendez-vous avec un technicien"}'
```

The reply includes the French answer, the chosen route, the tools called, the pending-booking confirmation state, citations and handoff reference when a handoff was stored. Without `X-Demo-Session`, the agent answers from the public corpus and offers a generic human route without any private data.

## Conversation agent internals

- **Routing order (per turn):** (1) deterministic prompt-injection check on the raw message — before any model call; (2) with a pending booking, exact known continuations (`oui`, `non`, `ok`, `confirmer`, `annuler`) and exact code phrases route straight to the booking flow without a classifier call — continuations only re-ask, they never confirm; (3) otherwise one cheap OpenRouter classification call (`CLASSIFIER_MODEL`, temperature 0, max_tokens 150, reasoning disabled, strict `json_schema` with `additionalProperties: false` and `provider.require_parameters: true`); (4) `out_of_scope` and `ambiguous` verdicts are protected — a pending booking never overrides them.
- **Confirmation:** the agent proposes the exact Europe/Paris slot and reason with a one-time code: reply exactly `CONFIRMER RDV <code>` to book or `ANNULER RDV <code>` to cancel. Codes are random per proposal, expire after 10 minutes (wall-clock independent of the frozen demo clock), and a stale code can never book. Bare "oui" books nothing.
- **Prompts** live in `neova/prompt/` (`classifier.md`, `answer.md`) — versioned Markdown, no prompt text in code.
- **Degradation:** missing `CLASSIFIER_MODEL`, a network error or a schema violation falls back to the deterministic keyword router and is reported as `classification.degraded: true` in the trace; `classification.source` is `model`, `keywords` or `skipped`.

## Architecture

```text
main.py
   │
   ▼
FastAPI
   ├── health endpoint
   ├── demo-session endpoint
   └── /agent/chat → bounded LangGraph (neova/graph.py + neova/nodes/)
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

`neova/graph.py` defines the bounded conversation graph state, its conditional wiring and the `run_conversation` entry point; each node lives in its own module under `neova/nodes/`. Every turn is a finite DAG with a hard step bound — no free-running agent loop.

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
uv run --locked --extra dev python -m pytest tests -q
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
- Health endpoint
- Secret-safe configuration errors
- Scoped customer reads, transactional booking, replay/concurrent claims and durable handoffs
- Hybrid retrieval, evidence gate and citations over the public corpus
- Bounded conversation routes, multi-turn booking confirmation, handoffs and privacy behavior
- Bounded provider retries/fallback, tool timeout, booking by-key recovery and the redacted usage ledger (offline, fault-injected)

The tests use temporary databases and make no network or OpenRouter requests.

## Current limitations

Not yet implemented:

- Human queue integration beyond stored handoff records
- Fixed evaluation set with measured results
- Model-backed live tracing (Langfuse)
- Live verification of the fallback route's upstream provider (`python -m neova.provider --verify`, publication-time; needs key budget)

The demo session is not production authentication. A real system would use an external identity provider and create a trusted principal after verifying the customer.

Detailed verification evidence is available in `docs/issue-2-foundation-evidence.md`.
