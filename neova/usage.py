"""Per-call usage and spend ledger for every OpenRouter call (issue #6).

One record per attempted model call — chat (answer), classifier and
embeddings — carrying the model name, upstream provider, route
(primary/fallback), token counts, retry index and the cost the API
itself reported. Rules enforced here:

- **Missing usage is recorded as ``None`` and reported as ``unknown``**,
  never as zero: a call whose usage the provider did not return is not
  free.
- **Redaction by construction**: a record can only contain the fields
  declared below — never the API key, session tokens or customer data.
- The ledger is process-local (same lifetime contract as
  ``SessionStore``) **and** optionally persisted: when ``USAGE_LOG`` is
  configured (wired at application startup), every record is appended
  as one redacted JSONL line, so spend evidence survives the server
  stopping and can be reloaded with :func:`load_records` / summarized
  with ``python -m neova.usage [path]``.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass

from .config import get_usage_log_path

KIND_CHAT = "chat"
KIND_CLASSIFIER = "classifier"
KIND_EMBEDDINGS = "embeddings"


@dataclass(frozen=True)
class CallRecord:
    """One attempted model call; the field set is the redaction boundary."""

    kind: str                      # chat | classifier | embeddings
    model: str
    route: str | None = None       # primary | fallback
    provider: str | None = None    # upstream provider, as returned by the API
    status: str = "ok"             # ok | failed
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None  # only when the API itself reported a cost
    retries: int = 0               # 0 = first attempt of the route
    error: str | None = None


_lock = threading.Lock()
_records: list[CallRecord] = []
_log_path: str | None = None


def set_log_path(path: str | None) -> None:
    """Optionally mirror every record as one redacted JSONL line."""
    global _log_path
    with _lock:
        _log_path = path


def configure_from_env() -> None:
    """Wire the optional ``USAGE_LOG`` path (called at app startup)."""
    set_log_path(get_usage_log_path())


def load_records(path: str) -> list[CallRecord]:
    """Reload redacted records from a persisted JSONL usage log.

    Malformed or partial lines are skipped, never guessed; the result
    feeds :func:`spend_summary` and the provider route report so spend
    evidence survives process restarts.
    """
    loaded: list[CallRecord] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    loaded.append(CallRecord(**json.loads(line)))
                except (ValueError, TypeError):
                    continue  # a corrupt line is skipped, not invented
    except OSError:
        return []
    return loaded


def record(**fields) -> CallRecord:
    """Append one redacted record; unknown fields stay None (unknown).

    The JSONL append happens while holding the ledger lock so concurrent
    model calls can never interleave partial lines, and the log's parent
    directory is created on first write.
    """
    entry = CallRecord(**fields)
    with _lock:
        _records.append(entry)
        path = _log_path
        if path:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
    return entry


def records() -> list[CallRecord]:
    with _lock:
        return list(_records)


def reset() -> None:
    """Test helper: drop the process-local ledger (JSONL files stay)."""
    with _lock:
        _records.clear()


def spend_summary(records: list[CallRecord] | None = None) -> dict:
    """Known spend per call kind; unknown-usage calls counted, never zeroed.

    Costs are the values the API itself reported (``usage.cost`` via
    OpenRouter usage accounting). Calls without a reported cost are
    counted under ``calls_unknown_cost`` — their spend is unknown, not
    zero. Pass reloaded :func:`load_records` output to summarize a
    persisted log; default is the current process ledger.
    """
    snapshot = list(records) if records is not None else records_current()
    summary: dict[str, dict] = {}
    for record_item in snapshot:
        bucket = summary.setdefault(record_item.kind, {
            "known_usd": 0.0, "calls": 0, "calls_unknown_cost": 0})
        bucket["calls"] += 1
        if record_item.cost_usd is None:
            bucket["calls_unknown_cost"] += 1
        else:
            bucket["known_usd"] = round(bucket["known_usd"] + record_item.cost_usd, 6)
    total_known = round(
        sum(bucket["known_usd"] for bucket in summary.values()), 6)
    return {"kinds": summary, "total_known_usd": total_known}


def records_current() -> list[CallRecord]:
    """Snapshot of the process-local ledger (same as :func:`records`)."""
    return records()


def main(argv: list[str] | None = None) -> int:
    """CLI: redacted spend summary from a usage log or this process.

    ``python -m neova.usage [path]`` — with no path it reads ``USAGE_LOG``
    when configured, else the in-memory ledger of the current process.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m neova.usage",
        description="Redacted per-call spend summary (chat, classifier, embeddings).")
    parser.add_argument(
        "path", nargs="?", default=None,
        help="Persisted usage JSONL path (defaults to USAGE_LOG, else the "
             "in-memory ledger of this process).")
    arguments = parser.parse_args(argv)

    if arguments.path:
        loaded = load_records(arguments.path)
    else:
        configured = get_usage_log_path()
        loaded = load_records(configured) if configured else records()
    print(json.dumps(spend_summary(loaded), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
