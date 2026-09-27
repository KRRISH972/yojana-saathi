"""Builds the search index: one embedded document per scheme in a persistent ChromaDB
collection. Used by scripts/ingest.py (the CLI) and by the retriever's tests.

Re-running the ingest always deletes the collection first and recreates it from scratch,
so it never accumulates duplicate or stale entries.
"""

from __future__ import annotations

from pathlib import Path

import chromadb
from chromadb.api.models.Collection import Collection

from backend.models.scheme import Eligibility, ExclusionCategory, Scheme
from backend.services.embeddings import get_embedding_model

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSIST_DIR = PROJECT_ROOT / "data" / "chroma"
COLLECTION_NAME = "schemes"

_EXCLUSION_LABELS: dict[ExclusionCategory, str] = {
    ExclusionCategory.INSTITUTIONAL_LAND_HOLDER: "institutional land holders",
    ExclusionCategory.CONSTITUTIONAL_POST_HOLDER: "holders of constitutional posts",
    ExclusionCategory.ELECTED_REPRESENTATIVE: "elected representatives",
    ExclusionCategory.GOVERNMENT_EMPLOYEE: "government employees",
    ExclusionCategory.HIGH_PENSIONER: "high pensioners",
    ExclusionCategory.INCOME_TAX_PAYER: "income tax payers",
    ExclusionCategory.REGISTERED_PROFESSIONAL: "registered professionals (doctors, engineers, lawyers, etc.)",
    ExclusionCategory.OTHER_SOCIAL_SECURITY_SCHEME: "people already covered by another social security scheme",
}


def _humanize(value: str) -> str:
    """Turn an enum value like 'salaried_private' into 'salaried private' for plain text."""
    return value.replace("_", " ")


def build_eligibility_summary(eligibility: Eligibility) -> str:
    """A short plain-language sentence covering the structured eligibility fields, so the
    search index also matches on things like age, land, income and occupation — not just
    the scheme's free-text description."""
    parts: list[str] = []

    if eligibility.min_age is not None or eligibility.max_age is not None:
        if eligibility.min_age is not None and eligibility.max_age is not None:
            parts.append(f"for ages {eligibility.min_age} to {eligibility.max_age}")
        elif eligibility.min_age is not None:
            parts.append(f"for age {eligibility.min_age} and above")
        else:
            parts.append(f"for age up to {eligibility.max_age}")

    if eligibility.gender.value != "all":
        parts.append(f"for {eligibility.gender.value} applicants")

    if eligibility.occupations:
        occupations = ", ".join(_humanize(o.value) for o in eligibility.occupations)
        parts.append(f"for these occupations: {occupations}")

    if eligibility.social_categories:
        categories = ", ".join(c.value.upper() for c in eligibility.social_categories)
        parts.append(f"for these social categories: {categories}")

    if eligibility.allowed_states:
        parts.append(f"available in {', '.join(eligibility.allowed_states)}")

    if eligibility.max_annual_income is not None:
        parts.append(f"for families with annual income up to Rs {eligibility.max_annual_income:,}")

    if eligibility.requires_own_cultivable_land:
        parts.append("requires owning cultivable land in your own name")

    if eligibility.max_landholding_hectares is not None:
        parts.append(f"for landholdings up to {eligibility.max_landholding_hectares:g} hectares")

    if eligibility.excluded_if:
        excluded = ", ".join(_EXCLUSION_LABELS[c] for c in eligibility.excluded_if)
        parts.append(f"not available to {excluded}")

    if not parts:
        return "No specific eligibility restrictions."
    return "Eligibility: " + "; ".join(parts) + "."


def build_search_document(scheme: Scheme) -> str:
    """Combine everything a search query might match on into one text document per scheme."""
    return "\n".join(
        [
            scheme.name_en,
            scheme.name_hi,
            scheme.category.value,
            scheme.description_en,
            scheme.description_hi,
            scheme.benefits,
            build_eligibility_summary(scheme.eligibility),
        ]
    )


def rebuild_collection(
    schemes: list[Scheme],
    persist_directory: Path = DEFAULT_PERSIST_DIR,
    collection_name: str = COLLECTION_NAME,
) -> Collection:
    """Delete any existing collection and re-create it from the given schemes.

    Deleting first (rather than upserting) means a re-run never leaves behind a scheme
    that was since removed from data/schemes.json, and never creates duplicate entries.
    """
    persist_directory.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_directory))

    try:
        client.delete_collection(collection_name)
    except Exception:  # noqa: BLE001 - chromadb's "not found" error type varies by version
        pass  # collection did not exist yet; nothing to delete

    collection = client.create_collection(name=collection_name, metadata={"hnsw:space": "cosine"})

    if not schemes:
        return collection

    documents = [build_search_document(scheme) for scheme in schemes]
    model = get_embedding_model()
    embeddings = model.encode(documents, normalize_embeddings=True).tolist()

    collection.add(
        ids=[scheme.id for scheme in schemes],
        embeddings=embeddings,
        documents=documents,
        metadatas=[{"scheme_id": scheme.id, "category": scheme.category.value} for scheme in schemes],
    )
    return collection
