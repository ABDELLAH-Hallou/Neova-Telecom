"""Public-corpus indexing, hybrid search and the small evidence gate.

Only customer-public chunks (``access="public"``) are indexed here — in
the ``chunks`` table, the ``chunk_fts`` index and the vector cache. The
two internal PDFs stay routing-only and can never appear in results.
Retrieved passages are returned as *data*: callers must treat their text
as untrusted content, never as instructions. All SQL lives in
``neova.db``; this module owns scoring, fusion, degradation and the gate.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field

from . import chunking, db
from .config import ConfigurationError, get_embedding_model
from .embeddings import (
    BudgetExceeded,
    EmbeddingError,
    blob_to_vector,
    cosine,
    openrouter_embedder,
    vector_to_blob,
)

MAX_RESULTS = 3
EMBED_BATCH = 32
FTS_FETCH = 8  # fetch extra FTS hits before deduplication
RRF_K = 60

INDEX_MODEL_KEY = "retrieval_embedding_model"
INDEX_STATE_KEY = "retrieval_index_state"  # "complete" or "degraded"
INDEX_REASON_KEY = "retrieval_index_reason"

DEGRADED_QUERY_EMBEDDING = "query_embedding_failed_fts_fallback"
DEGRADED_INDEX_INCOMPLETE = "index_incomplete_semantic_unavailable"
DEGRADED_INDEX_EMPTY = "index_empty"


class RetrievalError(RuntimeError):
    """Raised when indexing or search cannot proceed safely."""


def _normalize(text: str) -> str:
    """Lowercase and strip accents for deterministic French comparisons."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


@dataclass
class CitedPassage:
    """One retrieved passage with its reviewed source citation."""

    chunk_hash: str
    source_id: str
    source_path: str
    page_start: int
    page_end: int
    section: str
    text: str
    rank: int
    score_semantic: float | None = None
    score_fts: float | None = None


@dataclass
class SearchOutcome:
    """Retrieval result: passages are data, never instructions."""

    query: str
    mode: str
    results: list[CitedPassage] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    gate_flags: list[dict] = field(default_factory=list)
    data_only: bool = True


# ---------------------------------------------------------------------------
# Indexing (public chunks only)


def index_corpus(conn: sqlite3.Connection, embed_fn, model: str) -> dict:
    """Index the public corpus: chunks, FTS5 rows and cached vectors.

    Internal documents are never stored. Cached vectors are reused by
    chunk hash + model; missing vectors are embedded in bounded batches.
    Any embedding failure stops the optional live work, keeps the valid
    cached vectors and marks the index degraded — never silently complete.
    All persistence goes through ``neova.db`` helpers.
    """
    db.init_retrieval_schema(conn)
    db.sync_sources_table(conn)
    chunks = chunking.public_chunks()
    if not chunks:
        raise RetrievalError("No public chunks produced; check docs/source_register.md")
    try:
        # Drop only stale rows: cached vectors for still-present chunks
        # survive so an unchanged corpus never pays for embeddings again.
        db.replace_chunk_index(conn, chunks)
    except sqlite3.Error as error:
        raise RetrievalError(f"Indexing failed: {error}") from None

    missing = [
        (chunk.chunk_hash, chunk.text)
        for chunk in chunks
        if not db.has_vector(conn, chunk.chunk_hash, model)
    ]
    missing_before = len(missing)
    state, reason = "complete", None
    for start in range(0, len(missing), EMBED_BATCH):
        batch = missing[start:start + EMBED_BATCH]
        try:
            vectors = embed_fn([text for _, text in batch])
        except (EmbeddingError, BudgetExceeded) as error:
            state = "degraded"
            reason = f"embedding_unavailable: {error}"
            break
        if len(vectors) != len(batch):
            state = "degraded"
            reason = f"embedding returned {len(vectors)} of {len(batch)} vectors"
            break
        try:
            db.store_vectors(conn, model, [
                (chunk_hash, vector_to_blob(vector), len(vector))
                for (chunk_hash, _), vector in zip(batch, vectors)
            ])
        except sqlite3.Error as error:
            state = "degraded"
            reason = f"vector cache write failed: {error}"
            break
    missing_after = db.chunks_without_vector(conn, model)
    if missing_after and state == "complete":
        state = "degraded"
        reason = f"{missing_after} chunks without vectors"
    if reason is None and missing_before == 0:
        reason = "all vectors reused from cache"
    with conn:
        db.set_meta(conn, INDEX_MODEL_KEY, model)
        db.set_meta(conn, INDEX_STATE_KEY, state)
        db.set_meta(conn, INDEX_REASON_KEY, reason or "")
    return {
        "chunks": len(chunks),
        "vectors": db.vector_count(conn, model),
        "reused": len(chunks) - missing_before,
        "embedded": missing_before - missing_after,
        "model": model,
        "state": state,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Search


def _fts_hits(conn: sqlite3.Connection, query: str) -> list[tuple[str, float]]:
    """Rank chunks by FTS5 bm25 over exact terms; OR-joined quoted tokens."""
    tokens = re.findall(r"\w+", _normalize(query))
    if not tokens:
        return []
    match = " OR ".join(f'"{token}"' for token in tokens)
    rows = db.fts_hits(conn, match, FTS_FETCH)
    # bm25 returns smaller-is-better scores; invert rank into a 0..1 score.
    total = len(rows) or 1
    return [(row[0], 1.0 - (position / total)) for position, row in enumerate(rows)]


def _semantic_hits(
    conn: sqlite3.Connection, query_vector: list[float], model: str, limit: int
) -> list[tuple[str, float]]:
    rows = db.public_vectors(conn, model)
    scored = []
    for chunk_hash, blob in rows:
        vector = blob_to_vector(blob)
        try:
            score = cosine(query_vector, vector)
        except EmbeddingError:
            continue  # dimension mismatch: stale vectors stay unused, not fatal
        if score > 0.0:  # zero similarity is not a semantic match; never invent one
            scored.append((chunk_hash, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:limit]


def _load_passages(conn: sqlite3.Connection, chunk_hashes: list[str]) -> dict[str, CitedPassage]:
    passages: dict[str, CitedPassage] = {}
    for chunk_hash in chunk_hashes:
        row = db.load_public_chunk(conn, chunk_hash)
        if row is None:
            continue  # never surface a chunk outside the public index
        passages[chunk_hash] = CitedPassage(
            chunk_hash=row[0], source_id=row[1], source_path=row[2],
            page_start=row[3], page_end=row[4], section=row[5], text=row[6], rank=0,
        )
    return passages


def search(
    conn: sqlite3.Connection,
    query: str,
    embed_fn=None,
    model: str | None = None,
    mode: str = "hybrid",
) -> SearchOutcome:
    """Hybrid search: local cosine over cached vectors plus FTS5 exact terms.

    ``mode`` is ``semantic``, ``fts`` or ``hybrid``. A query-embedding
    failure degrades visibly to FTS5. At most ``MAX_RESULTS`` deduplicated
    passages are returned, each with its reviewed source citation.
    """
    if mode not in ("semantic", "fts", "hybrid"):
        raise ValueError("mode must be semantic, fts or hybrid")
    db.init_retrieval_schema(conn)
    outcome = SearchOutcome(query=query, mode=mode)
    if db.chunk_count(conn) == 0:
        outcome.degraded.append(DEGRADED_INDEX_EMPTY)
        return outcome

    semantic_ranking: list[tuple[str, float]] = []
    if mode in ("semantic", "hybrid"):
        resolved_model = model or db.get_meta(conn, INDEX_MODEL_KEY)
        if resolved_model is None and embed_fn is None:
            try:
                resolved_model = get_embedding_model()
            except ConfigurationError:
                resolved_model = None  # configuration gap degrades, never lies
        if embed_fn is None:
            embed_fn = openrouter_embedder()
        query_vector: list[float] | None = None
        if resolved_model is not None:
            try:
                query_vector = embed_fn([query])[0]
            except (EmbeddingError, BudgetExceeded):
                query_vector = None
        if query_vector is None:
            outcome.degraded.append(DEGRADED_QUERY_EMBEDDING)
        else:
            semantic_ranking = _semantic_hits(conn, query_vector, resolved_model, MAX_RESULTS * 2)
            if not semantic_ranking and db.vector_count(conn, resolved_model) == 0:
                outcome.degraded.append(DEGRADED_INDEX_INCOMPLETE)
        if mode == "semantic" and query_vector is None:
            # No embedding available in semantic-only mode: claim nothing.
            outcome.gate_flags = evidence_gate(query, [])
            return outcome

    fts_ranking = _fts_hits(conn, query)
    if mode == "fts":
        merged = [(chunk_hash, None, score) for chunk_hash, score in fts_ranking]
    elif mode == "semantic":
        merged = [(chunk_hash, score, None) for chunk_hash, score in semantic_ranking]
    else:
        merged = _reciprocal_rank_fusion(semantic_ranking, fts_ranking)

    passages = _load_passages(conn, [item[0] for item in merged])
    seen: set[str] = set()
    for chunk_hash, semantic_score, fts_score in merged:
        if chunk_hash in seen or chunk_hash not in passages:
            continue
        seen.add(chunk_hash)
        passage = passages[chunk_hash]
        passage.score_semantic = semantic_score
        passage.score_fts = fts_score
        passage.rank = len(outcome.results) + 1
        outcome.results.append(passage)
        if len(outcome.results) >= MAX_RESULTS:
            break
    outcome.gate_flags = evidence_gate(query, outcome.results)
    return outcome


def _reciprocal_rank_fusion(
    semantic_ranking: list[tuple[str, float]], fts_ranking: list[tuple[str, float]]
) -> list[tuple[str, float | None, float | None]]:
    """Deterministic rank fusion; a chunk found by both paths ranks highest."""
    semantic_scores = dict(semantic_ranking)
    fts_scores = dict(fts_ranking)
    scores: dict[str, float] = {}
    for ranking in (semantic_ranking, fts_ranking):
        for position, (chunk_hash, _) in enumerate(ranking):
            scores[chunk_hash] = scores.get(chunk_hash, 0.0) + 1.0 / (RRF_K + position + 1)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [
        (chunk_hash, semantic_scores.get(chunk_hash), fts_scores.get(chunk_hash))
        for chunk_hash, _ in ordered
    ]


# ---------------------------------------------------------------------------
# Evidence gate


_PRICING_TERMS = ("prix", "tarif", "cout", "combien", "mensuel", "euro")
_FEE_TERMS = ("rejet", "prelevement")
_INVOICE_TERMS = ("facture", "montant", "premiere facture")


def evidence_gate(query: str, passages: list[CitedPassage]) -> list[dict]:
    """Rule-based flags over retrieved passages; never resolves conflicts.

    Rules (see docs/source_register.md):
    - archived 2024 offer passages never back a current-price answer;
    - the unresolved fee-timing tension in faq-facturation is flagged;
    - invoice-amount questions are flagged: no line items exist to infer
      individual contract terms from;
    - the roaming scan is flagged non-contractual (its own footer).
    """
    flags: list[dict] = []
    normalized = _normalize(query)
    source_ids = {passage.source_id for passage in passages}
    if "promo-rentree-2024" in source_ids and any(term in normalized for term in _PRICING_TERMS):
        flags.append({
            "code": "archived_pricing",
            "message": (
                "Passage comes from the archived 2024 offer; current general "
                "prices come from grille-tarifaire-2026. Not presented as current."
            ),
        })
    if "faq-facturation" in source_ids and any(term in normalized for term in _FEE_TERMS):
        flags.append({
            "code": "fee_timing_conflict",
            "message": (
                "faq-facturation states both an at-rejection and a J+21 next-bill "
                "timing for the 2,00 € rejection fee; unresolved, flagged not guessed."
            ),
        })
    if "faq-facturation" in source_ids and any(term in normalized for term in _INVOICE_TERMS):
        flags.append({
            "code": "invoice_line_items_absent",
            "message": (
                "The corpus has no per-invoice line items; individual amounts "
                "cannot be inferred from general causes."
            ),
        })
    if "fiche-roaming-international-scan" in source_ids:
        flags.append({
            "code": "non_contractual_source",
            "message": (
                "The roaming sheet is footer-marked 'document non contractuel' "
                "(conditions in effect 1 January 2026); limits are not contractual."
            ),
        })
    return flags


# ---------------------------------------------------------------------------
# CLI (one bounded live run, budget-gated)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m neova.retrieval",
        description="Index the public corpus or search it (OpenRouter embeddings).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index", help="Index public chunks; cache vectors by model")
    search_parser = sub.add_parser("search", help="Search the indexed public corpus")
    search_parser.add_argument("query")
    search_parser.add_argument(
        "--mode", choices=("hybrid", "semantic", "fts"), default="hybrid"
    )
    arguments = parser.parse_args(argv)

    conn = db.connect()
    try:
        if arguments.command == "index":
            report = index_corpus(conn, openrouter_embedder(), get_embedding_model())
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        outcome = search(conn, arguments.query, mode=arguments.mode)
        print(json.dumps(_outcome_payload(outcome), ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


def _outcome_payload(outcome: SearchOutcome) -> dict:
    return {
        "query": outcome.query,
        "mode": outcome.mode,
        "degraded": outcome.degraded,
        "gate_flags": outcome.gate_flags,
        "data_only": outcome.data_only,
        "results": [
            {
                "rank": passage.rank,
                "source": passage.source_id,
                "path": passage.source_path,
                "pages": f"{passage.page_start}-{passage.page_end}",
                "section": passage.section,
                "score_semantic": passage.score_semantic,
                "score_fts": passage.score_fts,
                "text": passage.text[:400],
            }
            for passage in outcome.results
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())