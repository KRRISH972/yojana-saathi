"""CLI: run a fixed set of test queries against the search index and print the results.

This is a manual/diagnostic report, not a pytest suite — see backend/tests/test_retriever.py
for the automated tests. Run scripts/ingest.py first so the index exists.

Usage (from the project root):
    venv\\Scripts\\python scripts/test_search.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.services.retriever import search_schemes  # noqa: E402

QUERIES: dict[str, list[str]] = {
    "English": [
        "pension for farmers in old age",
        "money support for farmers",
    ],
    "Hindi": [
        "किसानों के लिए पेंशन योजना",
        "खेती के लिए पैसे",
    ],
    "Hinglish (Roman script)": [
        "budhape mein pension",
        "kisan ko paise wali yojana",
    ],
    "Unrelated": [
        "scholarship for students",
        "loan for business",
    ],
}


def main() -> int:
    """Run every query in QUERIES and print its top matches with similarity scores."""
    for group_name, queries in QUERIES.items():
        print(f"\n=== {group_name} ===")
        for query in queries:
            print(f"\nQuery: {query!r}")
            results = search_schemes(query, top_k=3)
            if not results:
                print("  (no results)")
            for rank, match in enumerate(results, start=1):
                print(f"  {rank}. {match.scheme_id}  (category={match.category})  score={match.score:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
