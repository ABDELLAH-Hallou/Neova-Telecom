# Néova foundation (Issue #2)

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). From the repository root:

```sh
uv run --locked --env-file .env -- python main.py
```

The server binds **127.0.0.1:8000** in one process. Visit
`http://127.0.0.1:8000/health` to confirm startup; stop with Ctrl+C.
First startup seeds local `neova.db` from the read-only supplied JSON.
Repeated startups preserve existing SQLite records. No OpenRouter key is
needed for the foundation. `.env.example` lists settings for later work;
the app does not load `.env` automatically. Configure settings as process
environment variables when needed; never commit credentials.

The bounded graph entry `POST /foundation/graph` returns only a foundation
status, **not** a customer answer. `POST /demo/sessions` with JSON
`{"customer_id":"NEO-88213"}` issues a random process-local fixture token;
selection of a customer ID is **not identity verification**. Tokens expire
when the server exits, and no private customer-data endpoints are exposed.
This foundation does **not** implement retrieval, booking, handoff, model
calls, or a conversational agent. Those belong to later issues.

Clock mode defaults to `live`; all supplied slots dated August–September
2026 are past as of September 23, 2026. To test the date policy against
future fixture slots, explicitly set `CLOCK_MODE=frozen` and
`DEMO_TIMESTAMP=2026-08-26T12:00:00+02:00` (Europe/Paris). This alone does
not provide a booking endpoint.

Offline tests (temporary databases, no key/network calls):

```sh
uv run --locked --extra dev python -m pytest tests/test_foundation.py -v
```

Design: `main.py` runs FastAPI and the bounded LangGraph in the same
process. `neova/db.py` reuses one SQLite connection per issued demo session,
serializes access to it across request threads, closes it on shutdown, and
seeds once without overwriting later writes;
`neova/clock.py` enforces aware `slot.start > now`; `neova/session.py`
uses random server-side token bindings for demo isolation only. See
`docs/issue-2-foundation-evidence.md` for verified checks and limitations.
