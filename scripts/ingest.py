"""CLI: build/rebuild the ChromaDB search index from data/schemes.json.

Usage (from the project root):
    venv\\Scripts\\python scripts/ingest.py

Re-running this always rebuilds the collection from scratch (see
backend/services/ingest.rebuild_collection), so it never creates duplicates or leaves
behind a scheme that was removed from schemes.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.models.scheme import Scheme  # noqa: E402
from backend.services.ingest import DEFAULT_PERSIST_DIR, rebuild_collection  # noqa: E402

SCHEMES_JSON = PROJECT_ROOT / "data" / "schemes.json"


def main() -> int:
    """Load every scheme, embed it, and rebuild the persistent search collection."""
    entries = json.loads(SCHEMES_JSON.read_text(encoding="utf-8-sig"))
    schemes = [Scheme.model_validate(entry) for entry in entries]

    print(f"Loaded {len(schemes)} scheme(s) from {SCHEMES_JSON.name}.")
    print("Loading the embedding model (first run downloads it; see backend/services/embeddings.py)...")
    rebuild_collection(schemes)
    print(f"Indexed {len(schemes)} scheme(s) into the 'schemes' collection at {DEFAULT_PERSIST_DIR}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
