# Issue #3 / PR 2 — customer API plan (approval required)

**Status:** plan only; implementation and verification await approval. Target branch: `3-pr-2-customer-api-scoped-reads-atomic-bookings-and-handoffs`. GitHub issue [#3 — PR 2: Customer API](https://github.com/ABDELLAH-Hallou/Neova-Telecom/issues/3) matches this branch; issue #2 is the completed PR 1 foundation. This plan uses `take_home_relation_client.md`, `docs/system_design.md`, and R4–R6/R8 in `docs/requirements_analysis.md` as context. No test outcome is claimed here.

## 1. Current relevant behavior

- `main.py` starts one loopback FastAPI process. `neova/app.py` offers `/health`, `/demo/sessions`, `/foundation/graph`, and `/models/chat`; there are **no** customer, incident, slot, appointment, lookup-by-key, or handoff routes yet. `neova/graph.py` remains a non-customer-facing placeholder.
- `POST /demo/sessions` accepts a fixture customer ID and returns an opaque, process-local token. `neova/session.py` binds a token to one customer; a typed ID alone does not validate as a token. Anyone with local API access can select a fixture customer, so this is demo isolation, **not identity verification**. `neova/db.py` caches one locked SQLite connection per token/database and commits on success or rolls back on exception; different sessions have different connections.
- Startup seeds `data/neova_data.json` without modifying it: 6 customers, 3 incidents, 12 slots, 4 reason strings and 5 category strings. Durable `appointments` and `handoffs` tables exist, and restart preserves rows. `appointments.confirmation_key` is unique, but `appointments.slot_id` is **not** unique: the schema currently permits two bookings of one slot. `slots.available` is seeded but never claimed by an API. `get_customer_by_id()` currently returns phone, address and full name as well as needed fields; it must not be returned wholesale by a summary route.
- The fixture has explicit `open_incident_id` links for only two customers; postcode overlap alone does not imply a linked incident. Available slots cover several postcodes but all start 27 August–1 September 2026; they are past under the current live clock (23 September 2026). `neova/clock.py` already offers `is_future_slot()` and an explicitly configured frozen mode, e.g. `2026-08-26T12:00:00+02:00`.
- `tests/test_foundation.py` exercises seeding, restart, session isolation, clock and lifespan. CI currently runs only foundation tests. The README still states booking and handoff are absent; `main.py` prints “no customer answers or booking.”

## 2. Files that should change after approval

| Path | Minimum intended change |
| --- | --- |
| `neova/app.py` | Add the six issue-specified routes with typed request/response contracts and one common session-token dependency (for example an `X-Demo-Session` header). Enforce customer binding before lookup and return bounded errors without private record leakage. |
| `neova/db.py` | Add narrow, parameterized read queries and transactional booking/handoff persistence. Enforce one appointment per slot with a unique index/constraint compatible with existing databases; check how to handle a pre-existing duplicate before applying the index. Acquire the write lock with `BEGIN IMMEDIATE`, check and claim slot and insert appointment together, and support safe key replay. |
| `neova/customer_api.py` (new) | Keep session-bound customer/incident/slot projections and booking/handoff validation separate from HTTP routing and SQLite setup if this makes `app.py` and `db.py` smaller; use only customer-safe fields. |
| `tests/test_customer_api.py` (new) | Offline TestClient contract/privacy tests, success/failure/replay/restart cases, and concurrent claims using isolated temporary SQLite paths; assert the fixture hash stays unchanged. |
| `.github/workflows/ci.yml` | Include the new API tests in the bounded offline CI suite. |
| `README.md`, `main.py` | Update endpoint/demo-clock examples and the launcher’s now-stale “no booking” message; distinguish an API-confirmed saved appointment from graph/customer conversation confirmation. Keep README concise. |

No dependency or lockfile change appears necessary for this scoped work.

## 3. Files that should not change

- `data/neova_data.json` and all `corpus/*`: fixture is read-only; no retrieval or document edits in this issue.
- `neova/graph.py`, `neova/models.py`, `neova/config.py`, `neova/clock.py`, `neova/session.py`, and `tests/test_models.py`: use their existing interfaces. Graph orchestration, model/provider policy, and identity-provider work belong elsewhere. If an existing interface proves insufficient, record the reason before widening scope.
- `take_home_relation_client.md`, `docs/system_design.md`, `docs/requirements_analysis.md`, `docs/material_inventory.md`, and issue #2's plan/evidence: retain original brief and prior evidence. Do not commit `.env`, `neova.db`, generated files, secrets or customer records.

## 4. Smallest implementation sequence (after approval)

1. Define narrow response shapes and a single session-header dependency: require an issued token for all six routes, match path/query/body customer IDs to the bound customer, and project only needed summary, incident and slot fields. Distinguish an explicitly linked incident from a postcode-area incident; do not imply customer impact from area coverage.
2. Implement scoped read endpoints (`/customers/{id}/summary`, `/incidents`, `/slots?customer_id=...`) through parameterized SQLite reads; test missing, forged and cross-customer tokens plus payload minimization and slot-time filtering against the configured clock.
3. Extend appointment storage with a unique slot guarantee (including a safe path for already-created SQLite databases). Add one transaction that first resolves idempotency-key replay for the **same bound customer and exact request**, then checks enumerated reason, coverage, availability/unclaimed status and future start, atomically claims the slot and stores an appointment ID. A reused key with a different request must fail; two distinct claims of one slot cannot both succeed.
4. Expose `POST /appointments` and scoped `GET /appointments/by-key/{key}`. Return success only for an actually saved appointment ID; use stable 4xx outcomes for invalid input, unavailable/past slot, key mismatch and conflicts, without suggesting a booking was confirmed on error. The later graph must collect explicit user confirmation before calling POST.
5. Implement `POST /handoffs` with enumerated category, bounded factual summary, urgency and only a session-verified customer reference; persist and return a durable handoff ID, without implying an advisor accepted it. Test stored fields and restart durability.
6. Add offline route, replay, concurrency, privacy, live/frozen-clock and unchanged-fixture tests; add them to CI and update the brief local API instructions and launcher text. Record observed evidence only after checks run.

## 5. Risks and unresolved assumptions

- **Demo access is not authentication:** `/demo/sessions` permits selection of any fixture ID on the loopback service. Requiring its bearer token prevents typed-ID-only and cross-session requests but cannot establish real-world identity. Decide whether all six endpoints should require a session (recommended); an anonymous handoff can remain a generic message outside this PR's API.
- **Confirmation boundary:** issue #3 asks for safe API booking, while the design expects an explicit user confirmation before mutation. This PR can validate a structured booking request but cannot prove the user said “yes”; the graph in issue #5 must enforce that before invoking the endpoint. Do not advertise an HTTP POST as proof of conversational consent.
- **Existing databases and uniqueness:** the foundation schema has no unique slot constraint. Add a migration/index that preserves existing valid bookings and fails transparently if an already-used DB has duplicate slot rows; do not silently delete or overwrite them. Existing local DBs may also have old numeric reason/category IDs, as issue #2 evidence notes.
- **Replay semantics and races:** decide and document the request fingerprint fields (customer, slot, reason); replay the same key only to the bound customer and exact request, even if its slot is now unavailable or past. SQLite uses one writer at a time; set a bounded busy timeout/handle lock conflicts without returning false success. Preserve failure atomicity if any step fails.
- **Read semantics:** choose the minimum summary needed by the future graph (likely plan, monthly price, balance due, open incident ID; no phone, address or invoice history). An area-wide incident may be returned as area status only; customer linkage requires `open_incident_id`. Slot listing should reflect postcode, clock, `available`, and saved bookings while still revalidating at POST.
- **Handoff data:** fixture category labels are stable IDs, but allowed urgency values and summary length are not prescribed. Define a small explicit set/limit and prevent free-text summaries from becoming an accidental repository of unverified personal details. There is no staffed queue.
- **Brief-level provider constraint:** the existing `/models/chat` accepts direct OpenAI credentials, whereas the brief requires only the supplied OpenRouter key. This is pre-existing, outside issue #3's API scope; resolve before final submission rather than treating it as compliant evidence.

## 6. Exact verification commands (after implementation)

From the repository root in PowerShell, with `uv` installed; tests must use temporary databases and make no model calls. These commands are **planned, not yet run**:

```powershell
uv sync --locked --extra dev
uv run --locked --extra dev python -m compileall -q main.py neova tests
uv run --locked --extra dev python -m pytest tests/test_customer_api.py tests/test_foundation.py tests/test_models.py -q
git diff --check
git diff --exit-code HEAD -- data/neova_data.json corpus/
git status --short
```

Manual frozen-clock route smoke (after setting a local `.env` to `CLOCK_MODE=frozen`, `DEMO_TIMESTAMP=2026-08-26T12:00:00+02:00`, and a disposable `DATABASE_URL`; start in terminal 1, stop with Ctrl+C):

```powershell
uv run --locked --env-file .env -- python main.py
```

In terminal 2, use the documented `X-Demo-Session` contract and the issued token; these calls are read-only, with state-changing cases covered by isolated automated tests:

```powershell
$base = 'http://127.0.0.1:8000'
$token = (Invoke-RestMethod -Method Post -Uri "$base/demo/sessions" -ContentType 'application/json' -Body '{"customer_id":"NEO-88213"}').session_token
$headers = @{ 'X-Demo-Session' = $token }
Invoke-RestMethod -Uri "$base/customers/NEO-88213/summary" -Headers $headers
Invoke-RestMethod -Uri "$base/incidents" -Headers $headers
Invoke-RestMethod -Uri "$base/slots?customer_id=NEO-88213" -Headers $headers
```

Automated acceptance evidence should cover forbidden cross-session reads and writes, absence of phone/address/invoice details, linked-versus-area incident wording, before/after booking rows, same-key replay and mismatch, unavailable/wrong-postcode/invalid-reason/past slot, concurrent slot collision, restart persistence, minimal saved handoff, and unchanged JSON SHA-256. No result is claimed until those checks run.
