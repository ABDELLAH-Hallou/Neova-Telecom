# Issue #4 / PR 3 — retrieval implementation report

**Addendum 5 (module split + SQL layer, same cycle):** `neova/corpus.py` split into three single-responsibility modules — `neova/sources.py` (metadata: manifest loading/validation, `SourceMeta`, resource/corpus paths), `neova/extraction.py` (strategy selection, `extract_pdf_pages`, artifact cleaning, markdown normalization, title/heading/table classifiers) and `neova/chunking.py` (`Chunk`, unit building, packing, `chunk_document`, `iter_chunks`/`public_chunks`, `chunk_hash_for`); `corpus.py` deleted with no facade — all importers updated (`neova.retrieval`, `neova.db`, both retrieval test files). All SQL moved out of `neova/retrieval.py` into `neova/db.py`: `replace_chunk_index`, `has_vector`, `store_vectors`, `vector_count`, `chunks_without_vector`, `chunk_count`, `fts_hits`, `public_vectors`, `load_public_chunk` (+ renamed `sync_sources_table` now reading the manifest directly); `retrieval.py` keeps only scoring, fusion, degradation, the evidence gate and the CLI. Full suite **87 passed**; `corpus/` and `data/neova_data.json` untouched.

**Addendum 4 (pymupdf4llm becomes the fitted default, same cycle):** based on the evaluation artifacts in `output/pymupdf4llm/`, the default extraction strategy switched from `pypdf` to `pymupdf4llm` (`DEFAULT_EXTRACTOR_NAME`), and the title/heading functions were rewritten to fit its markdown: `_doc_title` now treats an explicit markdown H1 (`#`) as the authoritative title (bold/inline-code markers stripped, fallback kept for plain-text strategies), and `_is_artifact` drops `<!-- PAGE N -->` separators alongside the banner/fence/footer rules. Result: 14 chunks total — 12 public, 2 routing-only internal — with **exact page attribution** (`p1-1`/`p2-2`) and real heading sections (e.g. `Article 14 — Restitution des équipements`, `Échelonnement`). Tests updated for the new shape (`tests/test_pdf_extractors.py` 16, `tests/test_retrieval.py` 32); full suite **87 passed**; `corpus/`, `data/neova_data.json` and the evaluation artifacts untouched. Note: `output/` and `scripts/` are the evaluation harness's untracked artifacts and stay out of commits.

**Addendum 3 (third strategy, same cycle):** `PyMuPDF4LLMExtractor` added to `neova/pdf_extractors.py` (`pymupdf4llm>=0.0.17,<0.2` resolved 0.1.9): page-exact segments via `to_markdown(page_chunks=True)` — unlike markitdown it preserves page attribution **and** emits real `#`/`##` markdown headings, which the corpus pipeline already treats as authoritative section boundaries. Two new artifact rules for its output: bold letter-spaced `**CO R P U S**` banner variants and inline-code/backtick-wrapped footers (`` `corpus/x.md` 1 / 2 ``) plus code-fence delimiters are now stripped; `_doc_title` de-bolds. Tests bring `tests/test_pdf_extractors.py` to 15; full suite **86 passed**; `corpus/`/`data/` untouched. Strategy comparison so far: pypdf (default, page-exact, plain text) · markitdown (coarse pages, pipe tables, no headings) · pymupdf4llm (page-exact **and** markdown headings — currently the best fit for citation accuracy; still opt-in via `PDF_EXTRACTOR` until the live evaluation).

**Addendum 2 (PDF extractor strategy, same cycle):** `neova/pdf_extractors.py` adds the strategy pattern: `PdfExtractor` (ABC) → `PypdfExtractor` (foundation behavior kept, default; one raw segment per page) and `MarkItDownExtractorAdapter` (microsoft/markitdown, `markitdown[pdf]>=0.1.3,<0.2` added; whole document as markdown with explicit pipe tables; page attribution coarse 1..N since markitdown drops page boundaries). Selection per call or via `PDF_EXTRACTOR` env (default `pypdf`; unknown names fail closed). `_is_heading` hardened (rejects pipe/`€`/`%`/digit-led table rows; `#`-lines authoritative), `_is_table` now catches markdown pipe tables and second-line table headers, markdown cleaning strips separators/emphasis/links and bare `1 / 2` page footers. New `tests/test_pdf_extractors.py` (12 tests); full suite **83 passed**; `corpus/` and `data/` untouched. Default indexing behavior is unchanged (pypdf); markitdown is opt-in until evaluated against the live corpus.

**Addendum (manifest refactor, same day):** source metadata moved out of code into `neova/resources/sources.json` (validated source of truth: structure, types, allowed values, unique IDs and paths, ISO dates, PNG transcription reference + SHA-256 match against `corpus/`), converted at load into the immutable `SourceMeta`. The roaming transcription moved to `neova/resources/fiche-roaming-international-scan.txt` and is read at call time. A runtime `sources` table (PK `source_id`, unique `path`) is created idempotently with the retrieval schema and mirrored from the manifest by `db.sync_sources_table()` during indexing (upsert + prune of removed entries; manifest stays authoritative). New tests bring `tests/test_retrieval.py` to 32 (manifest load/conversion, uniqueness, corpus-file existence, PNG hash, transcription, rejection cases, sources-table sync/prune, indexing population); full suite **71 passed**; `corpus/` and `data/neova_data.json` untouched.

**Status:** implemented on branch `4-pr-3-retrieval-verified-public-corpus-hybrid-search-and-evidence-gate`, following the approved plan in `docs/issue-4-retrieval-plan.md` (steps 1–7 only). Nothing is committed or pushed; all results below are from commands actually run on 2026-09-23.

## Files changed

| Path | Change |
| --- | --- |
| `docs/source_register.md` (new) | Independent source review: ten PDFs extracted page-by-page with `pypdf`, PNG inspected as an image and transcribed; per-source coverage, page map, `maj` date, status, public/internal access, verification method, SHA-256 hashes; recorded conflicts (fee timing, current vs archived prices, roaming authority) and the indexing decisions derived from the register. `.cache/pdf-text/` used only as cross-check. |
| `neova/corpus.py` (new) | Register-driven extraction and deterministic chunking: banner/footer/metadata/breadcrumb stripping, heading-aware sectioning, ~250–450-word chunks with overlap when a section splits, fee tables fused with their notes, roaming PNG served from the reviewed transcription (no OCR at runtime), `access` tagging with `public_chunks()` / `iter_chunks()`. |
| `neova/embeddings.py` (new) | OpenRouter non-streaming `/embeddings` client on the supplied key/base URL, `EMBEDDING_MODEL` from env, bounded retry/backoff on 408/429/5xx/529, budget gate (`/key` check; `ensure_budget` raises `BudgetExceeded` and stops optional live calls), vector serialization helpers and plain cosine. |
| `neova/retrieval.py` (new) | Public-only indexing (`chunks` + `chunk_fts` + `chunk_vectors`, cache by chunk hash + model, reuse across runs, old-model vectors retained, rebuild on model change), hybrid search (cosine + FTS5 bm25 via RRF, dedupe, top-3 cited passages with source/pages), visible degraded flags (query-embedding failure → FTS5 fallback, incomplete indexing, empty index), rule-based evidence gate, `index`/`search` CLI. |
| `neova/db.py` | Added idempotent `RETRIEVAL_SCHEMA` (`_meta` shared, `chunks`, `chunk_vectors` with `(chunk_hash, model)` PK, standalone `chunk_fts` FTS5 table), `init_retrieval_schema()`, `get_meta()`/`set_meta()`; called from `_initialize()` so existing databases migrate on startup. Booking/session/customer code paths untouched. |
| `neova/config.py` | Added `get_embedding_model()` following the fail-closed `_require` pattern. |
| `tests/test_retrieval.py` (new) | 18 offline tests, mocked embeddings only: register coverage and internal exclusion, artifact stripping, table/note integrity, roaming chunk, index determinism, cache reuse (zero embed calls), model change (old cache kept, meta updated), outage degradation, incomplete-index flag, empty index, internal chunk unreachable, semantic/FTS/hybrid comparability with dedupe, evidence-gate flags, `_meta` helpers, fail-closed live client, budget gate, cosine/blob roundtrip, foundation DB migration. |
| `.github/workflows/ci.yml` | Added `tests/test_retrieval.py` to the offline CI suite. |
| `pyproject.toml`, `uv.lock` | Added `pypdf>=6.1.0,<7` (resolved 6.19.0); only dependency change. |

Not changed (per plan): `corpus/*`, `data/neova_data.json`, `graph.py`, `app.py`, `customer_api.py`, `dto.py`, `session.py`, `clock.py`, `models.py`, `main.py`, `README.md`, prior docs, `.env`, `*.db`. No commits were made.

## Behavior added

1. **Verified source register** — all ten original PDFs independently extracted (0 empty pages, 1–2 pages each) and the PNG inspected; `docs/source_register.md` records coverage, citations, status, access flags and unresolved conflicts.
2. **Chunking** — 13 chunks over 11 sources; 11 customer-public chunks (146–450 words, two legitimate undersized tails where merging would exceed 450) and 2 routing-only internal chunks that never enter any index.
3. **Public-only indexing** — `chunks`/`chunk_fts`/`chunk_vectors` in SQLite; embeddings cached by `(chunk_hash, model)`; a re-index of an unchanged corpus makes zero embedding calls; a model change re-embeds and keeps the old model's vectors; any embedding failure keeps valid cached vectors and sets `_meta` `retrieval_index_state=degraded` with a reason.
4. **Hybrid search** — semantic (local cosine) + FTS5 (bm25, OR-joined quoted tokens) with reciprocal-rank fusion, deduplication, max 3 passages, each carrying `source_id`, `path`, page range, section and per-mode scores. Query-embedding failure or missing `EMBEDDING_MODEL` degrades visibly to FTS5 (`query_embedding_failed_fts_fallback`); semantic-only mode claims nothing when embeddings are unavailable. Zero-similarity passages are never returned as semantic matches.
5. **Evidence gate** — flags, never resolves: `archived_pricing` (2024 promo), `fee_timing_conflict` + `invoice_line_items_absent` (faq-facturation), `non_contractual_source` (roaming scan). All passages are returned as data (`data_only: true`), not instructions.
6. **CLI** — `uv run ... python -m neova.retrieval index|search [--mode ...]`, budget-gated before any live batch.

## Commands run and real results

| Command | Result |
| --- | --- |
| `uv sync --extra dev` (after adding `pypdf`) | Installed `pypdf==6.19.0`; lock updated. |
| `uv run python .cache/pdf-review/extract.py` | 10 PDFs: 2 pages each except `promo-rentree-2024.pdf` (1); 0 empty pages; hashes recorded. |
| `uv run --locked --extra dev python -m compileall -q main.py neova tests` | exit 0. |
| `uv run --locked --extra dev python -m pytest tests/test_retrieval.py -q` | 18 passed. |
| `uv run --locked --extra dev python -m pytest tests/test_retrieval.py tests/test_dto.py tests/test_customer_api.py tests/test_foundation.py tests/test_models.py -q` | **57 passed**, 1 pre-existing starlette `DeprecationWarning`. |
| `uv sync --locked --extra dev` | Resolved 58 packages, no changes needed. |
| `git diff --check` | clean (exit 0). |
| `git diff --exit-code HEAD -- data/neova_data.json corpus/` | unchanged (exit 0). |
| `git status --short` | exactly the files listed above; nothing staged or committed. |

**Not run:** the optional live embedding block (`python -m neova.retrieval index` / `search` with the supplied key). It spends the shared key cap and was not approved; the budget gate is in place and tested. Actual top-three hits on real French queries therefore remain to be produced by that run (see remaining work).

## Assumptions taken (recorded, not guessed silently)

1. **PNG transcription is the reviewed artifact** — no OCR dependency; transcription stored verbatim in `neova/corpus.py` with the image SHA-256 in the register. A human should still eyeball it before merge.
2. **Chunk sizes** — "approximately 250–450" is implemented as a hard MAX of 450 and a soft MIN of 250 with cross-section absorption and a tail-merge; the 2024 promo (146 words) and two tails (173–175 words) stay small by design because merging would breach the cap.
3. **Internal chunks are never stored**, not just never embedded — routing-only documents stay out of `chunks`, FTS5, vectors and search results entirely.
4. **Zero-cosine filtering** — a passage with zero similarity is not a semantic match and is never returned in semantic mode; RRF merges rankings only.
5. **FTS5 uses OR-joined quoted tokens** (recall-first exact-term matching with bm25 ranking); a phrase-AND query would be stricter but miss longer French questions.
6. **Budget gate** requires `limit_remaining ≥ 1.00 USD` before any optional live batch (`ensure_budget`, monkeypatch-tested); the exact threshold is a parameter, not a policy from the issue.
7. **Search has no HTTP surface in this PR** — consumed via `neova.retrieval` functions and CLI; the graph wires it in issue #5.
8. **`RetrievalError`/`BudgetExceeded`/`EmbeddingError` are new module-level exceptions**; no existing exception contract was widened.

## Remaining work

1. **Live acceptance evidence:** run the budget check, then one bounded `index` + `search` pass with the supplied key and record real top-3 hits (semantic-only / FTS-only / hybrid) in a follow-up evidence note; keep it stopped if the remaining cap is below the gate.
2. **Human spot-check of `docs/source_register.md`** (especially the PNG transcription) before merge — the register marks reviewer = coding agent.
3. Downstream issues: graph wiring (issue #5), resilience/spend logging (issue #6), eval + README delivery (issues #7/#8). Nothing here pre-wires them.

Next: review this report, then say the word and I prepare the three planned commit boundaries (source review; indexing/cache/search; gate/tests) — still without pushing until you say so.