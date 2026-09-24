# Issue #4 / PR 3 — retrieval plan (approval required)

**Status:** plan only; no implementation or verification has run. Target branch: `4-pr-3-retrieval-verified-public-corpus-hybrid-search-and-evidence-gate`. GitHub issue [#4 — PR 3: Retrieval](https://github.com/ABDELLAH-Hallou/Neova-Telecom/issues/4) matches this branch. Context: `take_home_relation_client.md`, `docs/system_design.md` (§“Retrieval and authority details”), `docs/material_inventory.md`, R1–R4/R9–R11 in `docs/requirements_analysis.md`. No test outcome is claimed here.

## 1. Current relevant behavior

- **No retrieval exists.** `README.md` lists “Knowledge-base retrieval” as unimplemented; `neova/graph.py` is the placeholder `input → classify → END` graph with no corpus access. Nothing in `neova/` reads `corpus/`.
- **Corpus state:** ten PDFs plus `fiche-roaming-international-scan.png` in `corpus/`. Git-ignored `.cache/pdf-text/*.txt` holds derived text extracts with `CORPUS` banners, `corpus/*.md` source labels, `1/2 · 2/2` footers and form-feed page breaks. `docs/material_inventory.md` documents metadata (id, status, public flag, `maj` date) from those extracts **only** — the issue explicitly forbids treating this cached text as independent verification. The PNG has no extract at all.
- **Access split already evidenced:** two PDFs are `public internal` (`politique-geste-commercial`, `procedure-escalade-n2`) and must stay routing-only; `promo-rentree-2024` is public but `deprecated`/archived; the roaming PNG is public but footer-marked “non contractuel”.
- **Storage:** `neova/db.py` schema covers fixture tables, `appointments`, `handoffs`, `_meta` only — no FTS5 table, no chunk table, no vector cache. `_meta` exists and is a safe place for indexing-state keys. Existing local `neova.db` files must keep working (same constraint as the PR-2 unique-slot migration).
- **Config/models:** `EMBEDDING_MODEL` is declared in `.env.example` but read nowhere. `neova/models.py` wraps chat only; OpenRouter `/embeddings` (non-streaming, same key/base URL) has no client. No budget gate exists yet (that is issue #6’s resilience work; the issue says live embedding calls need a supplied-key budget gate, else use mocked calls).
- **Tests/CI:** `tests/test_dto.py`, `test_customer_api.py`, `test_foundation.py`, `test_models.py` run offline in CI; CI checks `git diff --exit-code -- data/neova_data.json corpus/`. No retrieval tests exist.

## 2. Files that should change

| Path | Minimum intended change |
| --- | --- |
| `docs/source_register.md` (new) | Reviewable register built from **independent** inspection: every original PDF opened with page numbers, the PNG checked against its image; per-source extraction coverage, path/page-or-section, update date, status, public/internal access, verification method and date. Explicitly records that `.cache/pdf-text/` was used only as cross-check, not evidence. |
| `neova/corpus.py` (new) | Original-PDF extraction (with page numbers), banner/footer removal, heading/page chunking into ~250–450-word chunks with overlap, fee tables and their notes kept together; PNG transcription consumed as reviewed data. Emits chunks tagged with source, page/section, public/internal. |
| `neova/embeddings.py` (new) | OpenRouter `/embeddings` client (non-streaming, supplied key/base URL, `EMBEDDING_MODEL`); cache key = chunk hash + model name in SQLite; rebuild triggers on model change; visible degraded state when embedding is unavailable; bounded retry/backoff; budget-gate hook so optional live calls stop when remaining cap is inadequate. |
| `neova/retrieval.py` (new) | Public-only indexing pipeline into SQLite (FTS5 index + cached vectors), hybrid search (local cosine + FTS5 exact-term), dedupe, top-3 cited passages with source/page; visible FTS5 fallback and degraded flags; the small evidence gate (2026 grid over 2024 promo for general prices; fee-timing conflict, absent invoice line items, non-contractual roaming flags; retrieved text treated as data, never instructions). |
| `neova/db.py` | Add idempotent `chunks`, `chunk_vectors` (cache), `embeddings_meta`/FTS5 virtual-table schema creation alongside the existing tables; touch nothing in the session/booking/handoff code paths. |
| `neova/config.py` | Add `get_embedding_model()` and corpus-path accessor, following the existing fail-closed `_require` pattern. |
| `tests/test_retrieval.py` (new) | Offline tests with **mocked** embeddings: public/internal filtering, chunk/table-note integrity, French paraphrase and exact-term cases (including the PNG chunk), semantic-only vs FTS5-only vs combined top-3 comparability, query-embedding outage → visible FTS5 fallback, indexing outage → degraded (not silent full coverage), cache reuse and model-change rebuild, internal content never in results, evidence-gate current/archive, fee-timing and missing-line-item cases. |
| `.github/workflows/ci.yml` | Add `tests/test_retrieval.py` to the offline suite. |
| `pyproject.toml` + `uv.lock` | Add a PDF text extractor (e.g. `pypdf`) pinned in the existing `<`-bounded style; regenerate the lock. No other dependency. |

**Deliberately not in this PR** (per issue non-goals and commit boundaries): a search HTTP route in `app.py`, graph wiring, `README.md` updates, reranking, multi-query, hosted vector DB, conversation answers, the full spend-logging of issue #6.

## 3. Files that should not change

- `corpus/*` and `data/neova_data.json`: read-only inputs; CI already enforces their byte-stability. The PNG transcription lives in the register/module, not by editing the image.
- `.cache/**`: git-ignored derived cache; never cited as verification.
- `neova/graph.py`, `neova/app.py`, `neova/customer_api.py`, `neova/dto.py`, `neova/session.py`, `neova/clock.py`, `neova/models.py`, `main.py`: graph/tool wiring and HTTP surface belong to issues #5/#6/#7. If a retrieval interface proves insufficient for the graph later, widen it there with a recorded reason.
- `tests/test_dto.py`, `test_customer_api.py`, `test_foundation.py`, `test_models.py`: existing contracts stay green and untouched.
- `take_home_relation_client.md`, `docs/system_design.md`, `docs/requirements_analysis.md`, `docs/material_inventory.md`, `docs/issue-2-*`, `docs/issue-3-*`, `README.md`, `manual-test-customer-api.md`: brief, prior evidence and README delivery are out of this PR’s boundaries.
- `.env`, `*.db`: never committed; retrieval tests use temporary databases and mocked calls only.

## 4. Smallest implementation sequence (after approval)

1. **Source review commit:** extract the ten original PDFs page-by-page and check the PNG against its image; write `docs/source_register.md` with coverage, path/page/section, `maj` date, status, public/internal flag and method; cross-check against `.cache/pdf-text/` without treating it as evidence. Add the extractor dependency in the same commit.
2. **Chunking module:** implement banner/footer stripping and heading/page chunking (250–450 words, overlap, tables+notes atomic) in `neova/corpus.py`, driven by register metadata; unit-test chunk boundaries and table integrity offline.
3. **Storage:** extend `neova/db.py` with idempotent chunk/vector-cache/FTS5 schema and `_meta` indexing-state keys; verify a pre-existing foundation DB migrates without touching customer tables.
4. **Embedding cache + gate:** add `neova/config.py` accessors and `neova/embeddings.py` with hash+model cache, model-change rebuild, bounded retry, degraded state and the budget-gate hook (key `/key` check; stop optional live calls when remaining cap is insufficient); offline behavior fully testable with injected fakes.
5. **Public-only index:** build the indexing pipeline that excludes the two `public internal` PDFs from chunks/FTS5/vectors/answer context (register marks them routing-only) and flags incomplete indexing as degraded.
6. **Hybrid search + evidence gate:** implement cosine + FTS5 merge, dedupe, top-3 cited passages with source/page, visible FTS5 fallback on query-embedding failure, and the gate rules (2026-over-2024 for general prices, fee-timing conflict flag, missing invoice line items, non-contractual roaming, retrieved text as data).
7. **Tests/CI commit:** add `tests/test_retrieval.py` (mocked embeddings, temp DBs), wire into CI, record the three commit boundaries (source review; indexing/cache/search; gate/tests) — no graph or README work bundled.

## 5. Risks and unresolved assumptions

- **Extraction fidelity:** `pypdf`-style extraction of the originals may differ from the supplied `.cache` text (spacing, table flattening, accents). The register must record what the originals actually yield; chunking thresholds may need per-document tuning. Unresolved: whether any PDF needs a second extractor.
- **PNG verification is manual:** no OCR dependency is planned; the roaming sheet is transcribed by hand against the image and hashed. Risk: transcription error becomes a “verified” fact — mitigate by recording the PNG hash and the reviewer/date in the register.
- **Live embedding cost vs offline tests:** acceptance requires reporting **actual** top-three hits on French queries, which implies at least one live indexing run through the supplied key. Assumption: the remaining cap allows it; otherwise indexing stays mocked and the run is reported as degraded — never presented as live results. Budget gate must run before any batch.
- **Internal leakage:** FTS5 is cheap to build “for everything”; the filter must exclude internal chunks from *all* indexes (FTS5, vectors, answer context), not only embeddings. Tests must assert internal terms never surface.
- **Schema migration on existing DBs:** `neova.db` files from earlier issues must gain the new tables idempotently; FTS5 external-content vs standalone table choice affects rebuild behavior. Decide: standalone FTS5 table rebuilt from `chunks` (simplest, no trigger complexity).
- **Evidence gate scope creep:** the gate is rule-based over retrieved passages; it must not start inferring individual contract terms (issue boundary) nor pre-empt issue #6’s logging/fallback policy beyond a bounded retry on embeddings.
- **Degraded semantics:** “degraded” must be observable (flag in results) but this PR has no HTTP surface — the flag lives in the returned structure and tests; surfacing it to users happens with the graph.
- **Dependency choice:** adding a PDF library touches the lockfile; keep to one, pinned `<`-bounded, used only by the offline chunker.

## 6. Exact verification commands (after implementation)

From the repository root in PowerShell, with `uv` installed. Tests use temporary databases and **mocked** embeddings; no network calls in CI. Planned, not yet run:

```powershell
uv sync --locked --extra dev
uv run --locked --extra dev python -m compileall -q main.py neova tests
uv run --locked --extra dev python -m pytest tests/test_retrieval.py tests/test_dto.py tests/test_customer_api.py tests/test_foundation.py tests/test_models.py -q
git diff --check
git diff --exit-code HEAD -- data/neova_data.json corpus/
git status --short
```

Live acceptance evidence (optional, single supplied key, only after the budget gate passes; report real top-3 hits, never invented scores):

```powershell
# Budget check before any batch indexing:
curl -s https://openrouter.ai/api/v1/key -H "Authorization: Bearer $env:OPENROUTER_API_KEY"

# One bounded live run over the public corpus (planned CLI, implemented in step 5):
uv run --locked --env-file .env -- python -m neova.retrieval index
uv run --locked --env-file .env -- python -m neova.retrieval search "Comment résilier mon abonnement ?"
uv run --locked --env-file .env -- python -m neova.retrieval search "frais de rejet de prélèvement 2 euros"
```

The three semantic-only / FTS5-only / combined top-3 comparisons and the outage-fallback case are produced by `tests/test_retrieval.py` with fake embeddings, plus the live run above when budget allows. Evidence goes in a follow-up `docs/issue-4-retrieval-report.md` after checks actually run.