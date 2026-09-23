# Issue #2 / PR 1 — foundation plan (approval required)

**Status:** plan only; no application code changed or behavior verified. Target branch: `2-pr-1-foundation-one-process-app-local-state-and-demo-clock`. Issue: [PR 1 — Foundation: one-process app, local state and demo clock](https://github.com/ABDELLAH-Hallou/Neova-Telecom/issues/2). GitHub issue #1 in this repository is an unrelated Dependabot issue. This plan follows `take_home_relation_client.md`, `docs/system_design.md`, and the R1–R14 contract in `docs/requirements_analysis.md`; later PRs implement customer-facing behavior.

## 1. Current relevant behavior

- `main.py` only prints `Hello from neova-agent!` and exits; `README.md` is empty. There is no running API, LangGraph entry point, SQLite storage, session boundary, or test suite.
- `pyproject.toml` targets Python >=3.12 with no dependencies; `.python-version` pins 3.12. `uv.lock` exists. `.github/workflows/ci.yml` currently compiles only `main.py` and runs it as a terminating smoke test; that smoke step will need to change when `main.py` starts a server.
- `.env.example` has a placeholder OpenRouter key and `CHAT_MODEL` / `EMBEDDING_MODEL`, but no fallback model or clock mode. `.gitignore` excludes `.env`, `.cache/`, `.opencode/` and Python build artifacts; `docs/` is **not** ignored here.
- `data/neova_data.json` is the sole fixture: 6 customers, 3 incidents, 12 technician slots, 4 reasons, 5 escalation categories, and empty appointments/tickets. Slot starts are 27 August–1 September 2026, so they are already in the past under the present live date (23 September 2026). No code loads or modifies the fixture today.
- `docs/system_design.md` proposes one-process FastAPI + LangGraph, local SQLite, a demo session, and a frozen clock; it explicitly describes a proposal, not a working implementation. The take-home requires a later real API-backed booking tool, retrieval, handoff, evaluation, and single-key OpenRouter behavior; these are **not** foundation features.

## 2. Files that should change (after approval)

| Path | Smallest intended change |
| --- | --- |
| `main.py` | One documented `uv run --locked python main.py` launcher; bind the API to loopback and host the graph entry point in the same process. |
| `neova/app.py`, `neova/graph.py` (new) | App factory/lifespan plus a minimal, bounded compiled LangGraph and an explicitly non-customer-facing entry route; no fake answer or booking success. Provide a health route. |
| `neova/config.py`, `neova/clock.py` (new) | Central environment settings, redacted errors, live time from an aware clock, and opt-in fixed `Europe/Paris` demo time; a shared `slot.start > now` policy for later booking. |
| `neova/db.py`, `neova/session.py` (new) | First-start SQLite schema and idempotent fixture seed; reserved durable appointment/handoff tables; opaque server-issued fixture session bound to one fixture customer, never a typed customer ID acting as a credential. |
| `pyproject.toml`, `uv.lock`, `.env.example` | Minimal FastAPI/LangGraph/server/test dependencies and synced lock; placeholder-only configuration including fallback model, DB path and clock mode/time. |
| `tests/test_foundation.py` (new), `.github/workflows/ci.yml` | Offline seed/restart, session/config/clock and health/graph-entry tests; CI should run bounded tests rather than launch a non-terminating server. |
| `README.md`, `docs/issue-2-foundation-evidence.md` (new, if implementation proceeds) | Short launch prerequisites/demo limitations in README; longer sanitized foundation evidence in docs, keeping final README within two pages. |

New package paths are proposed, not existing files. An empty `neova/__init__.py` may be needed. This planning document is the **only** file being added now.

## 3. Files that should not change in this PR

- `data/neova_data.json`: seed read-only; never write appointments into the source fixture.
- All `corpus/*.pdf` and `corpus/*.png`: no extraction, retrieval indexing, or source edits until the retrieval PR (#4).
- `take_home_relation_client.md`, `docs/requirements_analysis.md`, `docs/material_inventory.md`, and `docs/system_design.md`: retain the brief and prior analysis/proposal as evidence, not retroactive implementation claims.
- No customer-read/booking/handoff endpoint implementations, search index, real model invocation, evaluation score, provider retry/fallback behavior, or live advisor queue: respectively assigned to later issues #3–#8. Do not commit `.env`, SQLite runtime DB, generated artifacts or credentials.

## 4. Smallest implementation sequence (after approval)

1. Add minimal dependencies and settings; update `.env.example` with blank placeholders, explicit `CLOCK_MODE=live` default and an opt-in fixed aware demo timestamp (proposed `2026-08-26T12:00:00+02:00`). Keep model names configurable and never log key values.
2. Add the clock policy and tests: live uses actual aware time; frozen mode requires an explicit timestamp; a past/naive slot is never considered bookable. No booking endpoint yet.
3. Add SQLite schema and first-start seed from the supplied JSON with stable unique fixture IDs. Create durable but initially empty appointment/handoff tables; on repeat start, neither reseed over existing rows nor erase future writes.
4. Add the loopback-only app and minimal compiled graph entry point. Issue opaque fixture-session tokens server-side, bind each to one allowed fixture customer, and reject a customer ID alone as a session token. Mark the entry point as foundation-only rather than pretending to answer customers; no model/network calls.
5. Make `main.py` the single-process launcher, document the one-command run and local demo-session limits, and replace CI's terminating `main.py` smoke step with bounded tests.
6. Add offline tests and a short sanitized evidence note: fresh seed/counts, restart preserving injected durable rows, unchanged source hash, session isolation, safe missing-config behavior, clock mode/past-slot checks, health/graph entry, and startup output without secrets or customer records.

## 5. Risks and unresolved assumptions

- **Fixture-session boundary is not authentication.** Proposed API binds only `127.0.0.1` and issues an unpredictable token for an explicitly selected fixture customer. Token lifetime/persistence and the eventual customer-selection route should be specified in tests; no private read should be exposed until issue #3 enforces this boundary.
- **Clock semantics:** the proposed frozen timestamp is before all supplied slots and must be opt-in; the default is live. Only the policy can be proven in this PR; actual HTTP booking rejection belongs to issue #3. Time-zone-aware comparisons must not trust the fixture's `available` flag alone.
- **Seed shape and migration:** arrays for appointments/tickets are empty and specify no row schema. Reserve minimal durable tables now; issue #3 will define transaction/idempotency constraints and may need a small schema migration. The repeat-start test must ensure existing state is not overwritten.
- **Missing key vs offline startup:** recommend allowing local health/fixture tests without OpenRouter credentials, while any model-dependent graph operation fails closed with a sanitized configuration error. Confirm whether issue #2 instead intends startup to fail when the key is absent; a real key should not be required in CI for a foundation with no model calls.
- **Clean-machine and CI behavior:** `uv run --locked python main.py` requires `uv`, Python 3.12 and dependency installation/network on first run; the server intentionally does not exit, so CI needs a bounded TestClient-based smoke test. No clean-machine run or test pass is claimed here.

## 6. Exact verification commands (to run after implementation)

Run from repository root with `uv` installed. The first command also validates that `uv.lock` matches `pyproject.toml`. TestClient tests must use an isolated temporary SQLite path and never the real fixture database. These are planned commands, **not executed results**:

```powershell
uv sync --locked
uv run --locked python -m compileall -q main.py neova tests
uv run --locked pytest -q tests/test_foundation.py
git diff --exit-code -- data/neova_data.json corpus/
git status --short
```

Manual single-process smoke, after creating a local `.env` with non-secret demo configuration (or using documented defaults). In terminal 1:

```powershell
uv run --locked python main.py
```

In terminal 2:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Stop terminal 1 with Ctrl+C. The offline tests must assert JSON seed counts `6/3/12/4/5`, unchanged source SHA-256 across initialization/restart, unchanged saved rows across restart, live past-slot refusal, frozen future-slot eligibility, lack of credential leakage, and that an ID typed as a session token is rejected. Health alone does **not** establish a working customer agent.
