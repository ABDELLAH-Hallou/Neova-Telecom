# Issue #6 / PR 5 — resilience report

**Status:** implemented per `docs/issue-6-resilience-plan.md` (approved steps 1–7) plus three post-review revisions (Addenda 1–3 below; the body of this report describes the **final state**). Branch: `6-pr-5-resilience-bounded-failures-provider-fallback-and-spend`. Nothing added, committed or pushed. All evidence is from the offline deterministic suite; the optional live budget-gated run was **not** executed (it spends the shared $10 key — see Remaining work). Final offline results: **44 resilience tests, 170 total, all passing.**

## 1. Files changed (final state)

| Path | Change |
| --- | --- |
| `neova/usage.py` (new) | Per-call usage ledger: `CallRecord` (kind, model, route, upstream provider, status, prompt/completion/total tokens, `cost_usd`, retries, error). Missing usage/cost stays `None` → reported `unknown`, never 0. `spend_summary(records=None)` sums known costs per kind and counts unknown-cost calls. Redaction by construction: the field set cannot hold the key, session tokens or customer data. Persistence: when `USAGE_LOG` is configured (wired at app startup via `configure_from_env()`), every record is appended under the ledger lock as one redacted JSONL line (parent directories created); `load_records()` reloads it (corrupt lines skipped); CLI `python -m neova.usage [path]` summarizes a persisted log. Process-local ledger, same lifetime contract as `SessionStore`. |
| `neova/provider.py` (new) | Shared OpenRouter chat policy: routes `[CHAT_MODEL, CHAT_FALLBACK_MODEL]`; on 429/529 honor `Retry-After` — numeric seconds **or HTTP-dates** — capped at `MAX_RETRY_AFTER_SECONDS=8`, else exponential backoff (`0.5s`, `1.0s`) × jitter (`0.8–1.2`); **at most 2 retries on the primary**, then **one** fallback attempt; every route exhausted → typed `ProviderUnavailable`. Injectable transport + sleeper + jitter (tests run offline/deterministically). Default transport builds the OpenAI client with `max_retries=0` — the policy is the only retry layer. `normalize_chat_response` flattens SDK responses (usage/provider unknown-safe). `PolicyChatModel` keeps the node `.invoke(prompt)` shape, with `max_tokens` passthrough. Verification: `verify_routes()` (live: budget-gated one 1-token probe per route, upstream provider read from each response), `fallback_verification(records=…)` (offline report over ledger or a reloaded usage log), CLI `python -m neova.provider [--verify] [--usage-log <path>]`. |
| `neova/config.py` | Added `get_chat_fallback_model()` (optional; unset degrades to primary-only, never raises) and `get_usage_log_path()` (reads `USAGE_LOG`). |
| `neova/classifier.py` | The single classification call now runs through `provider.chat_invoke` (`kind="classifier"`); the classifier keeps building its own OpenAI client with `max_retries=0` and its exact request shape (asserted by `tests/test_classifier.py`, unchanged). Fallback model applied when configured. |
| `neova/nodes/classify.py` | No change needed: a policy-exhausted classifier already degrades to the keyword router with `degraded=True`; usage of failed attempts is now recorded by the policy. |
| `neova/nodes/french_answer.py` | `answer_model()` returns a `PolicyChatModel` with `max_tokens=ANSWER_MAX_TOKENS (500)` — the response cost is bounded — and `extra_body={"usage": {"include": True}}` for OpenRouter usage accounting. On exhaustion → `MODEL_UNAVAILABLE_REPLY` (French unavailability + handoff offer) and `degraded += ["chat_model_unavailable"]` (or `chat_model_unconfigured`). Deterministic short-circuit: `api_values["read_failure"]` → `READ_FAILURE_REPLY` (short French failure + handoff offer), no model answer on partial data. |
| `neova/embeddings.py` | Retry loop honors `Retry-After` (numeric or HTTP-date, capped) else exponential backoff × jitter, through the injectable `sleeper`; `MAX_ATTEMPTS=3` = initial call + at most two retries, like chat. Exactly one ledger record per attempt, written in the `else` branch only after the response validates as a usable batch (a malformed response can never be logged ok *and* failed). Success records carry `prompt_tokens` + `total_tokens`, `completion_tokens=None` (embeddings generate no completion tokens). `ensure_budget` gate unchanged (runs before each batch). |
| `neova/tools.py` | Every tool call runs behind a hard wait bound: `TOOL_TIMEOUT_SECONDS=10` enforced via a worker future (the in-process ASGI transport cannot enforce httpx timeouts). `_request`: safe **GET** reads retried **exactly once** on `{500, 502, 503, 504}`, ASGI `RuntimeError` and timeout; 4xx never retried; **POST never retried** — a POST timeout surfaces `ToolError(504)` so the graph can verify a booking by key. New read tool `appointment_by_key(token, customer_id, key)` → `GET /appointments/by-key/{key}`. |
| `neova/nodes/gather.py` | A summary or incidents read that fails even after its retry records `api_values["read_failure"] = {read, status, detail}` alongside the existing `degraded`/`*_error` entries, then **returns before the retrieval pass** — no embedding credit is spent on a turn that ends in the failure message (`degraded` records `search_skipped_read_failure`). |
| `neova/nodes/booking_flow.py` | On booking `ToolError` with status in `{500, 502, 503, 504}` (server failure/timeout → unknown outcome): `_recover_by_key` — a saved appointment confirms (replayed wording, `conversation.clear`); **absent or failed check** → `_UNCONFIRMED_REPLY` (French: unconfirmed, nothing claimed, handoff offer) **and the offered code is voided** (`conversation.void_confirmation`) so a fresh proposal and fresh confirmation phrase are required. Read-failure replies now end with the handoff offer. |
| `neova/conversation.py` | One additive store operation: `void_confirmation(token)` — keeps the pending slot+reason pair, un-arms confirmation, voids the code (`expired=True`). No existing rule changed. |
| `neova/app.py` | Lifespan wires `usage.configure_from_env()`. The `POST /models/chat` direct-model endpoint (and its unused imports) **removed** — it bypassed the graph and the policy. |
| `neova/dto.py` | `ChatRequest` deleted (its `Prompt` bound survives via `AgentChatRequest`). |
| `neova/models.py` | **Deleted** — it existed only to serve `/models/chat`; the graph path is `neova.provider`. (`langchain-openai` stays pinned in `pyproject.toml`/`uv.lock`; no lockfile churn.) |
| `.env.example` | `USAGE_LOG=` documented (empty = memory-only, the pre-existing behavior). The `OPENAI_API_KEY`/`OPENAI_CHAT_MODEL` block is now unused by code (left for the final-submission cleanup, see Remaining work). |
| `README.md`, `docs/manual-e2e-curl.md` | No longer document `/models/chat`; README test command fixed to `pytest tests -q` and its coverage/limitations list updated. |
| `tests/test_resilience.py` (new) | 44 deterministic fault-injection tests (offline, no real network; only the two timeout tests use a real 0.3 s/0.05 s margin) — matrix in §4. |
| `tests/test_dto.py`, `tests/test_customer_api.py` | Only their `/models/chat`/`ChatRequest` assertions removed. `tests/test_models.py` **deleted**. |
| `docs/issue-6-resilience-plan.md` | The approved plan (snapshot of the approval; superseded details are recorded in the addenda below). |
| `docs/issue-6-resilience-report.md` | This report. |

Not changed: `db.py`, `retrieval.py`, `graph.py`, `customer_api.py`, `session.py`, `clock.py`, `chunking.py`/extractors, `pyproject.toml`/`uv.lock`, `corpus/`/`data/`, and the other existing tests (`test_foundation.py`, `test_retrieval.py`, `test_classifier.py`, `test_conversation.py`, `test_pdf_extractors.py` — all still green). Historical issue docs (issue-2…5) left as snapshots.

## 2. Behavior added (mapped to the issue scope)

- **Chat 429/529**: `Retry-After` honored when present — numeric seconds or HTTP-dates — capped at 8 s, else exponential backoff with jitter; ≤ 2 retries on the primary; then one fallback attempt on the same supplied key; both routes failing → French unavailability + handoff offer (`MODEL_UNAVAILABLE_REPLY`), never an unsupported answer.
- **Embeddings**: bounded 429/529 retries through the same key (same wait policy, ≤ 2 retries); failed **query** embedding → FTS5 with `query_embedding_failed_fts_fallback` (pre-existing, now covered by tests); failed **indexing** keeps cached vectors and marks the index `degraded` (pre-existing, unchanged).
- **API read 500/timeout**: one retry, then a short French failure + handoff offer (deterministic short-circuit, no answer built on partial data, no embedding credit spent after the failure).
- **Booking 500/timeout**: never replayed blindly — by-key check decides: saved ID → confirmed; absent/failed check → *unconfirmed* + handoff offer + voided code requiring a fresh check and a fresh `CONFIRMER RDV` phrase.
- **Budget/spend**: `/key` gate blocks optional calls and the live verification probes before any paid attempt; every chat, classifier and embeddings attempt is logged (model, upstream provider, route, tokens, retries, cost when the API reports it); unknown usage labeled unknown; `usage.spend_summary()` sums known chat + embedding spend; evidence persists via `USAGE_LOG` (locked, dir-creating JSONL appends). No key or customer data ever recorded.
- **Route verification**: `python -m neova.provider --verify` performs the real proof — one budget-gated 1-token live probe per route, upstream provider read from each response; `python -m neova.provider --usage-log <path>` reports observed providers from a persisted log; `fallback_verification()` works offline over ledger/reloaded records.
- **Boundedness**: answer output capped at `ANSWER_MAX_TOKENS=500`; classifier at 150; verification probes at 1; tool calls at `TOOL_TIMEOUT_SECONDS=10`.

## 3. Commands run and real results (final run)

| Command | Result |
| --- | --- |
| `uv run --locked --extra dev python -m compileall -q main.py neova tests` | exit 0 |
| `uv run --locked --extra dev python -m pytest tests/test_resilience.py -q` | **44 passed** |
| `uv run --locked --extra dev python -m pytest tests -q` | **170 passed**, 1 pre-existing `anyio` deprecation warning |
| `git diff --check` | exit 0 (no whitespace errors) |
| `git diff --exit-code HEAD -- data/neova_data.json corpus/` | exit 0 (read-only inputs byte-stable) |
| `python -m neova.provider` / `python -m neova.usage` (offline demos, fake names) | both print correct JSON reports, exit 0 — no network, no spend |
| route-table check (`python -c` on `neova.app.app`) | no `/models/chat` path |

Per-revision results are kept in each addendum's verification table below (28 → 30 → 41 → 44 resilience tests as the review fixes landed).

Live budget-gated evidence (`curl …/api/v1/key`, `python -m neova.retrieval index`, `python -m neova.provider --verify`) was **not** run — it spends the shared key; see §6.

## 4. Test evidence matrix (all offline, controlled sleeper, fake transports)

- 429 with `Retry-After: 3` → waits exactly `[3.0]`; 429/529 without header → waits exactly `[0.5, 1.0]`; `Retry-After: 30` → **capped to exactly `[8.0]`**.
- **HTTP-date `Retry-After`**: future date 45 s → parsed (~45) and capped to an 8.0 s wait; past date / naive date / garbage / empty / `None` → no delay (backoff path); plain `"3"` → exactly `3.0`.
- Primary fails 3× → exactly 2 retries, then the fallback model is called once; ledger shows routes `primary×3 failed, fallback×1 ok`.
- Non-429/529 failure switches to fallback immediately (no wait); both routes failing → `ProviderUnavailable` after 4 attempts, ledger records 4 failures.
- Missing `CHAT_FALLBACK_MODEL` → primary-only, still bounded.
- Response without usage → ledger `prompt_tokens/cost = None`, spend summary labels `calls_unknown_cost`, known spend stays 0 — never silently free.
- Ledger redaction asserted (key value absent; fixed safe field set), including under 8 concurrent recording threads (80/80 well-formed JSONL lines, none interleaved).
- **Live route verification** (`verify_routes`): one 1-token probe per route, upstream provider from each response — distinct → verified; same provider → not verified; failed probe → reported, not guessed; exhausted `/key` cap → `BudgetExceeded` before any probe.
- Classifier through the policy: 3× 429 on the classifier model then fallback success → valid classification, ledger `classifier` routes recorded; invalid JSON still fails closed (`ClassifierError`).
- Embeddings: `Retry-After: 2` honored (tokens logged as `prompt_tokens`/`total_tokens`, `completion_tokens=None`); 429 exhaustion → exactly 2 waits then `EmbeddingError`; malformed response (no `data`) → exactly three `failed` records, no phantom success; exhausted `/key` cap → `BudgetExceeded` **before** any paid call (zero HTTP attempts); query-embedding failure → FTS5-degraded search still returns the right passage.
- **Usage persistence**: JSONL log written under the lock (missing directories created), reload skips corrupt lines, `configure_from_env()` wires `USAGE_LOG`, both CLIs summarize a persisted file.
- API reads: 500,500 → one retry then success; 500,500 twice → `ToolError(500)`; POST never retried (1 call); ASGI failure retried once for GET, never for POST; **GET timeout → one retry then `ToolError(504)`; POST timeout → `ToolError(504)` with a single call**; graph-level exhausted read → French failure + handoff offer + `api_read_failed_essential`, with a counting embedder proving zero embedding calls.
- Booking 500 through the real 4-turn conversation flow: by-key **saved** → confirmed with saved ID 42 (exactly one POST, one by-key GET); by-key **absent** → unconfirmed reply + voided code (old `CONFIRMER RDV <code>` answered "expiré", zero API calls); by-key **check failing** → unconfirmed, no claim either way.
- Graph-level chat exhaustion → `MODEL_UNAVAILABLE_REPLY` + `chat_model_unavailable` in `degraded`, waits `[0.5, 1.0]`, ledger 4 failed records.
- Answer model carries the `ANSWER_MAX_TOKENS=500` bound and requests usage accounting.
- Spend summary: known chat (0.012) + known embeddings (0.0002) sum to 0.0122; unknown-cost calls counted per kind.

## 5. Assumptions and decisions (all previously flagged in the plan)

1. **Fallback optional at runtime.** `get_chat_fallback_model()` returns `None` when unset → primary-only degradation. The issue requires the fallback to be *configured and verified* **before publication**, not for every run to fail without it. Publication checklist: set `CHAT_FALLBACK_MODEL` in env, run one real turn, check `python -m neova.provider` reports `distinct_upstreams_verified: true`.
2. **Costs come from the API, not a price table.** The answer path requests OpenRouter usage accounting (`extra_body={"usage": {"include": True}}`) and the ledger records `usage.cost` when returned; anything else stays unknown. No static per-model price table (drift risk). The classifier reuses its exact request shape (asserted byte-for-byte by `tests/test_classifier.py`), so its cost is typically unknown — labeled unknown, per the issue.
3. **Usage accounting not added to the embeddings payload** (undocumented for `/embeddings`; sending it risks a 400). Tokens from the response's `usage` are recorded when present.
4. **Retry-After cap = 8 s** (per attempt); both numeric seconds and RFC 7231 HTTP-dates are supported (an HTTP-date is converted to the delay it represents; past/naive/unparsable values fall back to the backoff path). Originally HTTP-dates were treated as absent — fixed in Addendum 3.
5. **529 treated exactly like 429** (honor header, else backoff).
6. **`conversation.void_confirmation`** added as an *additive* store operation (plan §3 said `conversation.py` rules stay — no rule changed; the unconfirmed path needed a public way to void the offered code).
7. **Essential reads** = summary (business routes) and incidents (internet route); their exhaustion short-circuits the answer. Non-essential degradations keep the previous behavior.
8. **Live evidence skipped on purpose**: any real call spends the shared $10 key. The redacted-sample and live spend evidence (plan step 7's optional part) should be produced only with explicit approval.

## 6. Remaining work

- **Live evidence (needs approval / key budget):** one budget-gated live run — `curl /key` snapshot, a small `python -m neova.retrieval index`, one real chat turn — then record the redacted per-call ledger and approximate spend in this report, and confirm `distinct_upstreams_verified` with the real `CHAT_FALLBACK_MODEL` name.
- **Publication checklist:** set `CHAT_MODEL` / `CHAT_FALLBACK_MODEL` / `EMBEDDING_MODEL` to the final French-capable models; set `USAGE_LOG` to an evidence path; run `python -m neova.provider --verify` (two ~1-token calls behind the budget gate) and keep the printed report + the JSONL log as evidence; summarize spend with `python -m neova.usage <log path>`.
- Final-submission cleanups (would touch shared files, deliberately deferred): drop the now-unused `OPENAI_API_KEY`/`OPENAI_CHAT_MODEL` block from `.env.example` and decide whether `langchain-openai` should leave the runtime dependencies (would churn `uv.lock`).
- Not in this PR (per issue boundaries): full README rewrite and evaluation reporting (issue #8 / #7); no Langfuse, no second key, no free tier.

---

## Addendum — post-review revision (same PR, no commits)

Three review fixes applied on top of the implementation above.

### A1. `/models/chat` endpoint removed (it bypassed the graph and the policy)

- `neova/app.py`: `run_model_chat` route, `ChatRequest` import and `from .models import chat_model` removed (and the now-unused `ConfigurationError` import).
- `neova/dto.py`: `ChatRequest` deleted (its `Prompt` bound survives via `AgentChatRequest`).
- `neova/models.py` **deleted** — it existed only to serve that endpoint; the graph path is `neova.provider` (verified fallback, retries, usage ledger). `langchain-openai` remains a pinned dependency in `pyproject.toml`/`uv.lock` (no lockfile churn), now used transitively only.
- Tests: `tests/test_models.py` **deleted**; `tests/test_dto.py` and `tests/test_customer_api.py` lost only their `/models/chat`/`ChatRequest` assertions. `README.md` and `docs/manual-e2e-curl.md` no longer document the endpoint (historical issue-3/5 docs left untouched — they are snapshots of those issues).
- Verified: `neova.app.app` route table contains no `models/chat`; `OPENAI_API_KEY`/`OPENAI_CHAT_MODEL` in `.env.example` are now unused by code (left untouched on purpose — env example is outside this revision's scope).

### A2. Tool timeout is now actually enforced

httpx's ASGI transport **cannot** enforce per-request timeouts (verified in `httpx/_transports/asgi.py`: `handle_async_request` never reads the timeout extension) — `TOOL_TIMEOUT_SECONDS` was declared but dead. Now in `neova/tools.py`:

- every tool call runs on a small worker pool behind `_call_bounded`: `future.result(timeout=TOOL_TIMEOUT_SECONDS)`;
- expiry raises an internal `_RequestTimeout` → **GET** gets its single retry then `ToolError(504, "Le service local n'a pas répondu dans le délai imparti")`; **POST** immediately → `ToolError(504, …)` with no retry;
- `504` is in `booking_flow._BOOKING_UNKNOWN_STATUSES`, so a timed-out booking is decided by the **by-key check** (the worker thread may still finish the write in the background — exactly the unknown-outcome case the recovery path handles).

New tests: `test_read_timeout_is_retried_once_then_504` and `test_post_timeout_surfaces_504_without_retry` (deterministic: slow scripted handler at 0.3 s vs a 0.05 s monkeypatched bound).

### A3. Failed reads no longer spend embedding credit

`neova/nodes/gather.py`: once `api_values["read_failure"]` is set (summary or incidents read exhausted its retry), the node **returns before the retrieval pass** — `embedder_factory()` is never built and no embedding call is attempted on a turn that ends in the short French failure + handoff offer; `degraded` records `search_skipped_read_failure`. The answer node's deterministic `READ_FAILURE_REPLY` short-circuit then fires with zero model calls.

Test: `test_exhausted_read_gives_french_failure_and_handoff_offer` now also injects a counting embedder and asserts it was never reached.

### Verification (this revision)

| Command | Result |
| --- | --- |
| `uv run --locked --extra dev python -m compileall -q main.py neova tests` | exit 0 |
| `uv run --locked --extra dev python -m pytest tests/test_resilience.py -q` | **30 passed** (7 s) |
| `uv run --locked --extra dev python -m pytest tests -q` | **156 passed** (126 pre-existing after removing the 2 endpoint tests, + 30 resilience) |
| `git diff --check` | exit 0 |
| `git diff --exit-code HEAD -- data/neova_data.json corpus/` | exit 0 |
| route-table check (`python -c` on `neova.app.app`) | no `/models/chat` path |
| `git status --short` | files in §1 plus this addendum's list; deletions (`neova/models.py`, `tests/test_models.py`) unstaged; **nothing added, committed or pushed** |

### Remaining work (updated)

- §6 items unchanged, plus: decide whether to drop the now-unused `OPENAI_API_KEY`/`OPENAI_CHAT_MODEL` block from `.env.example` (README rewrite is issue #8; left untouched here) and whether `langchain-openai` should be dropped from the runtime dependencies at final submission (would churn `uv.lock`, so out of this revision).

---

## Addendum 2 — second review revision (same PR, no commits)

Six review fixes applied.

### B1. Retry-After is now capped, not discarded

`provider.retry_delay`: a valid header now yields `min(retry_after, MAX_RETRY_AFTER_SECONDS)`; previously a header beyond the clamp (e.g. `30`) fell through to the 0.5 s backoff. Test `test_retry_after_is_clamped` now asserts the wait is exactly `[8.0]` for `Retry-After: 30`.

### B2. Fallback verification is real

The ledger-only report could never see another process's server calls. `neova/provider.py` now has:

- `verify_routes(transport=None)` — the live proof: budget-gated (`embeddings.ensure_budget(0.10)`), then **one minimal probe per route** (`max_tokens=1`, prompt `"OK"`) on the supplied key; the upstream provider is read from each response itself and every probe is recorded in the ledger; a failed probe is reported `failed`, never guessed; `distinct_upstreams_verified` requires two *observed* different providers.
- CLI: `python -m neova.provider --verify` (live; spends two 1-token calls, gated) and `python -m neova.provider --usage-log <path>` — the offline report now reloads observed providers from a **persisted** usage log (`usage.load_records`), so evidence survives process restarts. `fallback_verification(records=…)` accepts reloaded records.

Tests: `test_verify_routes_probes_each_route_live_and_reports_providers` (each route called once, minimal tokens, distinct → verified), `…same_upstream_is_not_verified`, `…failed_probe_is_reported_not_guessed`, `…is_budget_gated` (gate fires before any call), `test_provider_cli_reads_persisted_providers`. The live `--verify` run itself remains publication-time work (needs the key; see Remaining work).

### B3. Embedding tokens logged correctly

Embeddings never generate completion tokens: the ledger now has an explicit `total_tokens` field (also filled for chat from `usage.total_tokens`); the embeddings success record stores `prompt_tokens` + `total_tokens` with `completion_tokens=None`. Test asserts `completion_tokens is None and total_tokens == 7`.

### B4. No duplicate usage records

In `embeddings.embed`, the success record was written before `response["data"]` was validated — a malformed response was logged `ok` and then `failed`. Restructured to try/except/**else**: the response is parsed into vectors inside the `try`, and the single `ok` record is written in the `else` branch only after validation. Test `test_malformed_embedding_response_is_never_logged_ok`: a response without `data` yields exactly three `failed` records, no phantom success.

### B5. Usage evidence is persisted

- `neova/config.py`: new `get_usage_log_path()` reading **`USAGE_LOG`**; `.env.example` documents it (empty = memory-only, the pre-existing behavior).
- `neova/usage.py`: `configure_from_env()` (wired in `app.lifespan` startup), `load_records(path)` (tolerant reload: corrupt lines skipped, absent fields stay unknown), `spend_summary(records=None)`, and a CLI — `python -m neova.usage [path]` — that summarizes a persisted log (or `USAGE_LOG`, or the current process).
- Tests: `test_usage_log_persists_and_reloads`, `test_load_records_skips_corrupt_lines`, `test_configure_from_env_wires_the_usage_log`, `test_usage_cli_summarizes_a_persisted_log`.

### B6. Answer-model output is bounded

`french_answer.ANSWER_MAX_TOKENS = 500` is passed through the new `PolicyChatModel(max_tokens=…)`: answer generation now has an explicit output-token cap (the classifier keeps its 150; verification probes use 1). Tests: `test_answer_model_output_is_bounded` (the wired answer model carries the bound) and the extended passthrough assertion in `test_answer_model_requests_usage_accounting`.

### Verification (this revision)

| Command | Result |
| --- | --- |
| `uv run --locked --extra dev python -m compileall -q main.py neova tests` | exit 0 |
| `uv run --locked --extra dev python -m pytest tests/test_resilience.py -q` | **41 passed** (8 s) |
| `uv run --locked --extra dev python -m pytest tests -q` | **167 passed** (91 s) |
| `git diff --check` / corpus byte-stability | exit 0 / exit 0 |
| `python -m neova.provider` / `python -m neova.usage` (offline demo) | both print correct JSON reports, exit 0 |
| `git status --short` | expected files only (`.env.example` now also modified for `USAGE_LOG`); **nothing added, committed or pushed** |

### Remaining work (updated)

- Live `python -m neova.provider --verify` with the real model names at publication (spends two ~1-token calls behind the budget gate), plus the §6/B5 live spend evidence from a `USAGE_LOG`-configured server run.
- `.env.example` still carries the now-unused `OPENAI_API_KEY`/`OPENAI_CHAT_MODEL` block (drop at final submission or with issue #8's README pass).

---

## Addendum 3 — third review revision (same PR, no commits)

Three review fixes applied.

### C1. HTTP-date `Retry-After` is now supported

`provider.retry_after_seconds(value, *, now=None)` accepts both forms:

- numeric seconds (unchanged, parsed exactly);
- RFC 7231 HTTP-dates (e.g. `Wed, 21 Oct 2026 07:28:00 GMT`) via `email.utils.parsedate_to_datetime`, converted to the positive delay from `now` (live clock by default; injectable `now` for deterministic tests).

Past dates, naive dates, unparsable text and empty values yield `None` → the caller uses exponential backoff; a parsed delay still goes through the `MAX_RETRY_AFTER_SECONDS` cap in `retry_delay`. Callers (default transport, classifier transport) pass header values straight through — no change needed there.

Test `test_retry_after_accepts_http_dates_and_rejects_garbage`: future HTTP-date 45 s → parsed (~45) and capped to an 8.0 s wait; past date / garbage / naive date / empty / None → no delay (backoff path); plain `"3"` still exactly `3.0`.

### C2. Usage-log directories are created

`usage.record()` creates the log's parent directories on first write (`os.makedirs(parent, exist_ok=True)`), so `USAGE_LOG=evidence/usage.jsonl` works without pre-existing folders. Test `test_usage_log_creates_missing_directories`.

### C3. JSONL append is now under the ledger lock

`record()` previously appended to the in-memory ledger under `_lock` but released it before opening/writing the file — concurrent model calls could interleave partial lines. The append (including the directory creation) now happens while holding `_lock`. Test `test_concurrent_records_never_interleave_the_log`: 8 threads × 10 records → exactly 80 ledger entries, 80 well-formed JSONL lines, all reloadable, spend sums exactly. Also removed a redundant inner import in `configure_from_env` (top-level import exists).

### Verification (this revision)

| Command | Result |
| --- | --- |
| `uv run --locked --extra dev python -m compileall -q main.py neova tests` | exit 0 |
| `uv run --locked --extra dev python -m pytest tests/test_resilience.py -q` | **44 passed** |
| `uv run --locked --extra dev python -m pytest tests -q` | **170 passed** |
| `git diff --check` / corpus byte-stability | exit 0 / exit 0 |

### Note

`.gitignore` shows one pre-existing working-tree modification (adds `.logs/`) that was not made in this session — left untouched and flagged here. Nothing added, committed or pushed.

---

## Addendum 4 — doc-consistency revision (same PR, no commits)

Two documentation fixes; no code or tests changed.

### D1. README test command repaired

- The "Tests" section listed `tests/test_models.py` in a hardcoded file list — that file was deleted in Addendum 1, so the command was broken. Replaced with the suite command `uv run --locked --extra dev python -m pytest tests -q`.
- Same pass-through removed two stale claims: the coverage list now mentions the resilience tests, and "Current limitations" no longer lists provider retries/fallback/spend logging as *not yet implemented* (they are implemented on this branch; the remaining limitation is the publication-time live verification, which needs key budget).

### D2. Report reconciled with its own addenda

The body (§1–§6) described the original 28-test implementation and contradicted the three addenda. It now describes the **final state** (addenda stay as the dated history):

- §1 files table rewritten to include the A/B/C revisions: `app.py`/`dto.py` (endpoint/DTO removal), `neova/models.py` + `tests/test_models.py` (deleted), `tools.py` (real `TOOL_TIMEOUT_SECONDS`), `gather.py` (no-embedder-spend stop), `.env.example` (`USAGE_LOG`), README/curl-guide updates, `ANSWER_MAX_TOKENS`, HTTP-date `Retry-After`, locked+dir-creating JSONL appends, `load_records`/CLI, `verify_routes`. The "Not changed" list no longer lists files that were in fact changed; test count corrected to 44.
- §2: Retry-After wording corrected (capped, HTTP-dates supported — not "treated as absent"); route verification now describes the live `--verify` probe; boundedness bullet added.
- §3: replaced the stale "28/156 passed" results with the final run (44 / 170), keeping per-revision numbers in the addenda where they belong.
- §4: matrix updated (8.0 s cap, HTTP-dates, timeouts, no-embedder-spend, concurrency, persistence, `verify_routes`, answer bound).
- §5 assumption 4: rewritten (HTTP-dates supported; the original "treated as absent" behavior recorded as fixed in Addendum 3).
- §6: the superseded "choose a JSONL path via `set_log_path()`" item replaced by the actual `USAGE_LOG` + CLI publication checklist; final-submission cleanups (`.env.example` `OPENAI_*` block, `langchain-openai` pin) listed explicitly as deferred.

### Verification (this revision)

| Command | Result |
| --- | --- |
| `uv run --locked --extra dev python -m compileall -q main.py neova tests` | exit 0 |
| `uv run --locked --extra dev python -m pytest tests -q` | **170 passed** |
| `pytest --collect-only` grep for `test_models` | no matches (deleted module unreferenced) |
| `git diff --check` / corpus byte-stability | exit 0 / exit 0 |

Nothing added, committed or pushed.
