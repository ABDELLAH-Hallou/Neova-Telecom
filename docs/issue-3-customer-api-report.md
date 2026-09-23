# Issue #3 / PR 2 — implementation report

**Branch:** `3-pr-2-customer-api-scoped-reads-atomic-bookings-and-handoffs`  
**Scope:** approved steps 1–6 in `docs/issue-3-customer-api-plan.md`. No files were staged, committed or pushed.

## Files changed

- `neova/app.py` — typed request/response contracts and six session-scoped FastAPI routes.
- `neova/customer_api.py` (new) — token binding, customer matching and customer-safe read projections.
- `neova/db.py` — narrow SQLite reads, existing-DB unique-slot index check, transactional booking and durable handoff writes.
- `tests/test_customer_api.py` (new) — offline route, privacy, restart, replay, atomicity, concurrency and migration tests.
- `.github/workflows/ci.yml` — run customer API, foundation and model tests.
- `README.md`, `main.py` — API usage, frozen clock and launcher wording.
- `docs/issue-3-customer-api-report.md` (this file) — observed results and limitations.

The existing untracked `docs/issue-3-customer-api-plan.md` was not modified. Neither the supplied JSON/corpus nor configuration secrets/lockfile were edited.

## Behavior added

- All six new routes require an issued `X-Demo-Session` token. Customer IDs in paths, queries or bodies must match that token's fixture customer; a typed ID is not a credential. Customer summaries omit phone, address, full name and invoice history. `GET /incidents` distinguishes `linked` from `area_only` postcode status; `GET /slots` lists only future, available, unclaimed slots covering that customer's postcode.
- `POST /appointments` validates reason, coverage, slot availability and clock; `BEGIN IMMEDIATE` serializes writers across sessions. A unique SQLite index prevents a slot from being booked twice, while insertion and marking the slot unavailable commit or roll back together. Replaying the same key/customer/slot/reason returns the saved ID even after the slot becomes unavailable; a mismatched reuse fails. Lookup by key returns only the bound customer's record. Existing databases with duplicate slot rows fail initialization with a review message rather than deleting records.
- `POST /handoffs` validates category, `normal`/`urgent` urgency and a nonblank summary of at most 500 characters. It stores the session-bound customer reference and returns a durable ID, without implying an advisor queue has accepted the case.
- README documents the distinction between an API-persisted booking and explicit conversational user confirmation, which the later graph must enforce.

## Commands run and actual results

From the repository root in PowerShell:

| Command | Observed result |
| --- | --- |
| `uv sync --locked --extra dev` | Exit 0; 57 packages resolved, 55 audited; no lock mismatch. |
| `uv run --locked --extra dev python -m compileall -q main.py neova tests` | Exit 0; no output. Run initially and again after test additions. |
| `uv run --locked --extra dev python -m pytest tests/test_customer_api.py tests/test_foundation.py tests/test_models.py -q` | Initial run: **20 passed**, 1 upstream Starlette/AnyIO deprecation warning. After adding a new-lifespan restart case and direct uniqueness assertion: **21 passed**, 1 same warning, in 7.23 s. |
| `git diff --check` | Exit 0; Git emitted Windows LF→CRLF conversion notices, no patch errors. |
| `git diff --exit-code HEAD -- data/neova_data.json corpus/` | Exit 0; supplied fixture/corpus unchanged. |
| `git status --short` | At last pre-report check: modified CI, README, main.py, app.py and db.py; untracked plan, customer_api.py and test_customer_api.py. This report adds one more untracked file. Nothing staged. |

Single-process smoke: launched `main.py` via `uv run --locked --extra dev python -` running a bounded Python subprocess, with `DATABASE_URL` pointing to a disposable database under `C:\Users\Abdellah\AppData\Local\Temp\opencode` and environment-only `CLOCK_MODE=frozen`, `DEMO_TIMESTAMP=2026-08-26T12:00:00+02:00`. Called the documented routes over `127.0.0.1:8000`; observed **health 200, demo-session creation 200, summary/incidents/slots each 200, and 3 eligible slots**. The subprocess was terminated and its temporary DB removed. This exercised the same `python main.py` launcher without creating or altering `.env`; state-changing routes were verified by isolated automated tests.

Read-only inspection of the existing local `neova.db` found string-valued reason/category IDs and zero appointments; it was not changed by these tests or the smoke run.

## Assumptions and decisions

- User confirmed all six routes require `X-Demo-Session`; incident reads may include area-only events explicitly labeled as unverified customer impact; handoff urgency is `normal` or `urgent` with a 500-character summary cap; the later graph, not this API, collects explicit conversational confirmation.
- The request fingerprint for idempotency is **customer ID + slot ID + reason ID**. A repeated key with a different fingerprint returns HTTP 409. A successful API booking is not proof the user consented in conversation.
- The local demo session is process-local fixture isolation, not real authentication: anyone able to call the local `/demo/sessions` route can select a fixture ID. The server remains loopback-only.
- Existing databases with duplicate appointment slots require manual review before initialization; this implementation deliberately does not discard historical rows. Databases created by much older scaffolds with numeric reason/category identifiers were not migrated.
- Caller-supplied handoff summaries should be factual and minimal; the API bounds length and strips surrounding whitespace but cannot verify the truth of free text. No staffed queue is implemented.

## Remaining work

- Later graph work must obtain an explicit user confirmation of the **exact slot and reason** before calling `POST /appointments`, wrap the API as an agent tool, and handle API 500/timeouts and ambiguous write outcomes safely.
- Retrieval, model resilience/fallback, evaluation and the final two-page README belong to later issues. The pre-existing `/models/chat` direct-OpenAI option conflicts with the take-home's single supplied OpenRouter key and remains a separate final-delivery concern.
- A production service needs genuine identity verification; demo-session selection is insufficient for real customer access. Legacy numeric-ID SQLite databases need an explicit migration if they must be supported.
