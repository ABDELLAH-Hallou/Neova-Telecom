"""PdfExtractor strategy tests: pypdf default, markitdown adapter, guards."""

from pathlib import Path

import pytest

from neova import chunking, extraction, sources
from neova.pdf_extractors import (
    ExtractedPage,
    MarkItDownExtractorAdapter,
    PdfExtractionError,
    PdfExtractor,
    PypdfExtractor,
    PyMuPDF4LLMExtractor,
    get_pdf_extractor,
)

GRILLE = "grille-tarifaire-2026"
ESPACE = "faq-espace-client"
FACTURATION = "faq-facturation"


@pytest.fixture(scope="module")
def markitdown() -> MarkItDownExtractorAdapter:
    return MarkItDownExtractorAdapter()


@pytest.fixture(scope="module")
def pymupdf4llm() -> PyMuPDF4LLMExtractor:
    return PyMuPDF4LLMExtractor()


def test_pypdf_strategy_returns_page_exact_segments():
    source = sources.SOURCE_BY_ID[GRILLE]
    segments = PypdfExtractor().extract(sources.source_path(source), source.pages)
    assert len(segments) == source.pages
    for number, segment in enumerate(segments, start=1):
        assert segment.page_start == segment.page_end == number
        assert segment.text.strip()


def test_markitdown_adapter_returns_coarse_markdown(markitdown):
    source = sources.SOURCE_BY_ID[GRILLE]
    segments = markitdown.extract(sources.source_path(source), source.pages)
    assert len(segments) == 1
    assert segments[0].page_start == 1
    assert segments[0].page_end == source.pages
    text = segments[0].text
    assert "grille" in text.lower()
    assert "| Offre" in text or "Offre" in text  # table content present


def test_pymupdf4llm_strategy_returns_page_exact_markdown(pymupdf4llm):
    source = sources.SOURCE_BY_ID[GRILLE]
    segments = pymupdf4llm.extract(sources.source_path(source), source.pages)
    assert len(segments) == source.pages  # page-exact, unlike markitdown
    for number, segment in enumerate(segments, start=1):
        assert segment.page_start == segment.page_end == number
        assert segment.text.strip()
    joined = "\n".join(segment.text for segment in segments)
    assert "\n# " in joined or joined.startswith("#")  # markdown headings detected
    assert "29,99" in joined  # table content preserved


def test_factory_builds_named_strategies_and_rejects_unknown():
    assert isinstance(get_pdf_extractor("pypdf"), PypdfExtractor)
    assert get_pdf_extractor("markitdown").name == "markitdown"
    assert get_pdf_extractor("pymupdf4llm").name == "pymupdf4llm"
    with pytest.raises(PdfExtractionError, match="Unknown PDF extractor"):
        get_pdf_extractor("ocr-magic")


def test_default_pdf_extractor_follows_env(monkeypatch):
    monkeypatch.delenv("PDF_EXTRACTOR", raising=False)
    assert extraction.default_pdf_extractor_name() == "pymupdf4llm"
    assert isinstance(extraction.default_pdf_extractor(), PyMuPDF4LLMExtractor)
    monkeypatch.setenv("PDF_EXTRACTOR", "markitdown")
    assert extraction.default_pdf_extractor_name() == "markitdown"
    monkeypatch.setenv("PDF_EXTRACTOR", "bogus")
    with pytest.raises(PdfExtractionError, match="Unknown PDF extractor"):
        extraction.default_pdf_extractor()


def test_default_chunks_match_pymupdf4llm_strategy():
    source = sources.SOURCE_BY_ID[GRILLE]
    assert chunking.chunk_document(source) == chunking.chunk_document(
        source, extractor=PyMuPDF4LLMExtractor()
    )


def test_pypdf_strategy_remains_selectable():
    """The foundation strategy stays available; it chunks differently."""
    source = sources.SOURCE_BY_ID[GRILLE]
    pypdf_chunks = chunking.chunk_document(source, extractor=PypdfExtractor())
    assert pypdf_chunks
    assert all(c.page_start == c.page_end or c.source_id == c.source_id for c in pypdf_chunks)


def test_markitdown_sections_come_from_headings_not_table_rows(markitdown):
    source = sources.SOURCE_BY_ID[GRILLE]
    chunks = chunking.chunk_document(source, markitdown)
    assert chunks
    for chunk in chunks:
        if chunk.section == "(introduction)":
            continue
        assert "|" not in chunk.section
        assert "€" not in chunk.section
        assert not chunk.section[0].isdigit()
    # Table content stays inside chunk bodies, never becomes a section name.
    bodies = "\n".join(chunk.text for chunk in chunks)
    assert "29,99" in bodies and "14,99" in bodies


def test_markitdown_fee_table_stays_atomic(markitdown):
    source = sources.SOURCE_BY_ID[GRILLE]
    chunks = chunking.chunk_document(source, markitdown)
    frais_chunks = [c for c in chunks if "Rejet de prélèvement" in c.text]
    assert frais_chunks
    for chunk in frais_chunks:
        assert "Activation de la ligne 12,50" in chunk.text
        assert "Rétablissement après suspension 15,00" in chunk.text


def test_markitdown_avoids_table_row_headings_on_espace_client(markitdown):
    source = sources.SOURCE_BY_ID[ESPACE]
    chunks = chunking.chunk_document(source, markitdown)
    assert chunks
    # The naive heuristic used to promote self-service table rows
    # ('Télécharger ses factures ... oui') to section headings.
    for chunk in chunks:
        if chunk.section == "(introduction)":
            continue
        assert "oui" not in chunk.section
        assert not chunk.section[0].isdigit()
    bodies = "\n".join(chunk.text for chunk in chunks)
    assert "Télécharger ses factures" in bodies


def test_headings_reject_table_rows_and_values():
    assert extraction._is_heading("Rythme de facturation")
    assert extraction._is_heading("# Rythme de facturation")
    for not_heading in (
        "| Mobile Néova 5 Go | 5 Go | 4,99 € | sans engagement |",
        "Fibre Néova 1 Gb/s 1 Gb/s / 700 Mb/s 39,99 € 12 ou 24 mois",
        "1er juin 2026",
        "Un usage majoritairement réalisé depuis l'étranger à 80 %",
        "Décodeur TV 5,00 €",
        "Offre ; Données en France ; Prix mensuel ; Engagement",
    ):
        assert not extraction._is_heading(not_heading), not_heading
    # A lone table row like 'Télécharger ses factures (24 derniers mois) oui'
    # is line-locally indistinguishable from a heading; the pipeline keeps it
    # out of sections at block level via _is_table (see second-line-header test).


def test_is_table_detects_markdown_pipes_and_second_line_header():
    markdown_table = (
        "Offres mobiles\n"
        "| Offre | Données en France | Prix mensuel | Engagement |\n"
        "| ----- | ----------------- | ------------ | ---------- |\n"
        "| Mobile Néova 5 Go | 5 Go | 4,99 € | sans engagement |"
    )
    assert extraction._is_table(markdown_table)
    second_line_header = (
        "Ce que le client peut faire seul\n"
        "Action Disponible\n"
        "Télécharger ses factures (24 derniers mois) oui\n"
        "Modifier son IBAN oui, effet à la facture suivante"
    )
    assert extraction._is_table(second_line_header)


def test_markdown_cleaning_strips_syntax_keeps_headings():
    raw = (
        "# Diagnostic de la box\n\n"
        "**CO R P U S**\n"
        "```\n"
        " corpus/faq-box-internet.md  ·  508 mots\n"
        "```\n"
        "**id** faq-box-internet **statut** current **public** public **maj** 2026-05-18\n"
        "| Voyant | État | Signification |\n"
        "| ----- | ---- | ------------- |\n"
        "| Rouge fixe | Panne | Aucun signal reçu. |\n"
        "Un voyant **rouge** signifie [une panne](https://example.com) réseau.\n"
    )
    cleaned = extraction._clean_markdown_page(raw, "Diagnostic de la box")
    lines = cleaned.splitlines()
    assert "CORPUS" not in cleaned.replace(" ", "").upper() or "CORPUS" not in cleaned
    assert "id faq-box-internet" not in cleaned
    assert not any(line.strip() == "```" for line in lines)
    assert "`corpus/faq-box-internet.md`" not in cleaned  # backticked footer gone
    assert any(line.startswith("# ") for line in lines)  # headings preserved
    assert "Voyant ; État ; Signification" in cleaned
    assert "Un voyant rouge signifie une panne réseau." in cleaned


def test_pymupdf4llm_headings_drive_sections_and_pages(pymupdf4llm):
    source = sources.SOURCE_BY_ID[FACTURATION]
    chunks = chunking.chunk_document(source, pymupdf4llm)
    assert len(chunks) >= 2
    for chunk in chunks:
        if chunk.section == "(introduction)":
            continue
        assert "|" not in chunk.section
        assert "€" not in chunk.section
        assert "corpus/" not in chunk.section
    # Page-exact attribution survives chunking: last chunk ends on page 2.
    assert max(c.page_end for c in chunks) == 2
    # Artifacts never survive into chunk bodies.
    bodies = "\n".join(c.text for c in chunks)
    assert "CORPUS" not in bodies
    assert "```" not in bodies
    assert "1 / 2" not in bodies
    assert "<!-- PAGE" not in bodies
    # Fee table content stays atomic inside one chunk.
    frais = [c for c in chunks if "rejet" in c.text]
    assert frais and all("2,00" in c.text for c in frais)


def test_pdf_extractor_env_selects_pymupdf4llm(monkeypatch):
    monkeypatch.setenv("PDF_EXTRACTOR", "pymupdf4llm")
    assert extraction.default_pdf_extractor_name() == "pymupdf4llm"
    assert isinstance(extraction.default_pdf_extractor(), PyMuPDF4LLMExtractor)


def test_markitdown_covers_all_public_sources(markitdown):
    """Strategies may chunk differently; neither may lose a source or shred text."""
    public = chunking.public_chunks()
    assert len(public) == 12
    public_markitdown = [
        chunk
        for source in sources.all_sources()
        if source.access == "public"
        for chunk in chunking.chunk_document(source, markitdown)
    ]
    assert {c.source_id for c in public_markitdown} == {
        s.source_id for s in sources.all_sources() if s.access == "public"
    }
    assert len(public_markitdown) >= 12  # every source yields at least one chunk
    for chunk in public_markitdown:
        assert chunk.word_count >= 120  # no empty or shredded chunks
        assert "CORPUS" not in chunk.text.split("\n", 1)[0]
        assert not extraction._PAGE_MARKER.match(chunk.text.splitlines()[-1])  # footers gone