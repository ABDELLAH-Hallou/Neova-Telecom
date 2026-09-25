# Issue #6 / PR 5 — resilience plan (approval required)

**Status:** plan only; no implementation has started. Target branch: `6-pr-5-resilience-bounded-failures-provider-fallback-and-spend` (current). GitHub issue [#6 — PR 5: Resilience: bounded failures, provider fallback and spend](https://github.com/ABDELLAH-Hallou/Neova-Telecom/issues/6). Depends on #3 (API), #4 (retrieval), #5 (graph) — all merged. No test outcome is claimed here.

## 1. Current relevant behavior

**Model calls (chat) — two separate call sites, no shared policy:**

- `neova/nodes/french_answer.py` builds `chat_model("openrouter")` (`ChatOpenAI` with `CHAT_MODEL`). `max_retries` is **not set**, so the `openai` client's own default retry loop runs — bounded in count but not **our** policy: no explicit Retry-After handling we control, no provider fallback, no usage/spend capture. On any exception the node answers `MODEL_UNAVAILABLE_REPLY` (French unavailability + handoff offer) — the safe terminal shape already exists, but nothing was measured or retried under our rules.
- `neova/classifier.py` (`openrouter_classifier`) sets `max_retries=0` on purpose ("retry policy belongs to issue #6"). One call per turn; any failure degrades `classify_node` to the deterministic keyword router (`neova/nodes/classify.py`, `source="keywords", degraded=True`). No fallback model, no usage capture.
- `CHAT_FALLBACK_MODEL` is declared in `.env.example` (line 20) and **read nowhere**. No code verifies that primary and fallback resolve to different upstream providers.

**Embeddings (`neova/embeddings.py`):**

- `openrouter_embedder` already does bounded retries (`MAX_ATTEMPTS=3`) on `{408, 429, 500, 502, 503, 529}` with fixed backoff `(0.5, 1.0, 2.0)` — but fixed `time.sleep`, **no Retry-After honoring, no jitter**, and time is not injectable (tests cannot assert waits deterministically).
- Budget gate exists: `key_status()` reads `/key` (`usage`, `limit`, `limit_remaining`) and `ensure_budget(min_remaining_usd=1.0)` fails closed **before each batch** — the exhausted-cap direction is already implemented for optional embedding batches.
- **No per-call usage/cost logging anywhere**: token counts, retries, model/provider and estimated USD are never recorded; nothing distinguishes "unknown usage" from zero.
- Downstream degradation is already correct: `retrieval.search` degrades a failed **query** embedding to FTS5 with `query_embedding_failed_fts_fallback`; `index_corpus` stops the batch run, keeps valid cached vectors and marks the index `degraded` (`index_incomplete_semantic_unavailable`) with a reason in meta.

**API failure recovery:**

- `neova/tools.py::_request` makes exactly one HTTP attempt over the in-process ASGI transport; any `>=400` becomes `ToolError(status, detail)`, ASGI failure becomes `ToolError(503, "Service local momentanément indisponible")`. **No retry on 500.**
- Read path (`neova/nodes/gather.py`, `booking_flow.py`): a failed read is recorded (`customer_summary.read`/`incidents.read` skipped, `degraded=["api_read_failed"]`, `api_values["*_error"]`) but the customer gets no explicit short French failure + handoff offer for the failed read itself — the failure only degrades context, and the answer may still be produced.
- Booking path (`neova/nodes/booking_flow.py::_execute_booking`): on `ToolError` 409 → conflict reply (correct). On **any other status (e.g. 500/timeout)** → reply "Le rendez-vous n'a pas pu être enregistré… Aucun rendez-vous n'a été créé" — this is **wrong under 500/timeout**: the write may have reached the store, so claiming it was not created is an unsupported answer, and `conversation.clear(token)` is not called (good), but no by-key verification happens.
- The by-key machinery already exists and is unused by the graph: `GET /appointments/by-key/{key}` (`neova/app.py:89`, `neova/customer_api.py::appointment`) and `db.appointment_by_key` + idempotent `db.book_appointment` (`replayed: True/False`). The graph never calls it.
- The idempotency key is deterministic: `conversation.confirmation_key(customer_id, slot_id, reason_id)` = `conv:{customer}:{slot}:{reason}`.

**Spend/budget surface:** only `embeddings.key_status()` (one-off read). No redacted per-call log, no way to sum known chat + embedding spend, no "insufficient → stop optional calls" gate for chat calls (the gate runs only inside the embedder).

## 2. Files that should change

| Path | Minimum intended change |
| --- | --- |
| `neova/provider.py` (new) | Shared OpenRouter **chat** policy used by both call sites: route list = `[CHAT_MODEL, CHAT_FALLBACK_MODEL]` (fallback required to exist and to resolve to a **different upstream provider**); per route: honor `Retry-After` (429/529), else exponential backoff with jitter; at most **2 retries** on the primary; then one verified fallback attempt; both failing → typed `ProviderUnavailable`. Injectable clock/sleeper and injectable transport (fake responses) so tests are deterministic. Captures `usage`, upstream `provider`/`model` from the response, and retry count per call. |
| `neova/usage.py` (new) | Per-call usage ledger (process-local + optional JSONL log): model, upstream provider, route (primary/fallback), input/output tokens, retries, estimated USD (price from a small per-model table or response-included cost when available), attempts; **missing usage recorded as `unknown`, never 0**; redaction rules: never the key, never customer data. `spend_summary()` sums known chat + embedding spend and reports unknowns separately. |
| `neova/config.py` | Read `CHAT_FALLBACK_MODEL` (`get_chat_fallback_model()`). No other new env vars; retry numbers stay code constants (documented), so env surface stays minimal. |
| `neova/classifier.py` | Route the single classification call through the shared policy (keep one call per turn semantics at the *node* level; the policy owns waits/fallback). Keep `provider: {"require_parameters": True}` so usage is present. |
| `neova/nodes/classify.py` | No routing change; ensure a policy-exhausted classifier still degrades to the keyword router with `degraded=True` and now records the spent usage. |
| `neova/nodes/french_answer.py` | Use the shared policy; on `ProviderUnavailable` keep the existing French unavailability + handoff-offer reply (`MODEL_UNAVAILABLE_REPLY` — already the required shape) and mark it in `degraded`. |
| `neova/embeddings.py` | Honor `Retry-After` when present, else exponential backoff **with jitter**, through an injectable sleeper; per-call usage record via `neova.usage`; keep `ensure_budget` gate and `MAX_ATTEMPTS` semantics (now: at most 2 retries like chat). `time` stays importable but never called directly in tests. |
| `neova/tools.py` | `_request` gains a bounded retry policy: safe **read** methods retried **once** on 500/502/503/ASGI failure, then `ToolError` carries the final status; state-changing methods (`POST`) are **never** retried here — booking recovery is by-key in the graph. Expose a way for tests to inject failure sequences (monkeypatch the transport). |
| `neova/nodes/gather.py` | After a read exhausts its retry: set `degraded=["api_read_failed"]` as today **and** surface a short French failure line + handoff offer in the reply when the read was essential (e.g. incidents for `internet`); the answer node composes it instead of answering from partial context. |
| `neova/nodes/booking_flow.py` | On booking `ToolError` 500/timeout: call `tools.appointment_by_key(token, customer, key)` — saved ID → confirmed (reuse the `replayed` reply shape); **absent** → "unconfirmed" French reply + handoff offer, **void the pending code** (`mark_proposed` reset / `expired=True`) so a fresh check and a new confirmation phrase are required before any retry; **check itself fails** → same unconfirmed path, no claim either way. Never replay the write blindly. |
| `tests/test_resilience.py` (new) | Deterministic fault injection: fake transport + controlled clock/sleeper. 429 with `Retry-After` → asserted wait honored; 429/529 without → asserted exponential+jitter waits; 3× failure on primary → exactly 2 retries + verified fallback switch; both routes fail → French terminal reply; embedding 429 exhaustion → FTS5-degraded outcome; read 500 → one retry then French failure + handoff offer; booking 500 with saved/absent/failed by-key; exhausted `/key` cap → optional calls blocked with no attempted paid call; spend summary sums known + labels unknown. |
| `docs/issue-6-resilience-plan.md` (this file) and later `docs/issue-6-resilience-report.md` | Plan now; report with actual traces after implementation. |
| `tests/test_conversation.py`, `tests/test_retrieval.py` | Minimal touch only if the injected fakes must expose the new injectable sleeper/transport seams (keep all existing contracts green). |

**Commit boundaries** (per the issue): ① provider retry/fallback + budget/usage commit (steps 1–3); ② API-failure recovery commit (steps 4–5); ③ deterministic fault-test/evidence commit (steps 6–7). No evaluation-reporting work moves into this PR.

## 3. Files that should not change

- `corpus/*` and `data/neova_data.json`: read-only; byte-stability must keep holding.
- `neova/db.py`: booking atomicity, idempotency (`book_appointment`, `appointment_by_key`) and handoff storage are done and tested — this PR **consumes** the by-key endpoint, it must not change semantics.
- `neova/app.py`: HTTP routes and idempotency are correct; recovery lives in the graph/tools layer. (No route changes; `POST /agent/chat` contract stays.)
- `neova/retrieval.py`: FTS5 degradation, cached-vector retention and `degraded` index state are already the issue's required behavior — consumed as-is.
- `neova/customer_api.py`, `neova/session.py`, `neova/clock.py`: finished contracts. (`clock.py` stays for demo time; the retry clock/sleeper is its own injectable seam in `provider.py`, not a reuse of the frozen demo clock.)
- `neova/graph.py`: wiring and step bound are issue-#5 contracts; resilience changes live inside nodes, not in edges.
- `neova/conversation.py`: confirmation gate, idempotency key and pending-state rules stay; only `booking_flow.py` reacts to API failure.
- `neova/dto.py`, `neova/prompt/*`, `neova/chunking.py`, `neova/pdf_extractors.py`, `neova/sources.py`, `neova/corpus.py`, `neova/extraction.py`: no surface change.
- `neova/models.py`: `chat_model()` remains for `/models/chat`; the graph path moves to `provider.py` (fallback handling lives there).
- `tests/test_foundation.py`, `test_customer_api.py`, `test_dto.py`, `test_models.py`, `test_pdf_extractors.py`: existing contracts stay green and untouched.
- `pyproject.toml`, `uv.lock`: no new dependency — `httpx`/`openai`/`urllib` and stdlib cover the policy; no lockfile churn.
- `main.py`, `README.md`, `.env.example` (already declares `CHAT_FALLBACK_MODEL`; nothing new needed), `docs/manual-*.md`, `docs/issue-2-*` … `issue-5-*`, `take_home_relation_client.md`: out of this PR's boundary. README/eval rewrite is issue #8.
- `.env`, `*.db`: never committed; resilience tests run fully offline with fakes and temp databases.

## 4. Smallest implementation sequence (maximum 7 steps)

1. **Usage ledger + provider policy skeleton** (commit ①): create `neova/usage.py` (per-call record: model, upstream provider, route, tokens, retries, est. USD, `unknown` when usage is missing; redaction; `spend_summary()`), then `neova/provider.py`: routes from `CHAT_MODEL` + `get_chat_fallback_model()`, honor `Retry-After` else exponential backoff with jitter, ≤2 retries on primary, then fallback, else `ProviderUnavailable`. Injectable sleeper + transport. Config gains `get_chat_fallback_model()`. Unit-test the policy against fake responses and a fake sleeper.
2. **Wire chat call sites** (commit ①): `classifier.py` and `french_answer.py` call through the shared policy; `classify_node` still degrades to keywords on exhaustion; `french_answer` keeps `MODEL_UNAVAILABLE_REPLY` as the terminal French answer on `ProviderUnavailable`. Usage recorded for every attempted call (including failed ones when the API returned usage).
3. **Embeddings policy + budget/usage finish** (commit ①): `embeddings.py` honors `Retry-After`, jitter, injectable sleeper; per-call usage through `neova.usage`; `ensure_budget` stays the gate before each batch and now also before any optional chat batch (indexing/batch runs only). Exhausted-cap test: gate raises before any paid call.
4. **Read recovery** (commit ②): `tools._request` retries a safe read **once** on 500/502/503/ASGI failure (never on 4xx, never for POST); `gather.py`/`booking_flow.py` summary/incidents/slots failures end in a short French failure + handoff offer when the read was essential, with `degraded` recording. Tests with injected failure sequences.
5. **Booking recovery** (commit ②): `booking_flow.py` on 500/timeout does the by-key check: saved ID → confirmed (replayed wording); absent → unconfirmed French reply + handoff offer + code voided (fresh confirmation required); check failure → same unconfirmed path, no success claim ever. Tests cover saved/absent/failed.
6. **Provider-route verification + fault tests** (commit ③): verification path that checks (offline, via response `provider` fields captured in the ledger, or `/models` metadata fetched by an explicit script) that primary and fallback chat routes are **different upstream providers** and the embedding model is the configured French-capable one — recorded in the report. Then `tests/test_resilience.py`: full deterministic matrix (Retry-After honored, bounded attempts, provider switch, terminal French answer, embedding degradation, read/booking recovery, exhausted cap, spend with unknowns labeled).
7. **Evidence + report commit** (commit ③): sample redacted per-call usage log and approximate spend (one small live run behind the budget gate, clearly labeled live) if the key allows; write `docs/issue-6-resilience-report.md` with the traces; keep the existing suite green.

## 5. Risks and unresolved assumptions

- **"Verified different-upstream-provider" fallback.** OpenRouter exposes the serving provider per response (`provider` field) and model metadata via `/models`; verification can be asserted offline from captured responses, but the *live* fact depends on OpenRouter's routing. Decision: assert from captured `provider` fields in tests; add a pre-publication manual check (script + curl) recorded in the report. Assumption: `CHAT_FALLBACK_MODEL` will be set to a model whose upstream differs from the primary's — needs a real name at publication time.
- **Retry-After vs. fixed timeouts.** A malicious/buggy `Retry-After` could stall a request thread. Decision: clamp honored delays to a small maximum (e.g. ≤8 s per wait, total budget per call bounded); beyond that treat as absent. Documented constant.
- **`ChatOpenAI` implicit retries must not double-count.** The graph path must set `max_retries=0` on the underlying client (as `classifier.py` already does) so *our* policy is the only retry layer; otherwise the `openai` client's own retries would break the "at most two retries" bound. This is a deliberate change to `french_answer`'s model construction.
- **Booking 500 semantics.** A 500 after a commit inside the API is indistinguishable from one before it — that is exactly why the by-key check decides. Risk: the by-key *check* itself 500s; decision: treat as unconfirmed (never claim failure of the write), void the code, require a fresh check. The idempotency key is deterministic, so a later retry with a fresh confirmation reuses the same key and replays safely (`replayed: True`).
- **Unknown usage must stay unknown.** Some providers/routes omit `usage`; the ledger records `unknown` and `spend_summary()` reports it separately — never as 0 USD. Risk: est. USD from a static price table drifts from OpenRouter billing; mitigated by cross-checking with `/key` `usage` in the report and labeling all figures as estimates.
- **Key/customer redaction.** The ledger never stores the API key, session tokens or customer IDs; tests assert redaction explicitly.
- **`529` handling.** 529 (OpenRouter "provider overloaded") may or may not carry `Retry-After`; the policy treats it exactly like 429: honor the header when present, else backoff.
- **Test seams vs. production code shape.** The sleeper/transport injection must be a plain keyword argument (no new framework) to keep the module importable offline; the `usage` ledger is process-local with an optional append-only JSONL path — it is not durable storage and dies with the process (same lifetime contract as `SessionStore`).
- **Non-goal discipline.** No second key, no direct upstream calls, no free tier, no unbounded loop, no Langfuse — the plan adds none of these; `/key` polling happens once per optional batch (as today), not per retry.

## 6. Exact verification commands (after implementation)

From the repository root in PowerShell, with `uv` installed. Tests use fake transports, controlled clock/sleeper, temp databases and **no network calls**. Planned, not yet run:

```powershell
uv sync --locked --extra dev
uv run --locked --extra dev python -m compileall -q main.py neova tests
uv run --locked --extra dev python -m pytest tests/test_resilience.py -q
uv run --locked --extra dev python -m pytest tests -q
git diff --check
git diff --exit-code HEAD -- data/neova_data.json corpus/
git status --short
```

Live, budget-gated evidence (optional, only with the supplied key set and after the offline suite is green):

```powershell
# Key spend / remaining cap (explicit, user-invoked — never printed from code)
curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $env:OPENROUTER_API_KEY" | jq '.data | {usage, limit, limit_remaining}'

# Budget-gated index run (stops if remaining cap is inadequate)
uv run --locked --extra dev python -m neova.retrieval index
```
