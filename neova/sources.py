"""Corpus metadata: the reviewed sources manifest and its validation.

The manifest at ``neova/resources/sources.json`` is the source of truth
(independent review of the original PDFs and the PNG, 2026-09-23; see
``docs/source_register.md``). This module validates it, converts each
entry into the immutable :class:`SourceMeta` and exposes the resource and
corpus paths. The two documents marked ``public internal`` there are
routing-only: consumers must filter on ``access`` and never store, embed
or return internal text.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parent
RESOURCES_DIR = MODULE_DIR / "resources"
CORPUS_DIR = MODULE_DIR.parent / "corpus"
SOURCES_MANIFEST_PATH = RESOURCES_DIR / "sources.json"
ROAMING_TRANSCRIPTION_PATH = RESOURCES_DIR / "fiche-roaming-international-scan.txt"

PDF_SUFFIX = ".pdf"
PNG_SUFFIX = ".png"

_ALLOWED_STATUSES = ("current", "deprecated")
_ALLOWED_ACCESS = ("public", "internal")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_ID_PATTERN = re.compile(r"[a-z0-9-]+")


class CorpusError(RuntimeError):
    """Raised when a corpus source or the manifest cannot be used as reviewed."""


@dataclass(frozen=True)
class SourceMeta:
    """Register row for one corpus source (see docs/source_register.md)."""

    source_id: str
    path: str
    status: str  # "current" or "deprecated"
    access: str  # "public" (customer-visible) or "internal" (routing-only)
    updated: str  # ISO date printed by the document
    pages: int
    non_contractual: bool = False


def load_sources_manifest(path: Path = SOURCES_MANIFEST_PATH) -> dict:
    """Load and fully validate the sources manifest; the source of truth.

    Validates structure, types, allowed values, unique source IDs and
    paths, ISO dates, the PNG transcription reference and the PNG hash.
    Raises :class:`CorpusError` naming the entry on any violation.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CorpusError(f"Sources manifest not found: {path}") from None
    except json.JSONDecodeError as error:
        raise CorpusError(f"Sources manifest is not valid JSON: {error}") from None
    if not isinstance(payload, dict):
        raise CorpusError("Sources manifest must be a JSON object")
    version = payload.get("version")
    if version != 1:
        raise CorpusError(f"Unsupported sources manifest version: {version!r}")
    reviewed = payload.get("reviewed")
    if not isinstance(reviewed, str) or not _DATE_PATTERN.match(reviewed):
        raise CorpusError("Sources manifest 'reviewed' must be an ISO date string")
    entries = payload.get("sources")
    if not isinstance(entries, list) or not entries:
        raise CorpusError("Sources manifest 'sources' must be a non-empty array")
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for entry in entries:
        _validate_entry(entry, seen_ids, seen_paths)
    return payload


def _validate_entry(entry: object, seen_ids: set[str], seen_paths: set[str]) -> None:
    if not isinstance(entry, dict):
        raise CorpusError(f"Manifest entry must be an object: {entry!r}")
    source_id = entry.get("source_id")
    path = entry.get("path")
    if not isinstance(source_id, str) or not re.fullmatch(r"[a-z0-9-]+", source_id):
        raise CorpusError(f"Manifest entry has invalid source_id: {source_id!r}")
    if not isinstance(path, str) or not path or path != Path(path).name:
        raise CorpusError(f"Manifest entry {source_id!r} has invalid path: {path!r}")
    if source_id in seen_ids:
        raise CorpusError(f"Duplicate source_id in manifest: {source_id!r}")
    if path in seen_paths:
        raise CorpusError(f"Duplicate source path in manifest: {path!r}")
    seen_ids.add(source_id)
    seen_paths.add(path)
    status = entry.get("status")
    if status not in _ALLOWED_STATUSES:
        raise CorpusError(f"Manifest entry {source_id!r} has invalid status: {status!r}")
    access = entry.get("access")
    if access not in _ALLOWED_ACCESS:
        raise CorpusError(f"Manifest entry {source_id!r} has invalid access: {access!r}")
    updated = entry.get("updated")
    if not isinstance(updated, str) or not _DATE_PATTERN.match(updated):
        raise CorpusError(f"Manifest entry {source_id!r} has invalid updated date: {updated!r}")
    pages = entry.get("pages")
    if not isinstance(pages, int) or isinstance(pages, bool) or pages < 1:
        raise CorpusError(f"Manifest entry {source_id!r} has invalid pages: {pages!r}")
    non_contractual = entry.get("non_contractual", False)
    if not isinstance(non_contractual, bool):
        raise CorpusError(
            f"Manifest entry {source_id!r} has invalid non_contractual: {non_contractual!r}"
        )
    if path.endswith(PNG_SUFFIX):
        transcription = entry.get("transcription")
        if not isinstance(transcription, str) or not transcription:
            raise CorpusError(
                f"Manifest entry {source_id!r} (PNG) must reference a transcription file"
            )
        transcription_path = RESOURCES_DIR / transcription
        if not transcription_path.is_file():
            raise CorpusError(
                f"Manifest entry {source_id!r} references missing transcription: {transcription!r}"
            )
        expected_hash = entry.get("sha256")
        if not isinstance(expected_hash, str) or not _HASH_PATTERN.match(expected_hash):
            raise CorpusError(
                f"Manifest entry {source_id!r} must record a valid sha256 "
                "for review traceability"
            )
        original_path = CORPUS_DIR / path
        if not original_path.is_file():
            raise CorpusError(
                f"Manifest entry {source_id!r} references missing corpus file: {path!r}"
            )
        actual_hash = hashlib.sha256(original_path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise CorpusError(
                f"Manifest entry {source_id!r}: PNG hash mismatch "
                f"(manifest {expected_hash[:12]}..., corpus {actual_hash[:12]}...)"
            )
    elif non_contractual:
        raise CorpusError(
            f"Manifest entry {source_id!r}: only the reviewed PNG carries "
            "the non_contractual flag"
        )


def _sources_from_manifest(payload: dict) -> tuple[SourceMeta, ...]:
    """Convert validated manifest entries into immutable SourceMeta rows."""
    return tuple(
        SourceMeta(
            source_id=entry["source_id"],
            path=entry["path"],
            status=entry["status"],
            access=entry["access"],
            updated=entry["updated"],
            pages=entry["pages"],
            non_contractual=entry.get("non_contractual", False),
        )
        for entry in payload["sources"]
    )


@lru_cache(maxsize=1)
def _load_sources() -> tuple[SourceMeta, ...]:
    """Parse and validate the manifest once per process."""
    return _sources_from_manifest(load_sources_manifest())


def reset_sources_cache() -> None:
    """Clear the cached manifest (used by tests that rewrite the manifest)."""
    _load_sources.cache_clear()


def all_sources() -> tuple[SourceMeta, ...]:
    """Validated sources from the manifest, in manifest order."""
    return _load_sources()


# Retained names for existing importers; populated from the validated manifest.
SOURCES: tuple[SourceMeta, ...] = all_sources()
SOURCE_BY_ID: dict[str, SourceMeta] = {source.source_id: source for source in SOURCES}


def source_path(source: SourceMeta) -> Path:
    """Absolute path of the original corpus file for a source."""
    return CORPUS_DIR / source.path