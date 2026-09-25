# Issue #8 — delivery implementation and observed evidence

**Branch:** `8-pr-7-final-delivery-clean-run-concise-readme-and-honest-evidence`, based on `66c731f`. No file was added to the index, committed or pushed in this cycle. This document records observed behavior; #7's [evaluation report](issue-7-evaluation-security-report.md), [`eval/results.json`](../eval/results.json) and [`eval/results.md`](../eval/results.md) remain the source of the earlier measured outcomes. The #8 plan is [here](issue-8-final-delivery-plan.md).

## Changes and decision

- `neova/app.py`: after seeding, a fresh server startup builds public chunks and an FTS5 index using local corpus files, once per empty database. It does **not** construct an embedding client or make a paid indexing request. Existing indexed databases are reused, including existing cached vectors; the explicit `python -m neova.retrieval index` command still creates/rebuilds semantic vectors behind its budget gate. Public-only chunking excludes the two internal routing sources. Startup fails rather than claim readiness if no public chunks are produced.
- `tests/test_foundation.py`: a fresh temp-database app lifespan must produce a nonempty FTS5 search with no key and exclude internal sources. Restart and a second fresh startup with placeholder model/key variables and an embedder patched to fail on invocation must preserve/build the index without calling the embedder.
- `README.md`: concise startup, graph, actual offline result, model-route status, cost provenance, exactly three design/trade-off choices, known breakage and two-day plan. `.env.example`: placeholder-only guidance for keyless FTS5, opt-in embedding, budget and frozen clock. `docs/issue-8-final-delivery-plan.md` is the previously written planning file; this report is the delivery evidence. No supplied JSON/PDF/PNG or #7 artifact was edited.
- User clarified the ambiguous requirement: **public retrieval must work after the one server command**. The smallest implementation is FTS5 on startup; paid semantic vectors stay opt-in. This is a genuine search-quality trade-off, stated in the README.

## Clean-checkout smoke (2026-09-25, Windows PowerShell)

`git clone . $env:TEMP\neova-issue-8-smoke` checked out `66c731f`; because adding/committing was prohibited, the two changed implementation/test files (`neova/app.py`, `tests/test_foundation.py`) were copied onto that clean checkout before verification. Thus this was an isolated **fresh database/dependency/config smoke of the working tree**, not a test of a published fresh checkout containing the uncommitted diff. `.env` in the disposable clone was copied from placeholder `.env.example`; inherited OpenRouter/Langfuse variables were cleared. `uv sync --locked --extra dev` resolved 95 and installed 93 packages in that checkout (Python 3.12.4). No supplied assets were copied from a development database.

Launched exactly `uv run --locked --env-file .env -- python main.py` (via PowerShell `Start-Process` with those arguments). Observed:

| Probe | Observation |
| --- | --- |
| `GET /health` | `status=ok`; server startup completed. |
| `POST /demo/sessions` for `NEO-88213` | Returned a process-local token; token omitted from evidence. |
| `GET /customers/NEO-88213/summary` with session header | Returned matching customer ID; no other account fields recorded. |
| `POST /agent/chat` with `Je veux prendre rendez-vous avec un technicien` | Route `booking`, attempted `customer_summary.read`, pending booking state; no booking claim. |
| `python -m neova.retrieval search 'frais de mise en service' --mode fts` under same `.env` | Three public passages returned, `degraded=[]`; first source `faq-facturation`. No key, paid model or index CLI required. |
| `GET /slots?customer_id=NEO-88213` with live default clock | Empty (fixtures are past). |
| Same endpoint after setting `CLOCK_MODE=frozen`, `DEMO_TIMESTAMP=2026-08-26T12:00:00+02:00` in disposable `.env` and successfully restarting | One future slot for the fixture customer. |

The first frozen restart collided with the original server's child process still listening on port 8000 (`Errno 10048`); the launcher had stopped, but the child had not. After identifying/stopping the actual listener and restarting, Uvicorn reported startup complete and the frozen probe returned one slot. The listener was then stopped. The malformed-encoding glyphs in PowerShell's JSON display were local console rendering of extracted text; the source PDFs were not changed. A complete live model answer, confirmed booking via manual HTTP, and actual advisor acceptance were **not** demonstrated by this keyless smoke. The offline eval covers the booking state change instead.

## Tests, evaluation and repository checks

From the original repository root unless noted:

| Command | Actual result |
| --- | --- |
| `python --version`; `uv --version`; `uv run --locked --extra dev python -m pytest tests/test_foundation.py -q` | Python 3.12.4; uv 0.9.7; 13 passed, 1 Starlette deprecation warning (rerun after final test edit). |
| `uv run --locked --extra dev python -m pytest tests -q` | First concurrent attempt timed out at 120 seconds after 36%; rerun with 360-second limit: **200 passed**, 1 Starlette deprecation warning, 144.07 seconds. The subsequent edit only strengthened the new startup test; its focused rerun passed. |
| `uv run --locked --extra dev python -m neova.eval --out "$env:TEMP\opencode\neova-issue-8-eval"` | **12/12 offline pass, 0 failures**; routing 1/1, grounding 3/3, booking 2/2, handoff 2/2, resilience 3/3, privacy 1/1. Used `--out` to keep committed/generated `eval/results.*` untouched. Faults and model responses are injected/fake as in #7. |
| `uv run --locked --extra dev python -m json.tool "$env:TEMP\opencode\neova-issue-8-eval\results.json" > $null` and `Get-Content ...\results.md` | JSON parsed; Markdown listed all 12 cases PASS and no failed cases. |
| `uv run --locked --extra dev python -m compileall -q main.py neova tests`; `git diff --check` | Both succeeded; Git printed only Windows LF→CRLF notices. |
| `git diff --exit-code HEAD -- data/neova_data.json corpus/` plus `git hash-object` against `git rev-parse HEAD:<path>` for the tracked JSON, ten PDFs and one PNG | All matched `HEAD`; no supplied source modified. |
| `git check-ignore -v docs/issue-8-final-delivery-evidence.md` | No match, exit 1 as expected: `docs/` is **not** ignored. Existing `docs/`, `eval/` and `.env.example` are tracked; this new report and the plan remain untracked because the user prohibited `git add`. |
| `git log --oneline --decorate --merges -15`; `git diff --stat origin/main...HEAD` | Merge history shows PRs #9–#14 (issues #2–#7); branch HEAD is still equal to `origin/main`, so the latter range is empty until a later commit. The uncommitted worktree diff must be reviewed separately. |
| `(Get-Content README.md \| Measure-Object -Word -Line)` | 822 words, 32 physical lines after adding the high-level Mermaid diagram. No Markdown-to-PDF renderer was installed/used, so **two rendered pages were not independently confirmed**; the README was kept short to target that constraint. |

Tracked-file path-only scan using `git grep -I -l -E 'sk-or-v1-|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|OPENROUTER_API_KEY=[^[:space:]]+' -- ':!uv.lock'` returned only `docs/manual-e2e-curl.md`, whose key assignment is the literal `<your-key>` placeholder. `git log --all --format='%h %s' -G <same-pattern> -- .` flagged commits `0930e11`, `ad87302`, `19c3f70`, `666a2c5`; a redacted added-line inspection showed three key-shaped examples in code/tests and the same placeholder key assignment, not a visible credential. These pattern scans are not a guarantee against every possible credential format; no credential was reproduced in logs or this report. `.env.example` contains only empty key/model values. No `git add`, commit or push was performed.

## Model and spend provenance / remaining work

- #7's offline result does not measure live French factual quality or general prompt-injection resistance; offline injected 429/529 and 500 cases are not upstream incidents. The earlier successful chat trace used `mistralai/mistral-small-3.2-24b-instruct`; the strict classifier `openai/gpt-4.1-nano` returned 404, `qwen/qwen3-embedding-8b` was configured but not validated on live indexed material, and no fallback route was configured or verified. See [tracing audit](langfuse-tracing-evidence.md).
- This cycle incurred **no model calls** in its keyless smoke or offline eval. #7 reported $0.000159 known from an earlier partial chat ledger plus four classifier calls of unknown cost; a historical supplied-key status was $14.982852627 used / $15 cap (the brief described a $10 cap). These overlapping snapshots cannot be summed or separated into assistant versus app spend. Combined take-home cost is unknown beyond that historical snapshot. No new `/key` read, live indexing, provider-route probe, or live eval was attempted with the previously reported $0.017 remaining; all remain unverified pending adequate supplied-key budget.
- Follow-up: confirm README's rendered page count with an available renderer; run a live, budget-gated indexed French evaluation and verify a working classifier and distinct upstream fallback; replace local demo sessions and handoff records with real identity and advisor queue integrations if product work continues. The #8 documentation and code are uncommitted by request; new `docs/` evidence will need to be included when an authorized commit is eventually made.
