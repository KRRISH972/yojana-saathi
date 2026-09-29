"""Semantic search over government schemes, backed by the ChromaDB collection built by
backend/services/ingest.py.

The embedding model and the ChromaDB client/collection are expensive to create, so each is
loaded once per process and reused (module-level singletons), not recreated on every call.
"""

from __future__ import annotations

import logging
from pathlib import Path

import chromadb
from chromadb.api.models.Collection import Collection
from pydantic import BaseModel

from backend.services.embeddings import get_embedding_model
from backend.services.ingest import COLLECTION_NAME, DEFAULT_PERSIST_DIR, rebuild_collection
from backend.services.scheme_store import load_all_schemes

logger = logging.getLogger(__name__)

_collection: Collection | None = None


class SchemeMatch(BaseModel):
    """One scheme match for a search query.

    ``score`` is a cosine similarity: 1.0 means the query and the scheme document are
    identical in meaning, 0.0 means unrelated, and negative values mean opposite meaning.
    Higher is always better.
    """

    scheme_id: str
    category: str
    score: float


def get_collection(persist_directory: Path = DEFAULT_PERSIST_DIR, collection_name: str = COLLECTION_NAME) -> Collection:
    """Return the shared Chroma collection, opening it only on first use.

    Tests and other callers that need an isolated collection (e.g. a temporary one built
    from a small fixture dataset) should not call this function — they should build their
    own collection with backend.services.ingest.rebuild_collection and pass it directly to
    search_schemes's ``collection`` argument instead.
    """
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=str(persist_directory))
        _collection = client.get_collection(name=collection_name)
    return _collection


def ensure_ready(persist_directory: Path = DEFAULT_PERSIST_DIR) -> None:
    """Load the embedding model and open the search index, so the first user does not wait.

    If the index does not exist yet (a fresh server, where data/chroma/ is not committed),
    it is built from data/schemes.json first, instead of every search failing.
    """
    global _collection
    get_embedding_model()
    try:
        get_collection(persist_directory)
    except Exception:  # noqa: BLE001 - chromadb's "not found" error type varies by version
        logger.warning("Search index not found; building it from data/schemes.json.")
        _collection = rebuild_collection(list(load_all_schemes()), persist_directory=persist_directory)


def search_schemes(
    query: str,
    top_k: int = 5,
    category: str | None = None,
    collection: Collection | None = None,
) -> list[SchemeMatch]:
    """Return the top_k schemes whose search document is closest in meaning to ``query``.

    ``category`` optionally restricts the search to one scheme category (e.g. "pension").
    ``collection`` lets a caller (mainly tests) pass in their own Chroma collection instead
    of the shared, default one.
    """
    active_collection = collection if collection is not None else get_collection()
    model = get_embedding_model()
    query_embedding = model.encode([query], normalize_embeddings=True).tolist()

    where = {"category": category} if category is not None else None
    result = active_collection.query(
        query_embeddings=query_embedding,
        n_results=top_k,
        where=where,
    )

    ids = result["ids"][0]
    distances = result["distances"][0]
    metadatas = result["metadatas"][0]

    return [
        SchemeMatch(scheme_id=scheme_id, category=metadata["category"], score=1.0 - distance)
        for scheme_id, distance, metadata in zip(ids, distances, metadatas, strict=True)
    ]
