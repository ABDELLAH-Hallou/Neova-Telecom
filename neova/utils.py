"""Read-only access to the supplied fixture (independent of SQLite)."""

import hashlib
import json
from pathlib import Path
from typing import Any


FIXTURE_PATH = Path(__file__).resolve().parent.parent / "data" / "neova_data.json"


def load_fixture() -> dict[str, Any]:
    """Load the supplied JSON without modifying it or accessing the database."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def fixture_hash() -> str:
    """Return the SHA-256 digest of the unchanged source bytes."""
    return hashlib.sha256(FIXTURE_PATH.read_bytes()).hexdigest()
