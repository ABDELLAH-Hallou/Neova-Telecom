"""Versioned prompt text loaded from package-local Markdown files."""

from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=2)
def load(name: str) -> str:
    if name not in {"classifier.md", "answer.md"}:
        raise ValueError("Unknown prompt")
    return files(__package__).joinpath(name).read_text(encoding="utf-8").strip()
