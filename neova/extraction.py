"""Text extraction and cleaning for the reviewed corpus sources.

This module owns everything between the raw strategy output and clean,
page-attributed text: strategy selection (``PDF_EXTRACTOR`` env, default
``pymupdf4llm``), artifact stripping (banners, footers, navigation,
markdown fences, page separators), markdown normalization, document title
detection and the heading/table classifiers that drive chunking. Chunking
itself lives in ``neova.chunking``.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from .pdf_extractors import (
    ExtractedPage,
    PdfExtractionError,
    PdfExtractor,
    PypdfExtractor,
    get_pdf_extractor,
)
from .sources import CorpusError, ROAMING_TRANSCRIPTION_PATH

# --- Extraction strategy selection -----------------------------------------

DEFAULT_EXTRACTOR_NAME = "pymupdf4llm"  # best fit: page-exact + markdown headings
_ENV_EXTRACTOR = "PDF_EXTRACTOR"


def default_pdf_extractor_name() -> str:
    """Strategy name for this process; ``PDF_EXTRACTOR`` overrides the default."""
    name = (os.environ.get(_ENV_EXTRACTOR) or DEFAULT_EXTRACTOR_NAME).strip().lower()
    return name or DEFAULT_EXTRACTOR_NAME


@lru_cache(maxsize=4)
def _cached_extractor(name: str) -> PdfExtractor:
    """One strategy instance per name: the markitdown converter is expensive."""
    return get_pdf_extractor(name)


def default_pdf_extractor() -> PdfExtractor:
    return _cached_extractor(default_pdf_extractor_name())


def extract_pdf_pages(pdf_path: Path, expected_pages: int | None = None,
                      extractor: PdfExtractor | None = None) -> list[ExtractedPage]:
    """Extract one original PDF with a strategy and clean each segment.

    Returns cleaned segments with page attribution; raises
    :class:`CorpusError` when the strategy fails or coverage is incomplete.
    """
    strategy = extractor or PypdfExtractor()
    try:
        raw_segments = strategy.extract(pdf_path, expected_pages or 1)
    except PdfExtractionError as error:
        raise CorpusError(str(error)) from None
    if expected_pages is not None:
        covered = sum(seg.page_end - seg.page_start + 1 for seg in raw_segments)
        if covered != expected_pages:
            raise CorpusError(
                f"{pdf_path.name}: expected {expected_pages} pages, strategy covered {covered}"
            )
    title = _doc_title(raw_segments[0].text)
    cleaned: list[ExtractedPage] = []
    for segment in raw_segments:
        if strategy.markdown:
            page_text = _clean_markdown_page(segment.text, title)
        else:
            page_text = _clean_page(segment.text, title)
        if not page_text.strip():
            raise CorpusError(
                f"{pdf_path.name} pages {segment.page_start}-{segment.page_end}: "
                "no text after cleaning"
            )
        cleaned.append(ExtractedPage(segment.page_start, segment.page_end, page_text))
    return cleaned


def extract_pdf_page_texts(pdf_path: Path) -> list[str]:
    """Per-page cleaned text via pypdf (foundation behavior, unchanged)."""
    return [segment.text for segment in extract_pdf_pages(pdf_path, None, PypdfExtractor())]


def roaming_pages() -> list[str]:
    """Return the reviewed PNG transcription as one page (no OCR at runtime)."""
    return [ROAMING_TRANSCRIPTION_PATH.read_text(encoding="utf-8").strip()]


_BULLET_LINE = re.compile(r"^(?:[•·]|\d{1,2}\.)\s*$")
_PAGE_MARKER = re.compile(r"^\d{1,3}\s*/\s*\d{1,3}$")  # bare '1 / 2' page footers
_HTML_COMMENT = re.compile(r"^<!--.*-->$")  # e.g. <!-- PAGE 1 --> separators


def _is_artifact(line: str, title: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if set(stripped) <= {"`", "~", " "}:
        return True  # markdown code-fence delimiters (pymupdf4llm output)
    if _HTML_COMMENT.match(stripped):
        return True  # <!-- PAGE N --> page separators
    stripped = stripped.replace("`", "")  # inline-code markup never matters
    if stripped.replace(" ", "").upper() == "CORPUS":
        return True  # banner, including bold letter-spaced variants
    if stripped.startswith("Accueil >") or "cookies pour améliorer" in stripped:
        return True
    if stripped.startswith("© Néova Télécom"):
        return True
    if stripped.startswith("corpus/") and ".md" in stripped:
        return True  # source label, word-count line or page footer
    if _PAGE_MARKER.match(stripped):
        return True  # detached page number footers (markitdown output)
    if stripped.startswith("id ") and " statut " in stripped and " maj " in stripped:
        return True  # metadata line
    if title and stripped == title:
        return True  # repeated document title used as a page footer
    if _BULLET_LINE.match(stripped):
        return True  # detached list markers rendered at page end
    return False


def _doc_title(first_page: str) -> str:
    """Document title, fitting the pymupdf4llm markdown output first.

    An explicit markdown H1 (``# ``) is authoritative when present;
    otherwise the first non-artifact content line wins (plain-text
    strategies). Bold and heading markers are stripped either way so the
    title still matches repeated footer lines for deduplication.
    """
    for line in first_page.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return _strip_title_markup(stripped)
    for line in first_page.splitlines():
        stripped = line.strip()
        if not stripped or _is_artifact(stripped, ""):
            continue
        if stripped.startswith("id ") or stripped.startswith("source "):
            continue
        return _strip_title_markup(stripped)
    raise CorpusError("No title line found on first page")


def _strip_title_markup(line: str) -> str:
    """Remove heading, bold and inline-code markup from a title candidate."""
    return line.lstrip("#").replace("**", "").replace("`", "").strip()


def _clean_page(text: str, title: str) -> str:
    kept = [line.rstrip() for line in text.splitlines() if not _is_artifact(line, title)]
    return "\n".join(kept).strip()


_MD_TABLE_SEPARATOR = re.compile(r"^\|?[\s:|-]+\|?\s*$")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC = re.compile(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])")
_MD_LINK = re.compile(r"\[([^\]\n]+)\]\([^)]*\)")


def _normalize_markdown_line(line: str) -> str:
    """Drop table separators, strip emphasis and links; inline table cells."""
    line = _MD_LINK.sub(r"\1", line)
    line = _MD_BOLD.sub(r"\1", line)
    line = _MD_ITALIC.sub(r"\1", line)
    stripped = line.strip()
    if stripped.startswith("|"):
        if _MD_TABLE_SEPARATOR.match(stripped):
            return ""
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        return " ; ".join(cell for cell in cells if cell)
    return line


def _clean_markdown_page(text: str, title: str) -> str:
    """Clean markdown extraction output: artifacts plus syntax normalization.

    Headings keep their ``#`` markers (authoritative section boundaries for
    chunking); table rows stay pipe-joined so ``_is_table`` can see them
    and ``_is_heading`` can reject them.
    """
    kept = []
    for line in text.splitlines():
        line = _normalize_markdown_line(line)
        if line and _is_artifact(line, title):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def _is_heading(block: str) -> bool:
    stripped = block.strip()
    if "\n" in stripped:
        return False
    if stripped.startswith("#"):
        return True  # explicit markdown heading: authoritative
    words = stripped.split()
    if not 1 <= len(words) <= 8 or len(stripped) > 70:
        return False
    if stripped[-1] in ".!?,;:":
        return False
    if stripped.startswith(("|", "-", "*", ">")) or " | " in stripped:
        return False  # markdown table row or list/quote markup
    if "€" in stripped or "%" in stripped:
        return False  # prices and percentages belong to tables, never headings
    if stripped[0].isdigit():
        return False  # numbered rows such as '1er juin 2026' are not headings
    return True


_TABLE_HEADERS = ("Offre", "Frais", "Option", "Consommation", "Action", "Voyant", "Situation")
_TABLE_VALUE_WORDS = ("Prix", "Tarif", "Montant", "Disponible", "Signification", "Frais")


def _is_table(block: str) -> bool:
    lines = [line for line in block.splitlines() if line.strip()]
    if sum(1 for line in lines if "€" in line) >= 2:
        return True
    if sum(1 for line in lines if line.strip().startswith("|")) >= 2:
        return True  # explicit markdown table
    for line in lines[:2]:  # header row may be second after a section title
        first = line.strip()
        if first.startswith(_TABLE_HEADERS) and any(
            word in block for word in _TABLE_VALUE_WORDS
        ):
            return True
    return False