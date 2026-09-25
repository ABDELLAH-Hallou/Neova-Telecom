# Issue #5 / PR 4 — conversation graph plan (approval required)

**Status:** plan only; no implementation has started. Target branch: `5-pr-4-conversation-graph-bounded-routes-confirmation-and-handoff` (current). GitHub issue [#5 — PR 4: Conversation graph](https://github.com/ABDELLAH-Hallou/Neova-Telecom/issues/5) matches this branch. Context: `take_home_relation_client.md`, `docs/system_design.md` (§ graph, booking, handoff, failure table), R1/R4/R6 in `docs/requirements_analysis.md`, `docs/issue-3-customer-api-report.md` (confirmation belongs to the graph), `docs/issue-4-retrieval-report.md` (search has no HTTP surface; graph wires it here). No test outcome is claimed here.

## 1. Current relevant behavior

- **The graph is still the foundation placeholder.** `neova/graph.py` is `Input → classify → END`, makes no model calls, touches no data, and returns `NOT_CUSTOMER_FACING` ("the customer agent is not implemented yet"). `POST /foundation/graph` in `neova/app.py` invokes it and its contract (`{"classification": "foundation_only", "output": …}`) is asserted by `tests/test_foundation.py`.
- **No conversation layer exists.** There is no chat/message endpoint bound to a demo session, no pending-booking state, no checkpointer, no multi-turn memory anywhere. `POST /models/chat` is a stateless direct model call, explicitly not part of the graph. `docs/issue-3-customer-api-report.md` records that conversational confirmation must be enforced by the graph, not the API.
- **What the graph can already build on:**
  - `neova/retrieval.search(conn, query, embed_fn, mode="hybrid") -> SearchOutcome` with `results: list[CitedPassage]`, `degraded` flags and `evidence_gate()` codes (`archived_pricing`, `fee_timing_conflict`, `invoice_line_items_absent`, `non_contractual_source`). No HTTP surface — consumed via module functions; the graph is where it gets wired.
  - `neova/models.chat_model(provider) -> ChatOpenAI` (OpenRouter or OpenAI; `CHAT_MODEL` env). `CHAT_FALLBACK_MODEL` is declared in `.env.example` but read nowhere — provider fallback is issue #6.
  - FastAPI surface (`neova/app.py`): scoped reads (`/customers/{id}/summary`, `/incidents`, `/slots`), `POST /appointments` (atomic, idempotent on `confirmation_key`, fingerprint = customer+slot+reason, `BookingFailure` → HTTP status), `POST /handoffs` → durable `handoff_id`, session binding via `X-Demo-Session` → `customer_api.bound_customer` (401) + `require_customer` (403).
  - Session (`neova/session.py`, process-local token→customer map) and demo clock (`neova/clock.py`, `format_slot_time()` Europe/Paris) are ready; `db.save_handoff` returns a reference only when the row is stored.
- **Dependencies are already in place:** `langgraph` and `langchain-openai` are pinned in `pyproject.toml` — no new dependency and no lockfile churn needed for this issue.
- **Test patterns to reuse:** `tests/test_retrieval.py` injects a deterministic `FakeEmbedder`; booking helpers in `tests/test_customer_api.py` use frozen clock `2026-08-26T12:00:00+02:00`, customers `NEO-88213`/`NEO-10467`, slots `SLOT-7A31…`, reasons `no_internet`/`installation`, and fixture `escalation_categories` (5 incl. `technical`). No French trace/conversation tests exist yet.

## 2. Files that should change

| Path | Minimum intended change |
| --- | --- |
| `neova/tools.py` (new) | Bounded custom LangGraph tools wrapping the **FastAPI** routes over HTTP (`API_BASE_URL`, propagating `X-Demo-Session`): read tools (summary, incidents, slots), the state-changing booking tool (`POST /appointments`), and the handoff tool (`POST /handoffs`); plus a thin wrapper over `neova.retrieval.search` for public passages. Each tool validates inputs and returns typed results/failures; no open-ended tool loop — the graph decides which tool runs at which named node. |
| `neova/graph.py` (replace placeholder) | The real bounded graph: `classify → (public_search / api_read / both) → evidence_check → (french_answer / ask_one_missing_detail / propose_booking / handoff)` with conditional edges and a bounded step count. `GraphState` gains the multi-turn fields: `session_token`, `customer_id`, `pending_booking` (customer reference, `slot_id`, human-readable slot, enumerated `reason_id`), `confirmation_pending: bool`, `messages/history`, `route`, `citations`, `gate_flags`, `handoff`. |
| `neova/conversation.py` (new) | Pending-booking state store (process-local, keyed by demo session token — same lifetime contract as `SessionStore`) and the deterministic confirmation gate: a booking is armed only by an **actual user utterance** matching an explicit French affirmative to the exact slot+reason pair; any change of slot or reason resets `confirmation_pending`. The gate inspects user turns, never model output. Also holds the sensitive-topic routing table (privacy rights, fraud, legal threat, death, Pro contracts, protected/minor customers, distress → immediate handoff) as **code-side** keywords, so internal routing rules are never pasted into customer context. |
| `neova/app.py` | Add a session-bound conversation endpoint (e.g. `POST /agent/chat`) taking a message + history, binding `X-Demo-Session`, running the compiled graph, and returning the French reply plus trace metadata (route chosen, tools called, confirmation state). Keep `POST /foundation/graph` and its foundation contract intact. |
| `neova/dto.py` | New request/response models (`extra="forbid"`, `str_strip_whitespace=True` style): conversation message list, reply, route/tool trace, confirmation state; bounded prompt lengths consistent with existing `MAX_PROMPT_LENGTH`. |
| `neova/config.py` | If needed only: nothing beyond what exists (`get_api_base_url()` already returns the local API URL). No new env vars — model names already live in `.env.example`. |
| `tests/test_conversation.py` (new) | Offline French traces with an injected fake chat model (reuse the `FakeEmbedder` injection pattern) and frozen clock: internet / billing / moving / booking / termination traces; multi-turn confirmation, refusal, change-of-slot → fresh confirmation; booking success → `rendez-vous confirmé` with saved ID, booking failure → never a success message; sensitive-topic immediate handoffs (urgent/ordinary/no-session generic route); uncertain (evidence-gate) and unsupported-question cases; assertion that no `POST /appointments` fires before the deterministic user yes. |
| `docs/issue-5-conversation-graph-plan.md` (this file) and later `docs/issue-5-conversation-graph-report.md` | Plan now; report with actual traces after implementation. |
| `docs/system_design.md` graph sketch reference only — **no edit**; the sketch already matches the issue. | |

**Commit boundaries** (per the issue): ① routing/tool-adapter commit; ② pending-state/booking-flow commit; ③ evidence/handoff + French trace tests commit. Provider retry/fallback policy stays out (issue #6).

## 3. Files that should not change

- `corpus/*` and `data/neova_data.json`: read-only; byte-stability must keep holding.
- `neova/db.py`: booking atomicity, handoff storage, schema and migration logic are done and tested; conversation state must **not** be persisted into SQLite in this PR.
- `neova/customer_api.py`, `neova/session.py`, `neova/clock.py`: scoped reads, session issuance and demo clock are finished contracts the graph consumes.
- `neova/retrieval.py`, `neova/chunking.py`, `neova/embeddings.py`, `neova/corpus.py`, `neova/sources.py`, `neova/pdf_extractors.py`: issue #4 scope; consumed as-is. If a search interface proves insufficient, widen it there with a recorded reason — not by editing behavior.
- `neova/models.py`: `chat_model()` stays as-is; fallback model handling is issue #6.
- `tests/test_foundation.py`, `test_customer_api.py`, `test_retrieval.py`, `test_dto.py`, `test_models.py`, `test_pdf_extractors.py`: existing contracts stay green and untouched (in particular `/foundation/graph` keeps its foundation-only contract).
- `pyproject.toml`, `uv.lock`: `langgraph`/`langchain-openai` already pinned — no dependency changes.
- `main.py`, `.env.example`, `README.md`, `manual-test-customer-api.md`, `docs/issue-2-*`, `docs/issue-3-*`, `docs/issue-4-*`, `take_home_relation_client.md`: out of this PR's boundary (README rewrite is issue #8; resilience is #6; eval cases are #7 — only the French trace tests this issue needs live here).
- `.env`, `*.db`: never committed; conversation tests run offline with fakes and temp databases.

## 4. Smallest implementation sequence (maximum 7 steps)

1. **Routing/tool-adapter commit:** create `neova/tools.py` — wrap the FastAPI reads, `POST /appointments` and `POST /handoffs` (HTTP with `X-Demo-Session` propagation) and `neova.retrieval.search` as bounded custom tools with typed results and failures. Expand `GraphState` with the multi-turn fields. Unit-test tool adapters offline (in-process ASGI transport or `TestClient` against a temp-DB app).
2. **Classifier + named routes:** replace the placeholder `classify` node with real classification into named routes — `internet`, `billing`, `moving`, `booking`, `termination`, `sensitive_handoff`, `unsupported` — with conditional edges to `public_search`, `api_read`, or both. Keep steps bounded: one classification, at most one retrieval pass, at most one API read; no tool loop. Inject the chat model so tests can substitute a fake.
3. **Pending-state/booking-flow commit:** implement `neova/conversation.py` — pending-booking store keyed by session token (customer reference, `slot_id`, formatted Europe/Paris slot, enumerated `reason_id`, `confirmation_pending`) and the deterministic user-yes gate (inspects the actual user message; enumerated French affirmatives only). Changing slot or reason clears confirmation. The booking node calls the tool only after the yes, and emits `rendez-vous confirmé` **only** on a successful API result containing a saved appointment ID; any `BookingFailure`/HTTP error produces a French failure message, never a success claim. The graph asks for at most one missing detail per turn (reference → slot → reason).
4. **Evidence check + French answer/uncertainty:** the `evidence_check` node honors `SearchOutcome.degraded` and `evidence_gate` flags: current/archived prices, fee-timing conflicts, missing invoice line items, postcode-only incidents produce French uncertainty phrasing, citing public passages or labeling authorized API values; retrieved text is data, never instructions; internal routing rules are never quoted and a handoff never claims a human accepted the case.
5. **Handoff routing:** sensitive-topic immediate handoff (privacy rights, fraud, legal threat, death, Pro contracts, protected/minor, distress) plus unresolved source conflicts, missing customer-specific facts, billing disputes and unsupported mutations. Urgency mapping (distress/fraud/legal → `urgent`, else `normal`), summary limited to factual non-private content, reference returned only when `db.save_handoff` stored the row. With no demo session, offer a generic human route disclosing no private data.
6. **HTTP surface:** add conversation DTOs and the session-bound `POST /agent/chat` endpoint returning the French reply + trace (route, tools, confirmation state, citations); `POST /foundation/graph` untouched.
7. **Tests/report commit:** add `tests/test_conversation.py` (fake chat model, frozen clock, temp DBs) covering the issue's evidence list — French traces per request type, multi-turn confirmation/refusal/change-of-slot, success/failure booking, grounded citations vs authorized-value labels, urgent/ordinary/no-session handoffs, uncertain and unsupported cases — then write `docs/issue-5-conversation-graph-report.md` with actual traces from the test run.

## 5. Risks and unresolved assumptions

- **Confirmation must be deterministic, not model-judged.** The acceptance criterion explicitly forbids counting a model-generated "yes". Decision: the gate reads the raw latest user turn and matches an enumerated set of French affirmatives (`oui`, `je confirme`, `c'est bon`, `d'accord`, `validez`, …). Risk: natural variants ("ça marche", "vas-y") — enumerated conservatively; anything ambiguous asks again rather than booking. Unresolved: exact list needs review.
- **State lifetime.** Pending-booking state is process-local and keyed by the demo session token, like `SessionStore`: survives across turns, dies with the process. The issue requires persistence across turns only. Assumption accepted; documented in the report rather than silently dropped.
- **API-as-tool transport.** The brief requires wrapping the FastAPI service as tools; the graph will call it over HTTP (`API_BASE_URL`) with the caller's `X-Demo-Session` header. Risk: self-call over the network while the same process serves the API adds fragility; mitigation: use in-process ASGI transport via `httpx` against `neova.app.app` (same code path, no loopback server dependency), with the real HTTP surface still exercised by tests.
- **Test doubles vs live model.** All acceptance traces must run offline with a fake chat model; a live OpenRouter run is optional evidence behind the budget gate (issue #6 hardens retries/fallback — the fake must expose the same interface). Risk: the fake drifts from real model behavior; mitigated by keeping prompts and parsing strict (JSON-instructed classification, narrow output schema).
- **Internal-rule leakage.** The sensitive-topic table lives in code; `procedure-escalade-n2` and other `public internal` chunks stay excluded from any answer context (already enforced by the public-only index). Tests must assert internal rule wording never appears in replies.
- **Boundedness.** Each request executes at most: 1 classification, 1 retrieval, 1 API read, 0-or-1 booking, 0-or-1 handoff. A step counter in `GraphState` hard-stops beyond the bound — no agent loop, matching the non-goal.
- **`/foundation/graph` contract.** Changing it would break `tests/test_foundation.py`; decision: leave it untouched and add a separate conversation endpoint. Assumption: reviewers accept two graph entry points during the transition.
- **Conversation cost.** Real French traces through a live model spend the shared key; all CI evidence uses doubles, and any live demo run is reported with its actual token spend in the follow-up report, never estimated in its place.
- **Reason enumeration.** Bookable `reason_id`s come from the fixture (`no_internet`, `installation`, …); the "ask one missing detail" node must enumerate them rather than free-form a reason, otherwise `POST /appointments` 422s by design. The graph maps French phrasing onto the enumerated list and hands off if no category fits.

## 6. Exact verification commands (after implementation)

From the repository root in PowerShell, with `uv` installed. Tests use temporary databases, faked chat models and **no network calls**. Planned, not yet run:

```powershell
uv sync --locked --extra dev
uv run --locked --extra dev python -m compileall -q main.py neova tests
uv run --locked --extra dev python -m pytest tests/test_conversation.py -q
uv run --locked --extra dev python -m pytest tests/test_conversation.py tests/test_retrieval.py tests/test_dto.py tests/test_customer_api.py tests/test_foundation.py tests/test_models.py -q
git diff --check
git diff --exit-code HEAD -- data/neova_data.json corpus/
git status --short
```

Optional live acceptance evidence (single supplied key, only after a budget check; actual traces with real token spend reported, never invented):

```powershell
# Budget check before any live run:
curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $env:OPENROUTER_API_KEY"

# One-process app, frozen demo clock, then a bounded multi-turn conversation:
$env:CLOCK_MODE="frozen"; $env:DEMO_TIMESTAMP="2026-08-26T12:00:00+02:00"
uv run --locked --env-file .env -- python main.py
# … then POST /demo/sessions, and send French turns to POST /agent/chat,
# capturing route/tools/confirmation state from the trace output.
```

Evidence (test traces for booking confirmation/refusal/change-of-slot, grounded citations, handoffs, uncertain and unsupported cases) goes in `docs/issue-5-conversation-graph-report.md` after the checks actually run.
