# Issue #7 — evaluation/security implementation and observed results

**Branch:** `7-pr-6-evaluationsecurity-fixed-cases-privacy-checks-and-actual-results`. **No files staged, committed or pushed.** Results below are observed from the completed offline run; they are not a prediction of live-model quality.

## Files changed in this cycle

- `eval/cases.json`: 12 fixed French cases with declared outcomes, frozen clock and fault descriptors.
- `eval/rubric.md`: check semantics and the distinction between offline shape tests and live model quality.
- `neova/eval.py`: isolated per-case temp databases, actual public PDF/scan corpus indexing, FastAPI/graph execution, semantic/FTS5/hybrid comparison, bounded fault injection, sanitized JSON/Markdown outcomes, optional budget-gated `--live` mode.
- `tests/test_eval.py`: runner schema, reproducibility, fault-attempt count, failed-case visibility and artifact redaction checks.
- `tests/test_security.py`: negative session/privacy, internal-source exclusion (including real indexing), poisoned retrieved passage, booking uncertainty, handoff minimization and usage-ledger checks.
- `neova/nodes/gather.py`: narrow trace-accuracy fix: mark customer-summary and incident reads as **attempted** before calling them, including after a failed/exhausted read. Previously failed reads were absent from `tools_called` despite having been attempted.
- `eval/results.json`, `eval/results.md`: sanitized outputs created by the requested run; no customer records, credentials, tokens or raw replies.
- This report (`docs/issue-7-evaluation-security-report.md`). The earlier Langfuse tracing work and the approved plan were already present; they were not changed in this cycle.

## What was measured

Command: `uv run --locked --extra dev python -m neova.eval`. Frozen clock: `2026-08-26T12:00:00+02:00` (Europe/Paris). Each case starts from a fresh SQLite copy of the **real** public corpus index; embeddings use the deterministic `FakeEmbedder` bag-of-words space; classification uses the keyword fallback; answer prose comes from `FakeChatModel`. Both API reads and writes use the actual in-process FastAPI service. This measures routing, attempts, citations, gates, state and safe reply shapes; it does **not** measure real-model French correctness or prompt-injection immunity.

**Actual offline result: 12/12 cases passed, 0 failed.** Breakdown: routing 1/1, grounding 3/3, booking 2/2, handoff 2/2, resilience 3/3, privacy 1/1. Failed-case list for this run: **none**. Case-by-case checks and observed route/tool/citation/state data are in `eval/results.json`; `eval/results.md` shows the run summary and retrieval comparison. A failing case remains in both outputs with its failed checks and a one-line error analysis (verified by `tests/test_eval.py`).

The API-500 read case records `customer_summary.read` as attempted, then `api_read_failed`, `search_skipped_read_failure`, a short French handoff offer, no citation and no saved booking. The implementation still tries an incident read after that failed summary; the evaluation records this as an extra attempted read rather than hiding it. OpenRouter 429 and 529 are **deterministically injected**, not observed live: each produces exactly three failed primary chat attempts, a `chat_model_unavailable` marker and the French terminal handoff-offer reply. No fallback model is configured in the offline run. The booking-success case verifies a saved appointment exists in SQLite after the exact confirmation code; the bad-slot case verifies no booking.

### Retrieval comparison (same four questions; top three public passages)

| Question | Semantic | FTS5 | Hybrid | Agreement with hybrid (semantic / FTS5) |
| --- | --- | --- | --- | --- |
| Current price vs archived 2024 | `promo-rentree-2024`, `grille-tarifaire-2026`, `fiche-roaming-international-scan` | same | same | 3/3 · 3/3 |
| Rejection-fee timing | `faq-facturation`, `faq-espace-client`, `cgv-resiliation` | `faq-facturation`, `faq-espace-client`, `grille-tarifaire-2026` | same as FTS5 | 2/3 · 3/3 |
| Roaming scan | **no semantic hits** | `fiche-roaming-international-scan`, `faq-retour-equipement`, `grille-tarifaire-2026` | same as FTS5 | 0/3 · 3/3 |
| Exact term “frais de mise en service” | `cgv-resiliation`, `procedure-demenagement`, `faq-facturation` | `faq-facturation`, `faq-facturation`, `faq-espace-client` | `faq-facturation`, `cgv-resiliation`, `procedure-demenagement` | 3/3 · 1/3 (unique source IDs) |

The scan miss is an **observed limitation of the fake semantic embedding**, not evidence of the live embedding model's behavior; FTS5/hybrid find it. FTS5 can return distinct chunks from the same source, so a repeated source ID represents two passages, not duplicate result rows. No retrieval algorithm was changed.

### Security/grounding audit

`tests/test_security.py`: 12 passing checks. Untokened reads return 401; the other customer's summary/slots and booking-by-key are inaccessible across session boundaries; account summary uses only five DTO fields; two internal routing-only sources remain excluded from the actual PDF index and from all three retrieval modes even when internal rows are manually inserted; a poisoned **retrieved public passage actually reaches the prompt as data**, yet the deterministic route/booking state and fake reply do not follow its instructions; handoff records are bounded/minimal; a booking 500 with absent by-key check cannot claim success; JSONL usage records reject customer/key fields. This demonstrates the implemented demo-session boundary, not production authentication or general semantic prompt-injection resistance.

**Narrow fix found by the audit/evaluation:** a failed customer-summary read was missing from `tools_called`; `gather.py` now records read attempts before the call. The original harness also initially scored only the final turn's tool list, allowed fake models to bypass 429/529, faulted the wrong API endpoint, and used canned instead of actual corpus chunks. Those were defects of newly introduced evaluation code and were corrected before the final measured run; the intermediate 7/12 and 11/12 scores are **not** presented as application scores. The full suite initially exposed a shared scripted transport left by earlier resilience tests; the new security fixture now owns its transport, without modifying those tests.

## Spend, model settings, and live status

- Offline eval: **no paid model or embedding requests**. Its failed 429/529 attempts are injected, not billed; offline solution spend = $0 known for this run.
- Existing redacted Langfuse-audit usage log, read via `uv run --locked --extra dev python -m neova.usage "$env:TEMP\opencode\lf-audit-usage.jsonl"`: chat **$0.000159 known** (3 calls), classifier **4 calls with unknown cost**, embeddings **no entries in that log**. This is *earlier tracing evidence*, not spend incurred by this offline eval. True combined take-home spend cannot be inferred from this partial log.
- Current supplied key status read using `neova.embeddings.key_status()`: **$14.982852627 used / $15 cap, $0.017147373 remaining** at the time of the check. This is total key usage across tools/assistant/app; the attribution outside the ledger is unknown. No credentials are included in this report.
- Configured live models (names only): classifier `openai/gpt-4.1-nano`, chat `mistralai/mistral-small-3.2-24b-instruct`, embedding `qwen/qwen3-embedding-8b`; fallback **unset**. The live tracing audit already identified classifier 404 under `provider.require_parameters=true` and an empty live index; see `docs/langfuse-tracing-evidence.md`.
- **Live eval and `python -m neova.provider --verify` were not run**: the key has $0.017 left, below the runner's $1.00 preflight threshold and the embedding budget gate. Thus there is **no live-model score, live fallback verification or live embedding comparison**. The optional plan commands are contingent on budget and remain for a replenished key.

## Exact verification commands and real results

From the repository root in PowerShell:

| Command | Result |
| --- | --- |
| `uv sync --locked --extra dev` | Succeeded; resolved 95, audited 93 packages. |
| `uv run --locked --extra dev python -m compileall -q main.py neova eval tests` | Succeeded, no output. |
| `uv run --locked --extra dev python -m pytest tests/test_security.py -q` | 12 passed, 1 Starlette deprecation warning. |
| `uv run --locked --extra dev python -m pytest tests/test_eval.py -q` | 8 passed, 1 Starlette deprecation warning (including an explicit synthetic failed-case rendering check). |
| `uv run --locked --extra dev python -m pytest tests -q` | **199 passed, 1 warning**. Initial full run had 5 security-fixture failures from inherited scripted transport; fixed and rerun green. |
| `uv run --locked --extra dev python -m neova.eval` | **12/12 offline pass**, breakdown above. |
| `uv run --locked --extra dev python -m json.tool eval/results.json` | Parsed successfully; 12 machine-checkable cases, 0 failures. |
| `Get-Content eval/results.md` | Shows the 12-case table and four retrieval comparisons; no failed cases in the final run. |
| `git diff --check` | Succeeded (line-ending conversion notice on `gather.py`). |
| `git diff --exit-code HEAD -- data/neova_data.json corpus/` | Succeeded, source fixture and corpus unchanged. |
| `git status --short` | `M neova/nodes/gather.py`; new `eval/`, `neova/eval.py`, `tests/test_eval.py`, `tests/test_security.py` (this report is also new). No staging, commit or push. |

## Assumptions and remaining work

- The 12 declared cases test safe outcomes, not detailed semantic truth; the offline canned answer is deliberately **not** scored for factual correctness beyond the deterministic reserve notices. A 12/12 offline score is not a perfect-accuracy claim.
- One `route` assertion applies to the **first** turn of a multi-turn case (later booking continuation routes are recorded); tool checks include **all turns**. Appointment state is read directly from SQLite; raw replies are checked in memory and discarded, not emitted to artifacts.
- No live score can be claimed until budget is replenished; at that point run `uv run --locked --env-file .env -- python -m neova.eval --live --out eval/live` to retain the offline artifacts, then summarize its redacted `USAGE_LOG`. Resolve the known classifier routing 404 and configure/verify a distinct fallback before relying on live results.
- Production authentication and queue integration remain outside this demo. README synthesis belongs to issue #8. No files were staged, committed or pushed.
