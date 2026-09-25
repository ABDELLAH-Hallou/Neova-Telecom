# Issue #7 / PR 6 — evaluation/security plan (approval required)

**Status:** plan only; no implementation has started. Target branch: `7-pr-6-evaluationsecurity-fixed-cases-privacy-checks-and-actual-results` (current). GitHub issue [#7 — PR 6: Evaluation/security: fixed cases, privacy checks and actual results](https://github.com/ABDELLAH-Hallou/Neova-Telecom/issues/7). Depends on #3 (API), #4 (retrieval), #5 (graph), #6 (resilience) — all merged; full offline suite **170 passed** on this branch. No test or eval outcome is claimed here.

## 1. Current relevant behavior

**What already exists (consumed, not rebuilt):**

- **End-to-end graph** (`neova/graph.py` + `neova/nodes/`): deterministic injection guard before any model call, one cheap classifier call with degradation to the keyword router, named routes (`gather`/`booking`/`handoff`/`unsupported`/`clarify`/`injection`), bounded step count, evidence check, French answer with citations, handoff with minimal fields. `tests/test_conversation.py` already asserts routes, tools, booking-state transitions, handoffs and privacy behavior with a `FakeChatModel` + `FakeEmbedder` on a temp DB with the frozen clock (`2026-08-26T12:00:00+02:00`).
- **Privacy/grounding controls in code:** public-only indexing (only `access="public"` chunks reach `chunks`/FTS5/vectors — 9 public, 2 internal routing-only); retrieved passages returned as data, never instructions (`SearchOutcome.data_only`); deterministic pre-classifier injection heuristic (`injection_guard_node`) plus the classifier `prompt_injection` flag; deterministic booking gate (explicit user "oui" to the exact slot+reason, one-time expiring codes); booking failures never claim success (issue #6 by-key recovery); handoff minimization (`handoff_summary`, no private fields, no stored record without a session); session isolation (`neova/session.py`, random tokens, tested in `test_foundation.py`); redacted usage ledger with `unknown` ≠ 0 (issue #6, `tests/test_resilience.py` asserts redaction).
- **Retrieval modes** (`neova/retrieval.py::search`): `mode="semantic" | "fts" | "hybrid"` already supported with per-passage `score_semantic`/`score_fts` — the comparison the issue asks for is runnable today, but nothing runs it or records it.
- **Spend evidence:** `neova/usage.py` JSONL ledger + `python -m neova.usage` summary; `python -m neova.provider --verify` for the live provider-route probe (budget-gated).

**What is missing (the actual gaps):**

- **No fixed evaluation set at all.** No committed cases file, no rubric, no runner, no measured pass/total anywhere. README still lists "Fixed evaluation set with measured results" under *Current limitations*. The ~20 conversation tests are trace/unit tests, not the declared-outcome eval the issue requires.
- **No machine-checkable case outcomes / human-readable failure output.** No harness emits per-case expected-vs-observed.
- **No retrieval-mode comparison** (semantic vs FTS5 vs hybrid on shared questions, including the scan-source and exact-term cases) recorded anywhere.
- **Security checks are scattered and partial.** Existing tests cover many controls implicitly (internal sources excluded, injection routes, no-booking-without-code, redaction), but there is no consolidated audit/regression suite covering the issue's exact list — notably: **cross-session private read attempts through `/agent/chat`**, **minimum customer fields** surfaced to the model/reply, **retrieved-content instruction resistance** (a poisoned chunk must not be obeyed), and **key/log redaction in eval artifacts**. No gap-fix pass has been done against this list.
- **No report** distinguishing observed results from design expectations, with failed cases + error analysis, commands, fixture/clock/model settings and observed spend.

## 2. Files that should change

| Path | Minimum intended change |
| --- | --- |
| `eval/cases.json` (new) | The fixed set: ~12 French cases, each with `id`, `category`, `customer_id`/session scope, messages, declared **expected safe outcome** (route, tools called, citations present/absent, booking state, reply must/must-not contain), fixture clock, and fault injection descriptor (`api_500`, `openrouter_429`, `openrouter_529`, `none`). Combine related assertions per case to keep the set small (e.g. booking-bad-slot and booking-success as one category pair; contradiction and unanswerable as grounding cases). |
| `eval/rubric.md` (new) | Scoring criteria per category (routing, grounding/citations, booking-state, privacy, resilience), what counts as a pass, and how failures are analyzed. |
| `neova/eval.py` (new) | Deterministic runner: builds a temp DB + sessions, wires the same offline fakes the tests use (fake chat/embedder/transport), executes each case through `run_conversation` / the FastAPI `TestClient`, applies the rubric assertions, and emits **two artifacts**: machine-checkable `eval/results.json` (per-case pass/fail + observed fields) and human-readable `eval/results.md` (failures stay visible with one-line error analysis). Optional `--live` mode reuses real models behind the budget gate with `USAGE_LOG` wired; identical rubric. Committed at repo root — `scripts/` and `output/` are gitignored, so neither may host committed eval artifacts. |
| `tests/test_security.py` (new) | Consolidated privacy/grounding regression suite for the issue's audit list: cross-session/cross-customer read attempts (typed ID without token, other token's booking by-key, agent with session A cannot surface customer B values), minimum customer fields only, internal-document exclusion from embeddings/search/model context/replies (both internal source IDs), retrieved-content instruction resistance (poisoned passage text cannot change route/booking/reply), handoff payload minimization, no unconfirmed state-change claim (500 then absent by-key), key/session/customer redaction in the usage ledger and logs. |
| `neova/nodes/*`, `neova/retrieval.py`, `neova/conversation.py` (only if the audit finds a gap) | Narrow fixes strictly limited to found gaps, each recorded with its failing case in the report. Expected to be zero-to-few edits; no architectural change. |
| `docs/issue-7-evaluation-security-plan.md` (this file) and later `docs/issue-7-evaluation-security-report.md` | Plan now; report with actual numbers, category breakdown, failed-case analysis, commands, fixture/clock/model settings, retrieval comparison table, privacy/injection evidence, fault traces and approximate combined spend after implementation. |
| `tests/test_eval.py` (new, small) | Runner contract tests: rubric parsing, per-case assertion application, `results.json` schema, failing cases stay visible — all offline with fakes. |

**Commit boundaries** (per the issue): ① evaluation fixtures/rubric commit (steps 1); ② security regression tests and narrow fixes commit (steps 2–3); ③ observed results/error-analysis commit (steps 4–6). No broad architectural rewrite, no forced commit count.

## 3. Files that should not change

- `corpus/*`, `data/neova_data.json`: read-only inputs; byte-stability must keep holding (`git diff --exit-code HEAD -- corpus/ data/neova_data.json`).
- `neova/db.py`, `neova/session.py`, `neova/clock.py`, `neova/customer_api.py`, `neova/dto.py`, `neova/app.py`, `main.py`: finished contracts from #2/#3/#5; the eval consumes them through public seams (`run_conversation`, `TestClient`, fakes) and must not change semantics.
- `neova/provider.py`, `neova/usage.py`, `neova/embeddings.py`, `neova/classifier.py`, `neova/tools.py`: issue-#6 resilience/spend surface is complete and tested; the eval **records** its outputs, it does not alter policy.
- `neova/graph.py`, `neova/nodes/*`, `neova/conversation.py`, `neova/retrieval.py`: untouched **unless** a security/eval case exposes a real gap; any touch is then a narrow, report-recorded fix (see §2).
- `neova/prompt/*`, `neova/chunking.py`, `neova/pdf_extractors.py`, `neova/sources.py`, `neova/extraction.py`: no surface change.
- `README.md`, `.env.example`: final-delivery rewrite (run command, graph sketch, eval numbers, design decisions) is **issue #8**, not this PR. `.env.example` already declares `USAGE_LOG` — nothing new needed.
- `pyproject.toml`, `uv.lock`: no new dependency — the runner uses stdlib + existing test fakes + `httpx`/FastAPI `TestClient`.
- `tests/test_conversation.py`, `test_resilience.py`, `test_retrieval.py`, `test_foundation.py`, `test_customer_api.py`, `test_dto.py`, `test_classifier.py`, `test_pdf_extractors.py`: stay green and untouched (the eval reuses their fakes by import, exactly like the existing `scripts/capture_issue5_traces.py` pattern — without adding anything to `scripts/`, which is gitignored).
- `docs/issue-2-*` … `issue-6-*`, `docs/manual-*.md`, `take_home_relation_client.md`: out of boundary.
- `.env`, `*.db`, `output/`, `scripts/`: never committed; eval artifacts are generated under `eval/` with only sanitized, redacted results committed.

## 4. Smallest implementation sequence (maximum 7 steps)

1. **Fixtures + rubric (commit ①):** write `eval/cases.json` (~12 cases covering: internet incident, bill question, moving + booking success, booking bad slot, termination + handoff, current-vs-archive price, fee-timing contradiction, invoice without line items → safe "no answer" handling, immediate sensitive handoff, API-500 read recovery, OpenRouter-429 and 529 degradation, one anonymous session case; each with route/tool/citation/booking-state assertions and the frozen clock) and `eval/rubric.md` with per-category pass criteria.
2. **Security regression suite (commit ②):** add `tests/test_security.py` implementing the audit list as negative tests (cross-session read, internal-doc exclusion across embeddings/search/context/replies, retrieved-content injection resistance, handoff minimization, no-unconfirmed-claim, ledger/log redaction, minimum customer fields).
3. **Narrow gap fixes (commit ②):** fix only what step 2 actually fails, keeping edits inside the owning node/module; every fix gets its failing test recorded. If nothing fails, state that explicitly in the report — no invented work.
4. **Eval runner (commit ②/③ boundary):** implement `neova/eval.py` (offline fakes by default; `--live` opt-in with real models behind the budget gate) + `tests/test_eval.py`; the runner emits `eval/results.json` and `eval/results.md` with failing cases visible.
5. **Retrieval comparison (commit ③):** run `retrieval.search` in `semantic`, `fts` and `hybrid` modes on the shared eval questions (including the scan-source fiche-roaming case and an exact-term case); record top-three agreement/differences per mode into the report — this is measurement, not a retrieval change.
6. **Observed run + report (commit ③):** execute the full offline eval; if remaining key budget allows, one small budget-gated live run with `USAGE_LOG` set and provider-route verification; capture spend (`python -m neova.usage <log>`, plus the `/key` curl for the user-run figure). Write `docs/issue-7-evaluation-security-report.md`: actual pass/total, category breakdown, each failed case with a one-line error analysis, commands, fixture/clock/model settings, observed-vs-design-expectation distinction, known breakage for issue #8. Commit only sanitized results (no customer records, no credentials).
7. **Suite green + hygiene:** full offline suite re-run, whitespace check, input byte-stability check, `git status` clean of secrets/artifacts.

## 5. Risks and unresolved assumptions

- **Offline ≠ model-backed.** With `FakeChatModel`, the offline eval measures routing, gates, tool use, booking state and reply *shape* — not French prose quality or real-model grounding. Only the optional live run measures the actual model. The report must label which numbers come from which mode; projections must never replace real run numbers. Assumption: a small live run is acceptable if the key budget remains; if not, the report records spend as unknown-beyond-ledger with the JSONL evidence.
- **429/529 cases cannot be reproduced live on demand.** The deterministic evidence is fault-injected fakes (issue-#6 pattern); a live 429 may occur incidentally and is recorded if observed. This satisfies "deterministic fault injection" in the scope; the report must say so plainly.
- **Expected outcomes must be declared before running, not fitted after.** Risk of writing the rubric to match current behavior and hiding real gaps. Mitigation: step 1 fixes `cases.json`/`rubric.md` in their own commit before any runner work; deviations found later are reported as failures or as narrow fixes with their failing cases — never silently re-scoped.
- **Corpus contradiction identification still rests on the earlier PDF review** (`requirements_analysis.md` R7): the fee-timing conflict (archive `politique-geste-commercial` vs `grille-tarifaire-2026`) and the invoice-without-line-items gap must map to specific passages; if the expected safe outcome conflicts with actual graph behavior, that is a finding, not a rubric edit.
- **`scripts/` and `output/` are gitignored.** The committed eval must therefore live in a new unignored path (`eval/` + `neova/eval.py`); reusing fakes from `tests/` by import (existing pattern) keeps that dependency explicit but creates a `neova → tests` import direction risk — kept safe by defining runner-side fakes in the runner itself or importing fakes only at run time, never at import time, so production imports stay clean.
- **Cross-session privacy surface is demo-grade.** Session tokens are process-local random strings (documented as not real authentication). The audit proves the *implemented* boundary (token-scoped reads, by-key customer scoping); it does not claim production security, and the report must keep that distinction (no "penetration test" claim — non-goal).
- **Retrieved-content injection resistance is heuristic.** The deterministic guard + "passages are data" prompt contract + evidence gate bound the risk; a case asserting "poisoned passage never books / never leaks internal text" is provable, but semantic-level resistance of the live model is not fully measurable offline. The report states this limit rather than claiming immunity.
- **Spend attribution across the whole take-home.** `usage.jsonl` covers only calls made with `USAGE_LOG` configured; earlier coding-assistant spend is only visible via the `/key` curl. The report distinguishes measured solution spend from total key usage and marks unknowns as unknown (never 0).
- **Case-count pressure.** ~12 cases with combined assertions is the committed target; adding cases late dilutes focus. Changes to `cases.json` after step 1 need a recorded reason in the report.

## 6. Exact verification commands (after implementation)

From the repository root in PowerShell, with `uv` installed. Steps 1–6 are planned, not yet run; commands marked *live* are user-invoked only.

```powershell
uv sync --locked --extra dev
uv run --locked --extra dev python -m compileall -q main.py neova eval tests
uv run --locked --extra dev python -m pytest tests/test_security.py -q
uv run --locked --extra dev python -m pytest tests/test_eval.py -q
uv run --locked --extra dev python -m pytest tests -q

# Deterministic offline evaluation run (fixtures + frozen clock, no network)
uv run --locked --extra dev python -m neova.eval

# Inspect the machine-checkable outcome and the human-readable failures
uv run --locked --extra dev python -m json.tool eval/results.json
Get-Content eval/results.md

git diff --check
git diff --exit-code HEAD -- data/neova_data.json corpus/
git status --short
```

Optional live evidence (commit ③, only after the offline suite is green, budget permitting — user-invoked):

```powershell
# Key spend / remaining cap (explicit, user-invoked — never printed from code)
curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $env:OPENROUTER_API_KEY" | jq '.data | {usage, limit, limit_remaining}'

# Live eval pass behind the budget gate (writes to the configured USAGE_LOG)
uv run --locked --env-file .env -- python -m neova.eval --live

# Redacted spend summary from the persisted per-call log
uv run --locked --env-file .env -- python -m neova.usage
uv run --locked --env-file .env -- python -m neova.provider --verify
```