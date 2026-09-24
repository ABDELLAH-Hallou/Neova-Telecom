"""Strategy pattern for original-PDF text extraction.

``PdfExtractor`` is the strategy interface. ``PypdfExtractor`` keeps the
foundation behavior: one raw text segment per PDF page. 
``MarkItDownExtractorAdapter`` wraps microsoft/markitdown and yields the
whole document as markdown-oriented text (tables as pipe rows); its page
attribution is intentionally coarse (one segment spanning all expected
pages) because markitdown does not preserve page boundaries.

Both strategies return raw, uncleaned text: artifact stripping and
markdown normalization stay in ``neova.corpus`` so chunking rules remain
in one place. Selection is explicit per call; the process default comes
from the ``PDF_EXTRACTOR`` environment variable (``pymupdf4llm`` when
unset, following the evaluation in ``output/pymupdf4llm/``).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


class PdfExtractionError(RuntimeError):
    """Raised when a strategy cannot extract the original PDF as reviewed."""


@dataclass(frozen=True)
class ExtractedPage:
    """One raw extraction segment with its page attribution."""

    page_start: int
    page_end: int
    text: str


class PdfExtractor(abc.ABC):
    """Strategy interface: extract raw text segments from an original PDF."""

    name: str = "abstract"
    #: Whether the strategy yields markdown syntax (headings, pipe tables).
    markdown: bool = False

    @abc.abstractmethod
    def extract(self, pdf_path: Path, expected_pages: int) -> list[ExtractedPage]:
        """Return raw text segments covering ``expected_pages`` pages."""


class PypdfExtractor(PdfExtractor):
    """Foundation strategy: one segment per PDF page via pypdf."""

    name = "pypdf"
    markdown = False

    def extract(self, pdf_path: Path, expected_pages: int) -> list[ExtractedPage]:
        try:
            reader = PdfReader(str(pdf_path))
            raw_pages = [(page.extract_text() or "") for page in reader.pages]
        except Exception as error:  # pragma: no cover - depends on supplied files
            raise PdfExtractionError(f"Cannot extract {pdf_path.name}: {error}") from None
        if not raw_pages:
            raise PdfExtractionError(f"{pdf_path.name}: no pages extracted")
        return [
            ExtractedPage(page_start=number, page_end=number, text=text)
            for number, text in enumerate(raw_pages, start=1)
        ]


class MarkItDownExtractorAdapter(PdfExtractor):
    """Adapts microsoft/markitdown's PDF conversion to the strategy.

    More accurate table extraction than plain text: markdown pipe-table
    rows make fee tables explicit. Page boundaries are not preserved by
    markitdown, so the output is one segment spanning the expected pages;
    citations stay source/page-range coarse for this strategy.
    """

    name = "markitdown"
    markdown = True

    def __init__(self) -> None:
        self._converter = None  # lazy: importing markitdown is expensive

    def extract(self, pdf_path: Path, expected_pages: int) -> list[ExtractedPage]:
        if self._converter is None:
            try:
                from markitdown import MarkItDown
            except ImportError as error:  # pragma: no cover - dependency pinned
                raise PdfExtractionError(
                    "markitdown is not installed; add markitdown[pdf]"
                ) from error
            self._converter = MarkItDown()
        try:
            result = self._converter.convert(str(pdf_path))
        except Exception as error:
            raise PdfExtractionError(
                f"markitdown cannot convert {pdf_path.name}: {error}"
            ) from None
        text = (getattr(result, "text_content", "") or "").strip()
        if not text:
            raise PdfExtractionError(f"{pdf_path.name}: markitdown produced no text")
        return [ExtractedPage(page_start=1, page_end=max(1, expected_pages), text=text)]


class PyMuPDF4LLMExtractor(PdfExtractor):
    """pymupdf4llm strategy: page-exact segments with markdown headings.

    ``to_markdown(page_chunks=True)`` returns one markdown chunk per page,
    so page attribution stays exact (unlike markitdown) while headings and
    emphasis survive as markdown. Headings prefixed with ``#`` become
    authoritative section boundaries in ``neova.corpus``.
    """

    name = "pymupdf4llm"
    markdown = True

    def extract(self, pdf_path: Path, expected_pages: int) -> list[ExtractedPage]:
        try:
            import pymupdf4llm
        except ImportError as error:  # pragma: no cover - dependency pinned
            raise PdfExtractionError(
                "pymupdf4llm is not installed; add pymupdf4llm"
            ) from error
        try:
            page_chunks = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True)
        except Exception as error:
            raise PdfExtractionError(
                f"pymupdf4llm cannot convert {pdf_path.name}: {error}"
            ) from None
        segments: list[ExtractedPage] = []
        for number, chunk in enumerate(page_chunks, start=1):
            text = (chunk.get("text") or "") if isinstance(chunk, dict) else str(chunk)
            segments.append(ExtractedPage(number, number, text.strip()))
        if not segments or all(not segment.text for segment in segments):
            raise PdfExtractionError(f"{pdf_path.name}: pymupdf4llm produced no text")
        return segments


_EXTRACTORS_BY_NAME = {
    PypdfExtractor.name: PypdfExtractor,
    MarkItDownExtractorAdapter.name: MarkItDownExtractorAdapter,
    PyMuPDF4LLMExtractor.name: PyMuPDF4LLMExtractor,
}


def get_pdf_extractor(name: str) -> PdfExtractor:
    """Build the named strategy; unknown names fail closed."""
    try:
        return _EXTRACTORS_BY_NAME[name]()
    except KeyError:
        raise PdfExtractionError(
            f"Unknown PDF extractor {name!r}; expected one of {sorted(_EXTRACTORS_BY_NAME)}"
        ) from None