"""Validate data/schemes.json against the Scheme model and report problems clearly.

Usage (from the project root):
    python scripts/validate_data.py [path/to/schemes.json]

Exit code is 0 when every entry is valid, 1 otherwise.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.models.scheme import Scheme  # noqa: E402

DEFAULT_DATA_PATH = PROJECT_ROOT / "data" / "schemes.json"
PLACEHOLDER_MARKER = "placeholder"


def load_entries(path: Path) -> list[Any]:
    """Read the JSON file and return its top-level list, exiting with a message on failure."""
    if not path.is_file():
        sys.exit(f"ERROR: file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))  # tolerate Windows BOM
    except json.JSONDecodeError as exc:
        sys.exit(f"ERROR: {path.name} is not valid JSON: {exc.msg} (line {exc.lineno}, column {exc.colno})")
    if not isinstance(data, list):
        sys.exit(f"ERROR: {path.name} must contain a JSON list of scheme objects at the top level.")
    return data


def describe_entry(index: int, entry: Any) -> str:
    """Return a short label such as 'entry #2 (id=my-scheme)' for error messages."""
    entry_id = entry.get("id") if isinstance(entry, dict) else None
    return f"entry #{index}" + (f" (id={entry_id})" if entry_id else "")


def format_errors(label: str, error: ValidationError) -> list[str]:
    """Turn a pydantic ValidationError into one readable line per problem."""
    lines = []
    for err in error.errors():
        field = ".".join(str(part) for part in err["loc"]) or "(entry)"
        if err["type"] == "missing":
            lines.append(f"  {label}: missing required field '{field}'")
        elif err["type"] == "extra_forbidden":
            lines.append(f"  {label}: unknown field '{field}' (typo?)")
        else:
            lines.append(f"  {label}: field '{field}': {err['msg']}")
    return lines


def validate_entries(entries: list[Any]) -> tuple[list[Scheme], list[str]]:
    """Validate each entry; return the valid schemes and a list of error lines."""
    schemes: list[Scheme] = []
    errors: list[str] = []
    for index, entry in enumerate(entries, start=1):
        label = describe_entry(index, entry)
        try:
            schemes.append(Scheme.model_validate(entry))
        except ValidationError as exc:
            errors.extend(format_errors(label, exc))
    return schemes, errors


def find_duplicate_ids(schemes: list[Scheme]) -> list[str]:
    """Return error lines for scheme ids that appear more than once."""
    counts = Counter(s.id for s in schemes)
    return [f"  duplicate id '{sid}' appears {n} times" for sid, n in counts.items() if n > 1]


def find_placeholders(schemes: list[Scheme]) -> list[str]:
    """Return ids of schemes that still contain placeholder text (not real data yet)."""
    return [s.id for s in schemes if PLACEHOLDER_MARKER in s.model_dump_json().lower()]


def main() -> int:
    """Run validation and print a report. Returns the process exit code."""
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATA_PATH
    entries = load_entries(path)
    schemes, errors = validate_entries(entries)
    errors.extend(find_duplicate_ids(schemes))

    print(f"Checked {len(entries)} entries in {path.name}: {len(schemes)} valid.")
    placeholders = find_placeholders(schemes)
    if placeholders:
        print(f"WARNING: {len(placeholders)} entries still contain placeholder data: {', '.join(placeholders)}")
    if errors:
        print(f"\n{len(errors)} problem(s) found:")
        print("\n".join(errors))
        return 1
    print("OK: all entries are valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
