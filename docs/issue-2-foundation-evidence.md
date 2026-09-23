# Issue #2 foundation evidence

This note records observed checks, not evidence for a working customer agent.

- Baseline on Windows, Python 3.12.4: `uv run --extra dev python -m pytest tests/test_foundation.py -v` → **12 passed**, one Starlette/AnyIO deprecation warning. The original tests injected environment variables and did not exercise the lifespan.
- Before repair, `uv run --locked python main.py` → `ImportError: attempted relative import with no known parent package`.
- After integrating the app and graph and adding `tzdata`: `uv run --extra dev python -m pytest tests/test_foundation.py -k "clock or session or health" -v` → **5 passed**, three deselected, one upstream deprecation warning.
- After isolating SQLite tests: `uv run --extra dev python -m pytest tests/test_foundation.py -k "seed or fixture or missing_key" -v` → **3 passed**, five deselected, same warning.
- Locked install: `uv sync --locked --extra dev` → 57 packages resolved, 55 audited; no lock mismatch.
- Locked compile: `uv run --locked --extra dev python -m compileall -q main.py neova tests` → exit 0.
- Complete suite after the launcher-output test: `uv run --locked --extra dev python -m pytest tests/test_foundation.py -v` → **9 passed**, one upstream Starlette/AnyIO deprecation warning.
- After adding the SQLite manager singleton and concurrent-init tests, the same locked suite → **11 passed**, one upstream Starlette/AnyIO deprecation warning. The two targeted manager tests → **2 passed**. Compile and fixture/corpus diff checks still exit 0.
- The manager singleton has since been replaced by a connection per issued demo session; initialization still uses a short-lived connection. Targeted tests for per-session reuse, isolation and serialized concurrent access passed **3/3**. Application lifespan closes cached connections.
- After the session-scoped connection change, the complete locked foundation suite → **12 passed**, one upstream Starlette/AnyIO deprecation warning. Compilation and fixture/corpus diff checks exited 0.
- Single-process smoke on Windows: launched `uv run --locked python main.py` with `DATABASE_URL` pointed to a temporary SQLite path and no `OPENROUTER_API_KEY`; `GET http://127.0.0.1:8000/health` → HTTP 200 with `status=ok`, `mode=foundation`, `db_ready=true`, `fixture_customers=6`. The temporary SQLite file existed and the server was stopped.
- `git diff --exit-code -- data/neova_data.json corpus/` → exit 0; `git diff --check` → exit 0 (Git printed Windows LF/CRLF conversion notices, not patch errors).

The tests now exercise a fresh temporary SQLite database, a second
initialization preserving edited fixture rows plus durable appointment and
handoff records, stable reason/category IDs, enforced foreign keys, seed
counts (6/3/12/4/5), and an unchanged SHA-256 of the supplied JSON. They
also check live past-slot refusal, frozen future-slot eligibility, naive
slot refusal, missing configuration without credential disclosure, real
TestClient lifespan startup, a bounded graph response, and opaque
process-local session isolation (including malformed and customer-ID inputs).

The supplied `data/neova_data.json` and `corpus/` are read-only. No
customer answers, appointment booking or handoff endpoints, retrieval,
model calls, or evaluation are implemented in this issue. The demo session
is not authentication; the service must stay loopback-only. Existing local
SQLite databases created by the earlier scaffold can contain numbered
reason/category IDs; no migration is claimed here.
