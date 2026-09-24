"""Offline issue #4 retrieval tests: mocked embeddings, temporary databases.

No network calls: embeddings are injected fakes; the live client is only
checked for fail-closed configuration errors.
"""

import hashlib
import json
import re
import sqlite3
from pathlib import Path

import pytest

from neova import chunking, db, extraction, retrieval, sources
from neova.config import ConfigurationError
from neova.embeddings import BudgetExceeded, EmbeddingError, cosine

INTERNAL_SOURCE_IDS = ("politique-geste-commercial", "procedure-escalade-n2")

# Deterministic fake embedding space: normalized bag-of-words over a fixed
# vocabulary, with a synonym map so French paraphrases rank close.
VOCAB = (
    "resiliation", "resilier", "abonnement", "frais", "rejet", "prelevement",
    "facture", "montant", "prix", "forfait", "roaming", "etranger", "donnee",
    "technicien", "box", "retour", "equipement", "demenagement", "adresse",
    "conseiller", "banque", "activation", "grille", "archive", "go",
)
SYNONYMS = {"arreter": "resiliation", "stopper": "resiliation", "tarif": "prix"}


class FakeEmbedder:
    """Counts calls; optionally fails to simulate an embeddings outage."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise EmbeddingError("simulated embeddings outage")
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            counts = dict.fromkeys(VOCAB, 0)
            for token in re.findall(r"\w+", retrieval._normalize(text)):
                key = SYNONYMS.get(token, token)
                if key in counts:
                    counts[key] += 1
            vectors.append([counts[word] for word in VOCAB])
        return vectors


CHUNK_A = (
    "Résiliation du contrat : le client peut résilier son abonnement à tout moment. "
    "Le préavis est de 10 jours et la dernière facture est calculée au prorata."
)
CHUNK_B = (
    "Frais de rejet de prélèvement : 2,00 €. Appliqués lorsque le prélèvement est "
    "refusé par la banque (provision insuffisante, opposition, compte clôturé)."
)
CHUNK_PROMO = (
    "Tarifs promotionnels de l'offre Rentrée 2024 : Fibre Néova 500 Mb/s à 19,99 € "
    "la première année. Ce document est conservé à titre d'archive ; les tarifs ne "
    "doivent pas être communiqués comme tarifs en vigueur."
)
CHUNK_ROAMING = (
    "Données utilisables depuis l'UE : Mobile Néova 80 Go : 25 Go. Document non "
    "contractuel établi sur la base des conditions générales en vigueur au 1er janvier."
)


def insert_chunk(conn: sqlite3.Connection, chunk_hash: str, source_id: str,
                 source_path: str, text: str, access: str = "public",
                 page_start: int = 1) -> None:
    conn.execute(
        "INSERT INTO chunks (chunk_hash, source_id, source_path, page_start, page_end, "
        "section, access, word_count, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chunk_hash, source_id, source_path, page_start, page_start, source_id, access,
         len(text.split()), text),
    )
    conn.execute(
        "INSERT INTO chunk_fts (text, source_id, chunk_hash) VALUES (?, ?, ?)",
        (text, source_id, chunk_hash),
    )
    conn.commit()


def store_vector(conn: sqlite3.Connection, chunk_hash: str, model: str,
                 vector: list[float]) -> None:
    from neova.embeddings import vector_to_blob

    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO chunk_vectors (chunk_hash, model, dim, vector, created_at) "
            "VALUES (?, ?, ?, ?, '2026-09-23T00:00:00+00:00')",
            (chunk_hash, model, len(vector), vector_to_blob(vector)),
        )


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(str(tmp_path / "retrieval.db"))
    db.init_retrieval_schema(connection)
    yield connection
    connection.close()


def test_index_covers_public_sources_and_excludes_internal(conn):
    report = retrieval.index_corpus(conn, FakeEmbedder(), "fake-model")
    assert report["chunks"] == len(chunking.public_chunks())
    assert report["state"] == "complete"
    assert report["vectors"] == report["chunks"]
    indexed_sources = {row[0] for row in conn.execute("SELECT source_id FROM chunks")}
    assert indexed_sources == {source.source_id for source in sources.SOURCES
                               if source.access == "public"}
    assert not (indexed_sources & set(INTERNAL_SOURCE_IDS))
    rows = conn.execute(
        "SELECT source_id FROM chunks WHERE source_id IN (?, ?)",
        INTERNAL_SOURCE_IDS,
    ).fetchall()
    assert rows == []
    assert conn.execute("SELECT COUNT(*) FROM chunk_fts").fetchone()[0] == report["chunks"]


def test_index_is_deterministic_and_reuses_cache(conn):
    first = retrieval.index_corpus(conn, FakeEmbedder(), "fake-model")
    assert first["state"] == "complete" and first["embedded"] == first["chunks"]
    cached = FakeEmbedder()
    second = retrieval.index_corpus(conn, cached, "fake-model")
    assert second["state"] == "complete"
    assert second["reason"] == "all vectors reused from cache"
    assert second["embedded"] == 0 and cached.calls == []


def test_model_change_reembeds_and_keeps_old_cache(conn):
    first = retrieval.index_corpus(conn, FakeEmbedder(), "model-a")
    assert first["vectors"] == first["chunks"]
    changed = retrieval.index_corpus(conn, FakeEmbedder(), "model-b")
    assert changed["state"] == "complete"
    assert changed["model"] == "model-b"
    assert conn.execute(
        "SELECT COUNT(*) FROM chunk_vectors WHERE model = 'model-a'"
    ).fetchone()[0] == first["chunks"]
    assert db.get_meta(conn, retrieval.INDEX_MODEL_KEY) == "model-b"


def test_embedding_outage_degrades_visible_not_silent(conn):
    report = retrieval.index_corpus(conn, FakeEmbedder(fail=True), "fake-model")
    assert report["state"] == "degraded"
    assert "embedding_unavailable" in report["reason"]
    assert report["chunks"] > 0 and report["vectors"] == 0
    assert db.get_meta(conn, retrieval.INDEX_STATE_KEY) == "degraded"
    outcome = retrieval.search(conn, "résiliation", embed_fn=FakeEmbedder(fail=True),
                               model="fake-model", mode="hybrid")
    assert retrieval.DEGRADED_QUERY_EMBEDDING in outcome.degraded
    assert outcome.results  # FTS5 fallback still returns real passages
    semantic = retrieval.search(conn, "résiliation", embed_fn=FakeEmbedder(fail=True),
                                model="fake-model", mode="semantic")
    assert semantic.results == []
    assert retrieval.DEGRADED_QUERY_EMBEDDING in semantic.degraded


def test_incomplete_indexing_is_flagged(conn):
    retrieval.index_corpus(conn, FakeEmbedder(fail=True), "fake-model")
    outcome = retrieval.search(conn, "frais de rejet", embed_fn=FakeEmbedder(),
                               model="fake-model", mode="semantic")
    assert retrieval.DEGRADED_INDEX_INCOMPLETE in outcome.degraded
    assert outcome.results == []


def test_empty_index_degrades(conn):
    outcome = retrieval.search(conn, "bonjour", embed_fn=FakeEmbedder(), model="fake-model")
    assert retrieval.DEGRADED_INDEX_EMPTY in outcome.degraded
    assert outcome.results == []


def test_internal_content_never_reachable(conn):
    internal_hash = chunking.chunk_hash_for("politique-geste-commercial", 1, "geste")
    insert_chunk(conn, internal_hash, "politique-geste-commercial",
                 "politique-geste-commercial.pdf", "geste", access="internal")
    from neova.embeddings import vector_to_blob
    store_vector(conn, internal_hash, "fake-model", [1.0] * len(VOCAB))
    outcome = retrieval.search(conn, "geste commercial seuil",
                               embed_fn=FakeEmbedder(), model="fake-model")
    assert outcome.results == []  # internal chunk is filtered out of every path


def test_synthetic_modes_are_comparable(conn):
    hash_a = chunking.chunk_hash_for("cgv-resiliation", 1, CHUNK_A)
    hash_b = chunking.chunk_hash_for("faq-facturation", 1, CHUNK_B)
    insert_chunk(conn, hash_a, "cgv-resiliation", "cgv-resiliation.pdf", CHUNK_A)
    insert_chunk(conn, hash_b, "faq-facturation", "faq-facturation.pdf", CHUNK_B)
    store_vector(conn, hash_a, "fake-model", FakeEmbedder()([CHUNK_A])[0])
    store_vector(conn, hash_b, "fake-model", FakeEmbedder()([CHUNK_B])[0])

    paraphrase = "Je veux arrêter mon forfait"
    semantic = retrieval.search(conn, paraphrase, embed_fn=FakeEmbedder(),
                                model="fake-model", mode="semantic")
    assert [p.chunk_hash for p in semantic.results] == [hash_a]  # synonym match
    fts = retrieval.search(conn, paraphrase, embed_fn=FakeEmbedder(),
                           model="fake-model", mode="fts")
    assert fts.results == []  # no exact terms: FTS5 claims nothing
    hybrid = retrieval.search(conn, paraphrase, embed_fn=FakeEmbedder(),
                              model="fake-model", mode="hybrid")
    assert [p.chunk_hash for p in hybrid.results] == [hash_a]

    exact = "frais de rejet de prélèvement 2,00 €"
    semantic_exact = retrieval.search(conn, exact, embed_fn=FakeEmbedder(),
                                      model="fake-model", mode="semantic")
    assert semantic_exact.results[0].chunk_hash == hash_b
    fts_exact = retrieval.search(conn, exact, embed_fn=FakeEmbedder(),
                                 model="fake-model", mode="fts")
    assert fts_exact.results[0].chunk_hash == hash_b
    hybrid_exact = retrieval.search(conn, exact, embed_fn=FakeEmbedder(),
                                    model="fake-model", mode="hybrid")
    assert hybrid_exact.results[0].chunk_hash == hash_b
    assert len(hybrid_exact.results) <= retrieval.MAX_RESULTS
    hashes = [p.chunk_hash for p in hybrid_exact.results]
    assert len(hashes) == len(set(hashes))  # deduplicated


def test_evidence_gate_flags(conn):
    hash_promo = chunking.chunk_hash_for("promo-rentree-2024", 1, CHUNK_PROMO)
    hash_roaming = chunking.chunk_hash_for("fiche-roaming-international-scan", 1, CHUNK_ROAMING)
    insert_chunk(conn, hash_promo, "promo-rentree-2024", "promo-rentree-2024.pdf", CHUNK_PROMO)
    insert_chunk(conn, hash_roaming, "fiche-roaming-international-scan",
                 "fiche-roaming-international-scan.png", CHUNK_ROAMING)
    store_vector(conn, hash_promo, "fake-model", FakeEmbedder()([CHUNK_PROMO])[0])
    store_vector(conn, hash_roaming, "fake-model", FakeEmbedder()([CHUNK_ROAMING])[0])

    pricing = retrieval.search(conn, "quel est le prix fibre 500 Mb/s",
                               embed_fn=FakeEmbedder(), model="fake-model")
    codes = {flag["code"] for flag in pricing.gate_flags}
    assert "archived_pricing" in codes  # archived offer never backs current prices
    roaming = retrieval.search(conn, "données roaming depuis l'étranger",
                               embed_fn=FakeEmbedder(), model="fake-model")
    assert any(flag["code"] == "non_contractual_source" for flag in roaming.gate_flags)
    neutral = retrieval.search(conn, "comment retourner la box",
                               embed_fn=FakeEmbedder(), model="fake-model")
    assert neutral.gate_flags == [] or all(
        flag["code"] != "archived_pricing" for flag in neutral.gate_flags
    )


def test_fee_timing_and_invoice_flags():
    fee_passage = _cited_passage("faq-facturation")
    flags = retrieval.evidence_gate(
        "pourquoi des frais de rejet sur ma facture", [fee_passage]
    )
    assert any(flag["code"] == "fee_timing_conflict" for flag in flags)
    assert any(flag["code"] == "invoice_line_items_absent" for flag in flags)


def _cited_passage(source_id: str) -> retrieval.CitedPassage:
    return retrieval.CitedPassage(
        chunk_hash="x", source_id=source_id, source_path=f"{source_id}.pdf",
        page_start=1, page_end=1, section="s", text="t", rank=1,
    )


def test_real_corpus_public_only_and_chunk_quality():
    public = chunking.public_chunks()
    all_chunks = chunking.iter_chunks()
    assert {c.source_id for c in all_chunks} == {s.source_id for s in sources.SOURCES}
    assert len(all_chunks) == 14 and len(public) == 12
    for chunk in all_chunks:
        lowered = chunk.text.lower()
        assert "corpus/" not in lowered
        assert "statut" not in lowered or "maj" not in lowered
        assert not chunk.text.startswith("CORPUS")
        assert " mots" not in lowered
        assert "<!-- page" not in lowered
        if chunk.access == "public":
            assert chunk.source_id not in INTERNAL_SOURCE_IDS
    assert chunking.chunk_hash_for("s", 1, "t") == chunking.chunk_hash_for("s", 1, "t")


def test_fee_table_and_notes_stay_together():
    grille = next(c for c in chunking.public_chunks() if c.source_id == "grille-tarifaire-2026")
    assert "Activation de la ligne" in grille.text
    assert "Rétablissement après suspension 15,00 €" in grille.text
    promo = next(c for c in chunking.public_chunks() if c.source_id == "promo-rentree-2024")
    assert "19,99" in promo.text and "ne doivent pas être communiqués" in promo.text


def test_roaming_chunk_from_reviewed_transcription():
    roaming = next(c for c in chunking.public_chunks()
                   if c.source_id == "fiche-roaming-international-scan")
    assert "FIC-ROAM-2026-02" in roaming.text
    assert "25 Go" in roaming.text and "0,50 €" in roaming.text
    assert "non contractuel" in roaming.text


def test_foundation_database_gains_retrieval_tables(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'neova.db'}")
    db.init_db()
    with db.connect(str(tmp_path / "neova.db")) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','index')"
        )}
        assert {"chunks", "chunk_vectors", "sources"} <= tables
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'chunk_fts'"
        ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Sources manifest (source of truth) and runtime sources table


def _manifest_entry(source: sources.SourceMeta) -> dict:
    entry = {
        "source_id": source.source_id,
        "path": source.path,
        "status": source.status,
        "access": source.access,
        "updated": source.updated,
        "pages": source.pages,
        "non_contractual": source.non_contractual,
    }
    if source.path.endswith(".png"):
        entry["transcription"] = "fiche-roaming-international-scan.txt"
        entry["sha256"] = "0" * 64
    return entry


def _write_manifest(tmp_path: Path, entries: list[dict], **overrides) -> Path:
    payload = {"version": 1, "reviewed": "2026-09-23", "sources": entries, **overrides}
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _valid_entry(tmp_path: Path) -> dict:
    return _manifest_entry(sources.SourceMeta(
        "sample", "sample.pdf", "current", "public", "2026-01-01", 1
    ))


def test_manifest_loads_and_converts_to_source_meta():
    payload = sources.load_sources_manifest()
    manifest_sources = sources.all_sources()
    assert payload["version"] == 1
    assert len(manifest_sources) == len(payload["sources"]) == 11
    assert all(isinstance(source, sources.SourceMeta) for source in manifest_sources)
    by_id = {source.source_id: source for source in manifest_sources}
    assert by_id["politique-geste-commercial"].access == "internal"
    assert by_id["promo-rentree-2024"].status == "deprecated"
    roaming = by_id["fiche-roaming-international-scan"]
    assert roaming.non_contractual is True
    assert sources.SOURCE_BY_ID == by_id  # module-level view stays in sync
    assert sources.SOURCES == manifest_sources


def test_manifest_entries_are_unique_and_files_exist():
    payload = sources.load_sources_manifest()
    ids = [entry["source_id"] for entry in payload["sources"]]
    paths = [entry["path"] for entry in payload["sources"]]
    assert len(ids) == len(set(ids))
    assert len(paths) == len(set(paths))
    for entry in payload["sources"]:
        original = sources.CORPUS_DIR / entry["path"]
        assert original.is_file(), f"missing corpus file: {entry['path']}"
        assert entry["path"] == Path(entry["path"]).name  # no directory escape


def test_manifest_png_hash_matches_corpus_image():
    payload = sources.load_sources_manifest()
    png_entry = next(e for e in payload["sources"]
                     if e["source_id"] == "fiche-roaming-international-scan")
    actual = hashlib.sha256((sources.CORPUS_DIR / png_entry["path"]).read_bytes()).hexdigest()
    assert actual == png_entry["sha256"]
    transcription = sources.RESOURCES_DIR / png_entry["transcription"]
    assert transcription.is_file()
    assert "FIC-ROAM-2026-02" in transcription.read_text(encoding="utf-8")
    assert sources.SOURCE_BY_ID["fiche-roaming-international-scan"].non_contractual is True


def test_manifest_rejects_duplicate_source_id(tmp_path):
    entries = [_valid_entry(tmp_path), _valid_entry(tmp_path)]
    path = _write_manifest(tmp_path, entries)
    with pytest.raises(sources.CorpusError, match="Duplicate source_id"):
        sources.load_sources_manifest(path)


def test_manifest_rejects_duplicate_path(tmp_path):
    first = _valid_entry(tmp_path)
    second = _manifest_entry(sources.SourceMeta(
        "other", "sample.pdf", "current", "public", "2026-01-02", 1
    ))
    path = _write_manifest(tmp_path, [first, second])
    with pytest.raises(sources.CorpusError, match="Duplicate source path"):
        sources.load_sources_manifest(path)


def test_manifest_rejects_bad_status_access_date_pages_flags(tmp_path):
    base = _valid_entry(tmp_path)
    for field, value, message in (
        ("status", "archived", "invalid status"),
        ("access", "customer", "invalid access"),
        ("updated", "01-2026", "invalid updated"),
        ("pages", 0, "invalid pages"),
        ("pages", True, "invalid pages"),
        ("non_contractual", "yes", "invalid non_contractual"),
    ):
        entry = dict(base)
        entry[field] = value
        path = _write_manifest(tmp_path, [entry])
        with pytest.raises(sources.CorpusError, match=message):
            sources.load_sources_manifest(path)


def test_manifest_rejects_missing_transcription(tmp_path):
    png_entry = _manifest_entry(sources.SourceMeta(
        "scan", "scan.png", "current", "public", "2026-01-01", 1, True
    ))
    png_entry["transcription"] = "does-not-exist.txt"
    png_entry["sha256"] = "0" * 64
    path = _write_manifest(tmp_path, [png_entry])
    with pytest.raises(sources.CorpusError, match="missing transcription"):
        sources.load_sources_manifest(path)


def test_manifest_rejects_missing_hash(tmp_path):
    png_entry = _manifest_entry(sources.SourceMeta(
        "fiche-roaming-international-scan", "fiche-roaming-international-scan.png",
        "current", "public", "2026-01-01", 1, True
    ))
    png_entry["transcription"] = "fiche-roaming-international-scan.txt"
    png_entry.pop("sha256")  # absent key must fail the format check
    path = _write_manifest(tmp_path, [png_entry])
    with pytest.raises(sources.CorpusError, match="sha256"):
        sources.load_sources_manifest(path)
    png_entry["sha256"] = "not-a-hash"  # wrong format fails the same check
    path = _write_manifest(tmp_path, [png_entry])
    with pytest.raises(sources.CorpusError, match="sha256"):
        sources.load_sources_manifest(path)


def test_manifest_rejects_wrong_png_hash(tmp_path):
    png_entry = _manifest_entry(sources.SourceMeta(
        "fiche-roaming-international-scan", "fiche-roaming-international-scan.png",
        "current", "public", "2026-01-01", 1, True
    ))
    png_entry["transcription"] = "fiche-roaming-international-scan.txt"
    png_entry["sha256"] = "0" * 64  # well-formed but not the reviewed image
    path = _write_manifest(tmp_path, [png_entry])
    with pytest.raises(sources.CorpusError, match="hash mismatch"):
        sources.load_sources_manifest(path)


def test_manifest_rejects_non_png_non_contractual(tmp_path):
    entry = _valid_entry(tmp_path)
    entry["non_contractual"] = True
    path = _write_manifest(tmp_path, [entry])
    with pytest.raises(sources.CorpusError, match="non_contractual"):
        sources.load_sources_manifest(path)


def test_manifest_rejects_invalid_json_and_version(tmp_path):
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(sources.CorpusError, match="not valid JSON"):
        sources.load_sources_manifest(bad_json)
    missing = tmp_path / "missing.json"
    with pytest.raises(sources.CorpusError, match="not found"):
        sources.load_sources_manifest(missing)
    path = _write_manifest(tmp_path, [_valid_entry(tmp_path)], version=99)
    with pytest.raises(sources.CorpusError, match="version"):
        sources.load_sources_manifest(path)


def test_sources_table_syncs_from_manifest(conn):
    db.sync_sources_table(conn)
    rows = conn.execute(
        "SELECT source_id, path, status, access, updated, pages, non_contractual, reviewed "
        "FROM sources ORDER BY source_id"
    ).fetchall()
    manifest_rows = sorted(
        (
            source.source_id, source.path, source.status, source.access,
            source.updated, source.pages, int(source.non_contractual), "2026-09-23",
        )
        for source in sources.all_sources()
    )
    assert rows == manifest_rows
    assert len(rows) == 11
    # Idempotent: a second sync changes nothing.
    db.sync_sources_table(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM sources"
    ).fetchone()[0] == 11


def test_sources_table_prunes_removed_entries(conn):
    db.sync_sources_table(conn)
    with conn:
        conn.execute(
            "INSERT INTO sources (source_id, path, status, access, updated, pages, "
            "non_contractual, reviewed) VALUES ('ghost', 'ghost.pdf', 'current', "
            "'public', '2026-01-01', 1, 0, '2026-09-23')"
        )
    db.sync_sources_table(conn)
    assert conn.execute(
        "SELECT 1 FROM sources WHERE source_id = 'ghost'"
    ).fetchone() is None


def test_indexing_populates_sources_table(conn):
    report = retrieval.index_corpus(conn, FakeEmbedder(), "fake-model")
    assert report["state"] == "complete"
    assert conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 11
    internal_rows = conn.execute(
        "SELECT source_id FROM sources WHERE access = 'internal'"
    ).fetchall()
    assert {row[0] for row in internal_rows} == set(INTERNAL_SOURCE_IDS)
    # Routing-only sources stay in the metadata table but never in chunks.
    assert conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE access = 'internal'"
    ).fetchone()[0] == 0


def test_meta_helpers(conn):
    assert db.get_meta(conn, "missing", "default") == "default"
    db.set_meta(conn, "retrieval_index_state", "degraded")
    db.set_meta(conn, "retrieval_index_state", "complete")
    assert db.get_meta(conn, "retrieval_index_state") == "complete"


def test_live_embedder_fails_closed_without_configuration(monkeypatch):
    from neova import embeddings

    for name in ("OPENROUTER_API_KEY", "OPENROUTER_BASE_URL", "EMBEDDING_MODEL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigurationError):
        embeddings.openrouter_embedder()


def test_budget_gate_stops_optional_live_calls(monkeypatch):
    from neova import embeddings

    monkeypatch.setattr(embeddings, "key_status",
                        lambda: {"usage": 8.5, "limit": 10.0, "limit_remaining": 0.4})
    with pytest.raises(BudgetExceeded):
        embeddings.ensure_budget(1.0)
    monkeypatch.setattr(embeddings, "key_status",
                        lambda: {"usage": 2.0, "limit": 10.0, "limit_remaining": 8.0})
    assert embeddings.ensure_budget(1.0)["limit_remaining"] == 8.0


def test_cosine_and_blob_roundtrip():
    from neova.embeddings import blob_to_vector, vector_to_blob

    blob = vector_to_blob([0.0, 1.0, 2.5])
    assert blob_to_vector(blob) == [0.0, 1.0, 2.5]
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    with pytest.raises(EmbeddingError):
        cosine([1.0], [1.0, 2.0])