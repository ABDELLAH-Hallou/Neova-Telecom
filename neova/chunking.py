"""Chunk functions: splitting cleaned source text into cited passages.

Sections come from the heading classifiers in ``neova.extraction`` (markdown
``#`` headings are authoritative); tables stay atomic and may be fused with
their note; sections are soft boundaries and chunks pack to roughly
250-450 words with a small overlap when a section must be split.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .extraction import (
    _is_heading,
    _is_table,
    default_pdf_extractor,
    extract_pdf_pages,
    roaming_pages,
)
from .pdf_extractors import ExtractedPage, PdfExtractor
from .sources import PDF_SUFFIX, SourceMeta, all_sources, source_path

MIN_CHUNK_WORDS = 250
MAX_CHUNK_WORDS = 450
OVERLAP_WORDS = 40
NOTE_MAX_WORDS = 120  # a table note may be fused with its table up to this size


@dataclass(frozen=True)
class Chunk:
    """One indexable text passage with its reviewed source citation."""

    chunk_hash: str
    source_id: str
    source_path: str
    page_start: int
    page_end: int
    section: str
    text: str
    access: str
    word_count: int
    overlap: bool


@dataclass
class _Unit:
    """Atomic packing unit: a paragraph, or a table fused with its note."""

    text: str
    page_start: int
    page_end: int
    section: str
    atomic: bool


def _blocks_with_pages(
    segments: list[tuple[int, int, str]]
) -> list[tuple[int, int, str]]:
    """Split cleaned segments into blank-line-separated blocks with page ranges."""
    blocks: list[tuple[int, int, str]] = []
    for page_start, page_end, text in segments:
        current: list[str] = []
        for line in text.splitlines() + [""]:
            if line.strip():
                current.append(line)
            elif current:
                blocks.append((page_start, page_end, "\n".join(current)))
                current = []
    return blocks


def _build_units(segments: list[tuple[int, int, str]]) -> list[_Unit]:
    """Turn blocks into units, splitting blocks at embedded heading lines.

    Tables are never split: a table block stays one unit and may be fused
    with the following note block. Any other block is cut at heading-like
    lines, which start a new section. Markdown headings (``#``-prefixed)
    are authoritative and lose their markers.
    """
    units: list[_Unit] = []
    section = "(introduction)"
    blocks = _blocks_with_pages(segments)
    index = 0
    while index < len(blocks):
        page_start, page_end, text = blocks[index]
        if _is_table(text):
            if index + 1 < len(blocks):
                next_start, next_end, note = blocks[index + 1]
                if not _is_heading(note) and len(note.split()) <= NOTE_MAX_WORDS:
                    fused = f"{text}\n\n{note}"
                    units.append(_Unit(fused, page_start, next_end, section, True))
                    index += 2
                    continue
            units.append(_Unit(text, page_start, page_end, section, True))
            index += 1
            continue
        segment: list[str] = []
        segment_start = page_start
        for line in text.splitlines() + [""]:
            if line.strip() and _is_heading(line):
                if segment:
                    units.append(_Unit("\n".join(segment), segment_start, page_end, section, False))
                heading = line.strip().lstrip("#").strip()
                section = heading
                segment = [heading]
                segment_start = page_start
                continue
            if line.strip():
                segment.append(line)
            elif segment:
                units.append(_Unit("\n".join(segment), segment_start, page_end, section, False))
                segment = []
        if segment:
            units.append(_Unit("\n".join(segment), segment_start, page_end, section, False))
        index += 1
    return units


def _split_long_unit(unit: _Unit) -> list[_Unit]:
    """Split an oversized non-atomic unit into sentence windows with overlap."""
    sentences = [s.strip() for s in unit.text.replace("\n", " ").split(". ") if s.strip()]
    if len(sentences) < 2:
        return [unit]
    parts: list[_Unit] = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        words = len(sentence.split())
        if current and current_words + words > MAX_CHUNK_WORDS:
            text = ". ".join(current) + "."
            parts.append(_Unit(text, unit.page_start, unit.page_end, unit.section, False))
            tail = " ".join(text.split()[-OVERLAP_WORDS:])
            current = [tail, sentence]
            current_words = len(tail.split()) + words
        else:
            current.append(sentence)
            current_words += words
    if current:
        parts.append(_Unit(". ".join(current) + ".", unit.page_start, unit.page_end, unit.section, False))
    return parts


def chunk_document(source: SourceMeta, extractor: PdfExtractor | None = None) -> list[Chunk]:
    """Extract one reviewed source and split it into cited chunks.

    ``extractor`` selects the strategy (default: ``PDF_EXTRACTOR`` env or
    ``pymupdf4llm``). Markitdown yields one coarse page segment; the
    pymupdf4llm default keeps page-exact attribution.
    """
    strategy = extractor or default_pdf_extractor()
    if source.path.endswith(PDF_SUFFIX):
        segments = extract_pdf_pages(source_path(source), source.pages, strategy)
    else:
        segments = [ExtractedPage(1, source.pages, roaming_pages()[0])]
    cleaned_segments = [(s.page_start, s.page_end, s.text) for s in segments]
    units: list[_Unit] = []
    for unit in _build_units(cleaned_segments):
        if len(unit.text.split()) > MAX_CHUNK_WORDS and not unit.atomic:
            units.extend(_split_long_unit(unit))
        else:
            units.append(unit)
    return pack_units(source, units)


def pack_units(source: SourceMeta, units: list[_Unit]) -> list[Chunk]:
    """Pack units into chunks of roughly MIN to MAX words.

    Sections are soft boundaries: an undersized buffer absorbs the next
    section instead of emitting a tiny chunk. A chunk is emitted when adding
    the next unit would exceed MAX words; within a section the next chunk
    then starts with a small tail of the previous chunk (overlap).
    """
    chunks: list[Chunk] = []
    buffer: list[_Unit] = []
    buffer_pages: list[int] = []
    buffer_words = 0
    section: str | None = None
    overlap_start = False

    def flush() -> None:
        nonlocal buffer, buffer_pages, buffer_words
        if not buffer:
            return
        first = buffer[0]
        text = "\n\n".join(unit.text for unit in buffer).strip()
        chunks.append(Chunk(
            chunk_hash=chunk_hash_for(source.source_id, first.page_start, text),
            source_id=source.source_id,
            source_path=source.path,
            page_start=min(buffer_pages),
            page_end=max(buffer_pages),
            section=first.section,
            text=text,
            access=source.access,
            word_count=len(text.split()),
            overlap=overlap_start,
        ))
        buffer = []
        buffer_pages = []
        buffer_words = 0

    for unit in units:
        if section is not None and unit.section != section and buffer_words >= MIN_CHUNK_WORDS:
            flush()
            overlap_start = False
        section = unit.section
        words = len(unit.text.split())
        if buffer_words and buffer_words + words > MAX_CHUNK_WORDS:
            flush()
            if chunks and chunks[-1].section == unit.section:
                tail = " ".join(chunks[-1].text.split()[-OVERLAP_WORDS:])
                buffer.append(_Unit(tail, unit.page_start, unit.page_end, unit.section, False))
                buffer_words = len(tail.split())
                buffer_pages = [unit.page_start]
                overlap_start = True
        buffer.append(unit)
        buffer_words += words
        buffer_pages.extend(range(unit.page_start, unit.page_end + 1))
    flush()
    # Merge an undersized tail chunk into its predecessor when possible.
    if len(chunks) >= 2 and chunks[-1].word_count < MIN_CHUNK_WORDS:
        previous = chunks[-2]
        if previous.word_count + chunks[-1].word_count <= MAX_CHUNK_WORDS:
            merged_text = f"{previous.text}\n\n{chunks[-1].text}"
            chunks[-2] = Chunk(
                chunk_hash=previous.chunk_hash,
                source_id=previous.source_id,
                source_path=previous.source_path,
                page_start=previous.page_start,
                page_end=max(previous.page_end, chunks[-1].page_end),
                section=previous.section,
                text=merged_text,
                access=previous.access,
                word_count=len(merged_text.split()),
                overlap=previous.overlap,
            )
            chunks.pop()
    return chunks


def chunk_hash_for(source_id: str, page_start: int, text: str) -> str:
    """Stable content hash; combined with the model name it is the cache key."""
    payload = f"{source_id}|{page_start}|{text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def iter_chunks() -> list[Chunk]:
    """Chunks for every reviewed source, including routing-only documents.

    Consumers must filter on ``access``: only ``public`` chunks may be
    indexed, embedded or shown to customers.
    """
    chunks: list[Chunk] = []
    for source in all_sources():
        chunks.extend(chunk_document(source))
    return chunks


def public_chunks() -> list[Chunk]:
    """Customer-public chunks only; internal sources stay routing-only."""
    return [chunk for chunk in iter_chunks() if chunk.access == "public"]