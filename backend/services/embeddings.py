"""Loads the multilingual sentence embedding model used for scheme search.

Model choice
------------
We use ``paraphrase-multilingual-MiniLM-L12-v2`` from sentence-transformers:

- It is trained on 50+ languages, including Hindi (Devanagari script) and English, and maps
  same-meaning text in different languages close together in vector space — so a Hindi
  query can find a scheme document written in English, and vice versa.
- It is a small (~118M parameter, ~470 MB) MiniLM model, fast enough to run on a CPU with
  no GPU, which matches our free-tier Hugging Face Spaces deployment target.
- It is one of the standard, widely-used sentence-transformers models for exactly this
  "small, multilingual, CPU-friendly" use case, so it is well-tested and well-documented.

The model is downloaded once from Hugging Face the first time it is used, then cached
locally (by default under the user's Hugging Face cache folder), so later runs are fast
and offline-capable.
"""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

_model: SentenceTransformer | None = None


def get_embedding_model() -> SentenceTransformer:
    """Return the shared embedding model instance, loading it only on first use."""
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model
