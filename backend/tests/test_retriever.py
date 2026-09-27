"""Tests for the retriever, using the real schemes.json data ingested into a temporary,
isolated ChromaDB collection (never the shared data/chroma/ used by scripts/ingest.py).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.models.scheme import Scheme
from backend.services.ingest import rebuild_collection
from backend.services.retriever import search_schemes

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMES_JSON = PROJECT_ROOT / "data" / "schemes.json"


@pytest.fixture(scope="module")
def schemes() -> list[Scheme]:
    """The real schemes shipped in data/schemes.json."""
    entries = json.loads(SCHEMES_JSON.read_text(encoding="utf-8-sig"))
    return [Scheme.model_validate(entry) for entry in entries]


@pytest.fixture(scope="module")
def test_collection(tmp_path_factory: pytest.TempPathFactory, schemes: list[Scheme]):
    """A collection built from the real dataset, in a throwaway directory."""
    persist_dir = tmp_path_factory.mktemp("chroma_test")
    return rebuild_collection(schemes, persist_directory=persist_dir, collection_name="test-schemes")


def test_english_pension_query_finds_pm_kmy(test_collection, schemes: list[Scheme]) -> None:
    """A clear English pension query should rank PM-KMY (the pension scheme) at the top."""
    results = search_schemes("pension for farmers in old age", top_k=2, collection=test_collection)
    assert results
    assert results[0].scheme_id == "pm-kmy"


def test_hindi_pension_query_finds_pm_kmy(test_collection) -> None:
    """The same query in Hindi should also find the pension scheme, thanks to the
    multilingual embedding model."""
    results = search_schemes("किसानों के लिए पेंशन योजना", top_k=2, collection=test_collection)
    assert results
    assert results[0].scheme_id == "pm-kmy"


def test_category_filter_restricts_results(test_collection) -> None:
    """Filtering by category='pension' should only ever return pension schemes."""
    results = search_schemes("farmer support", top_k=5, category="pension", collection=test_collection)
    assert results
    assert all(r.category == "pension" for r in results)


def test_top_k_limits_result_count(test_collection) -> None:
    """top_k=1 must return at most one result, even with two schemes in the index."""
    results = search_schemes("farmer income support", top_k=1, collection=test_collection)
    assert len(results) <= 1


def test_results_are_sorted_by_descending_score(test_collection) -> None:
    """Chroma already returns nearest neighbours first; check scores are non-increasing."""
    results = search_schemes("government scheme for farmers", top_k=5, collection=test_collection)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_unrelated_query_scores_lower_than_relevant_query(test_collection) -> None:
    """An unrelated query's best score should be clearly lower than a relevant query's."""
    relevant = search_schemes("pension for farmers in old age", top_k=1, collection=test_collection)
    unrelated = search_schemes("scholarship for students", top_k=1, collection=test_collection)
    assert relevant[0].score > unrelated[0].score
