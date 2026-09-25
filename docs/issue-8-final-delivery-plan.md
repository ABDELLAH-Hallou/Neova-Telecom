# Issue #8 / PR 7 — final delivery plan (approval required)

**Status:** plan only; implementation and clean-checkout verification have not been run for #8. Branch `8-pr-7-final-delivery-clean-run-concise-readme-and-honest-evidence` starts clean at `66c731f` (the #7 merge). Scope: [issue #8](https://github.com/ABDELLAH-Hallou/Neova-Telecom/issues/8) and `take_home_relation_client.md`. This file is the only change made during planning.

## 1. Current relevant behavior

- `uv run --locked --env-file .env -- python main.py` starts one Uvicorn/FastAPI process (`main.py`, `neova/app.py`) hosting `/health`, the local fixture-backed customer endpoints, `/demo/sessions`, and `/agent/chat` (bounded LangGraph in `neova/graph.py`). Startup seeds SQLite from `data/neova_data.json`; session tokens select fixture customers, not authenticated users. Booking requires an exact one-time confirmation code and an available future slot; handoffs are local records, not an advisor queue. Unauthenticated chat can only use public material.
- `.env.example` is already tracked and contains empty key/model fields and local defaults. The README currently instructs copying it to `.env`; its 229 lines include stale claims that the evaluation set and tracing are not implemented, and its prose says a bare “oui” confirms a booking although the later code-phrase rule says otherwise. It does not present #7's measured results, three concise trade-offs, combined spend provenance, or a two-day plan.
- Corpus indexing is **not** part of API startup: `python -m neova.retrieval index` explicitly creates public chunks/FTS5/vectors. A fresh database started with the one server command can answer health and perform fixture API/booking flows, but a corpus-grounded answer will have `index_empty` until indexing is performed. The older manual e2e guide prescribes indexing as a separate command. This conflicts with a reading of “one command” as a fully ready retrieval demo; verify and resolve or document the prerequisite honestly rather than implying indexed retrieval at startup.
- #7 recorded **12/12 offline cases passed, 0 failures** (routing 1/1, grounding 3/3, booking 2/2, handoff 2/2, resilience 3/3, privacy 1/1), with `FakeChatModel`, `FakeEmbedder`, keyword classifier, frozen clock and injected 500/429/529 faults (`eval/results.json`, `eval/results.md`, `docs/issue-7-evaluation-security-report.md`). No live-model evaluation score exists. The report recorded earlier chat usage of $0.000159 known with four classifier calls of unknown cost, and a historical total supplied-key reading of $14.982852627 / $15 cap with attribution unknown; those are different scopes, not additive amounts. The stated brief cap is $10; avoid presenting the historical key reading as the brief's promised budget or fresh spend.
- Live tracing evidence observed a successful `mistralai/mistral-small-3.2-24b-instruct` answer, but `openai/gpt-4.1-nano` classifier returned 404 with `provider.require_parameters=true`; configured `qwen/qwen3-embedding-8b` was not verified on the empty live index and the fallback was unset/unverified. Model names must be labeled by observed verification status, never advertised as all verified French-capable. The `README` currently calls the fallback verified without supporting evidence.
- `.gitignore` ignores `.env` and `.env.*` except `.env.example`, plus `scripts/`, `output/`, and `*.db`; it **does not currently ignore `docs/`**. Existing `docs/` evidence, eval artifacts, JSON, PDFs, PNG and `.env.example` are tracked. The current worktree has no edits. Git history contains the #2–#7 PR merges and smaller intervening commits; proposal documents and cached PDF extracts are not execution evidence.

## 2. Files that should change (after approval)

| Path | Smallest intended change |
| --- | --- |
| `README.md` | Replace stale long walkthrough with at most two rendered pages: prerequisites and exact single server command, separate indexing prerequisite or verified startup solution, graph sketch, truthful model names/config/status, #7 totals/failures and offline-vs-live limits, spend with known/unknown attribution, exactly three decisions/trade-offs, limitations, two-day plan and links to longer evidence. |
| `.env.example` | Keep placeholders only; clarify required model settings, clock/demo choice, optional usage logging and the unverified fallback/classifier configuration. No actual credential or guessed verified model. |
| `docs/issue-8-final-delivery-evidence.md` (new) | Sanitize and record actual fresh-checkout smoke transcript, index state, eval/test results and failures, data-integrity/history/secret scan, budget limitations, commit/PR progression and supported demo claims. Link #7's real artifacts rather than duplicating them. |
| `docs/issue-8-final-delivery-plan.md` | This approval-stage plan; update status/evidence links only if useful at delivery. |
| `neova/app.py` or a narrow startup/indexing owner, with a targeted regression test **only if necessary** | If the acceptance interpretation requires retrieval ready after the one server command, implement and verify the smallest bounded, budget-aware startup indexing path. Do not trigger paid calls silently on keyless startup or claim this is already working. Decide based on fresh-checkout evidence before editing code. |

No `.gitignore` change is currently indicated; explicitly verify that the new `docs/` file is staged/tracked at delivery. A verification-only fix commit is conditional on a discovered defect.

## 3. Files that should not change

- `take_home_relation_client.md`, `data/neova_data.json`, `corpus/*.pdf`, `corpus/*.png`: supplied brief and source assets are read-only; compare hashes to `HEAD` before and after.
- `eval/cases.json`, `eval/rubric.md`, `eval/results.json`, `eval/results.md`, `neova/eval.py`: preserve #7's declared cases and real run artifacts; only regenerate the outputs when explicitly re-running the same offline command and record any changed outcome.
- `docs/issue-2-*` through `docs/issue-7-*`, `docs/system_design.md`, `docs/material_inventory.md`, `docs/requirements_analysis.md`, `output/`: historical records or proposals, not replacements for new smoke evidence.
- `neova/graph.py`, `neova/nodes/`, `neova/customer_api.py`, `neova/db.py`, `neova/provider.py`, `neova/retrieval.py`, `neova/usage.py`, `neova/prompt/`, `pyproject.toml`, `uv.lock`, existing tests: outside delivery scope unless a *specific* fresh-run failure requires a minimal documented fix. No new feature work.
- `.env`, `*.db`, usage JSONL, tokens, local extracts and logs: local-only; never add to git or paste unredacted into evidence.

## 4. Smallest implementation sequence (maximum seven steps)

1. From an isolated clean checkout, validate Python/uv prerequisites, copy placeholder config, start the exact `uv run --locked --env-file .env -- python main.py` command, exercise health, demo session, authorized API and a keyless deterministic graph turn; capture sanitized observed results and the fresh retrieval state.
2. Resolve the fresh-index gap as narrowly as the acceptance criterion demands: either demonstrate/document an explicit, budget-gated indexing prerequisite while retaining one server command, or add a bounded startup initialization with a focused test; do not make paid calls just to improve the transcript. Verify frozen fixture booking versus live-date refusal.
3. Re-run offline eval and full tests; link actual #7 outcomes and record any new failures. Only attempt live indexing, classifier/fallback verification or live eval after an explicit remaining-budget check; mark unrun routes unverified.
4. Condense `README.md` to the two-page target and update `.env.example` to placeholders and validated settings; use exactly three decisions/trade-offs and explicitly distinguish measured offline behavior, live observations, limitations and two-day follow-up.
5. Write sanitized `docs/issue-8-final-delivery-evidence.md` with smoke, evaluation, model/spend provenance, limitations and #2–#7 PR/commit mapping; check `.gitignore` behavior, secret/history scan, tracked asset hashes and README size/content.
6. Review final diff and history, run the exact checks below, record blockers honestly and commit one focused delivery/docs/config change; use a separate verification-fix commit only if the smoke reveals a real defect. Stop if findings invalidate README claims.

## 5. Risks and unresolved assumptions

- **One command vs complete retrieval:** startup seeds customer data but does not index the corpus. Does issue #8 require the command alone to produce a grounded answer on a fresh checkout, or is documented pre-indexing a permitted prerequisite? Resolve through the clean smoke before selecting a code change.
- **No confirmed live model stack:** classifier route currently 404s, fallback absent, live index was empty and the recorded remaining key balance was $0.017147373 at the prior check. A README cannot call those routes verified or claim a live score; a new key/budget or user decision may be needed for live proof. No spend is authorized by this plan.
- **Cost accounting:** earlier partial per-call ledger and total key reading cover overlapping but different scopes, including assistant use and unknown-cost calls. Do not add them, equate unknown with zero, or claim $0.000159 is the combined take-home spend. Fresh `/key` access and a representative ledger might be unavailable; label the limit.
- **Clean checkout and platform:** README uses Unix `cp`/`curl` examples; verification here is PowerShell on Windows. Document prerequisites and a portable copy command or platform-specific alternative. A clean `git worktree` shares local git history and does not by itself simulate a machine without environment credentials or cached uv packages.
- **Two-page constraint:** Markdown page count depends on renderer and paper/layout; target a concise ~800–900 words plus a rendered PDF/page review where tooling permits. Keep detailed API commands in existing docs and long output in sanitized evidence.
- **Secret/history integrity:** no secret/history scan has been performed in this planning pass; historical credentials could require separate remediation. Scan without echoing matching values. The current `docs/` ignore concern from the issue is not reproduced by `.gitignore`; recheck both ignore behavior and staged inclusion after adding evidence.

## 6. Exact verification commands (planned; not run in this planning pass)

Run from repository root in **PowerShell** after approval. Use a fresh temporary checkout and a disposable `.env`/SQLite path for the smoke; do not copy a populated local `.env` into evidence. Commands marked live are gated by available key/budget and should be skipped with an explicit note if blocked.

```powershell
# Prerequisites / source checkout (replace the URL if checking a local clone)
python --version
uv --version
$smoke = Join-Path $env:TEMP 'neova-issue-8-smoke'
git clone . $smoke
Set-Location $smoke
Copy-Item .env.example .env
# Keep key/model placeholders empty. Clear inherited credentials in BOTH terminals.
Remove-Item Env:OPENROUTER_API_KEY,Env:LANGFUSE_PUBLIC_KEY,Env:LANGFUSE_SECRET_KEY -ErrorAction SilentlyContinue
# To exercise fixture booking later, set CLOCK_MODE=frozen and
# DEMO_TIMESTAMP=2026-08-26T12:00:00+02:00 in the disposable .env.
uv sync --locked --extra dev

# Terminal A: single server command; keep it running while using Terminal B
uv run --locked --env-file .env -- python main.py

# Terminal B, from the same smoke checkout: do not print the token
$repo = (git remote get-url origin).Trim() # local source path from `git clone .`
Remove-Item Env:OPENROUTER_API_KEY,Env:LANGFUSE_PUBLIC_KEY,Env:LANGFUSE_SECRET_KEY -ErrorAction SilentlyContinue
(Invoke-RestMethod http://127.0.0.1:8000/health).status
$s = Invoke-RestMethod -Method Post http://127.0.0.1:8000/demo/sessions -ContentType 'application/json' -Body '{"customer_id":"NEO-88213"}'
$h = @{ 'X-Demo-Session' = $s.session_token }
(Invoke-RestMethod http://127.0.0.1:8000/customers/NEO-88213/summary -Headers $h).customer_id
$r = Invoke-RestMethod -Method Post http://127.0.0.1:8000/agent/chat -Headers $h -ContentType 'application/json' -Body '{"message":"Je veux prendre rendez-vous avec un technicien"}'
$r | Select-Object route,tools_called,pending_booking,degraded
# Check retrieval index state without a paid call (no API key in .env):
uv run --locked --env-file .env -- python -m neova.retrieval search 'frais de mise en service' --mode fts
# Stop Terminal A with Ctrl+C; never record raw customer data or session token.

# Deterministic offline evaluation and suite (from the original repository root)
Set-Location $repo
uv run --locked --extra dev python -m pytest tests -q
uv run --locked --extra dev python -m neova.eval
uv run --locked --extra dev python -m json.tool eval/results.json > $null
Get-Content eval/results.md

# Tracked evidence, ignored state, source integrity, history and diff
git check-ignore -v docs/issue-8-final-delivery-evidence.md
# Expected: no output, exit code 1 (path is NOT ignored).
git ls-files docs/ eval/ .env.example
git status --short --untracked-files=all
git diff --check
git diff --exit-code HEAD -- data/neova_data.json corpus/
git ls-files data/neova_data.json 'corpus/*.pdf' 'corpus/*.png' | ForEach-Object { if ((git hash-object -- $_) -ne (git rev-parse "HEAD:$_")) { throw "Source asset changed: $_" } }
git log --oneline --decorate --merges -15
git diff --stat origin/main...HEAD

# Scan *paths only*; inspect any hits privately, never paste matching lines.
git grep -I -l -E 'sk-or-v1-|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|OPENROUTER_API_KEY=[^[:space:]]+' -- ':!uv.lock'
git log --all --format='%h %s' -G 'sk-or-v1-|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|OPENROUTER_API_KEY=[^[:space:]]+' -- .
(Get-Content README.md | Measure-Object -Word -Line) | Format-List
```

For clean-checkout retrieval **if** the chosen documented prerequisite is indexing, use this only with an explicitly budget-checked key and real model names: `uv run --locked --env-file .env -- python -m neova.retrieval index`; inspect `state`, `chunks`, and `vectors`. Optional live eval, only if budget permits: `uv run --locked --env-file .env --extra dev -- python -m neova.eval --live --out eval/live` (confirm `--out` path/flags before running), followed by `uv run --locked --env-file .env -- python -m neova.usage <sanitized-local-log-path>`. Record skipped commands as blocked, not passed. Before a commit, confirm `git add -n README.md .env.example docs/issue-8-final-delivery-evidence.md` includes intended evidence; review the staged patch and check the staged source assets and secrets again.
