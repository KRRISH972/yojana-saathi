"""Loads the scheme dataset (data/schemes.json) once per process and shares it."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from backend.models.scheme import Scheme

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMES_JSON = PROJECT_ROOT / "data" / "schemes.json"


@lru_cache
def load_all_schemes() -> tuple[Scheme, ...]:
    """Every scheme in data/schemes.json, validated, cached after the first read."""
    entries = json.loads(SCHEMES_JSON.read_text(encoding="utf-8-sig"))  # tolerate a Windows BOM
    return tuple(Scheme.model_validate(entry) for entry in entries)


@lru_cache
def schemes_by_id() -> dict[str, Scheme]:
    """The same schemes, keyed by id."""
    return {scheme.id: scheme for scheme in load_all_schemes()}
