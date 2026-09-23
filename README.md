# Néova Foundation — Issue #2

Foundation for the Néova customer-relations agent.

This version provides the local API, SQLite persistence, demo sessions, a configurable clock and a minimal LangGraph. It does not provide customer answers yet.

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

- Seeds SQLite from the supplied JSON.
- Does not modify the source fixture.
- Preserves appointments and handoffs after restart.
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

The booking implementation must later use this rule:

```text
slot.start > current time
```

Timestamps without timezone information are rejected for booking validation.

## Tests

Run the offline foundation tests:

```bash
uv run --locked --extra dev python -m pytest tests/test_foundation.py -v
```

Current result:

```text
12 passed
1 upstream Starlette/AnyIO deprecation warning
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

The tests use temporary databases and make no network or OpenRouter requests.

## Current limitations

This foundation does not yet implement:

- Customer-data endpoints
- Technician appointment booking
- Knowledge-base retrieval
- Human handoff
- Model-backed customer agent (the chat endpoint is a direct model call)
- Conversation evaluation

The demo session is not production authentication. A real system would use an external identity provider and create a trusted principal after verifying the customer.

Detailed verification evidence is available in `docs/issue-2-foundation-evidence.md`.
