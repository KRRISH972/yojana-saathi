# Yojana Saathi

AI assistant that helps Indian citizens (especially rural users) discover government schemes they are eligible for. It understands Hindi, Hinglish, and English, and supports voice input/output.

## Stack (all free)

- **Backend:** Python 3.14, FastAPI (confirmed: `requirements.txt` installs cleanly on 3.14, including torch, sentence-transformers and chromadb — stay on 3.14)
- **Vector DB:** ChromaDB (persistent, local)
- **Embeddings:** sentence-transformers multilingual model, run locally
- **LLM:** Google Gemini API (free tier), model `gemini-3.8-flash`, configured via `GEMINI_API_KEY` / `GEMINI_MODEL`
- **Frontend:** plain HTML + Tailwind CSS + vanilla JS (no build step)
- **Voice:** browser Web Speech API (speech recognition + synthesis)
- **Deployment:** Hugging Face Spaces (Docker)

## Folder structure

```
backend/
  app/        FastAPI app: entrypoint, routes, config loading
  services/   Business logic: retrieval, embeddings, Gemini calls, eligibility
  models/     Pydantic schemas (request/response, scheme records)
  tests/      pytest tests
data/         Scheme datasets (JSON/CSV) used to build the vector DB
frontend/     Static HTML/CSS/JS served to the browser
scripts/      One-off CLI tools (e.g. ingest schemes into ChromaDB)
```

## Coding rules

- Type hints on every function signature and public attribute.
- Docstring on every module, class, and function (short, one-line summary is fine).
- Keep functions small and single-purpose; split when a function does more than one thing.
- **No hardcoded secrets** — API keys, tokens, and URLs with credentials never appear in code.
- All configuration comes from environment variables, loaded in one place (`backend/app/config.py`) and imported from there. `.env` is local only and gitignored; `.env.example` documents required variables.
- Routes stay thin: validate input, call a service, return a model. Logic lives in `services/`.
- Write tests in `backend/tests/` for new service logic.
- Frontend must work on low-end phones and slow networks: no heavy frameworks, keep JS small.

## Gemini API

- **Model:** `gemini-3.8-flash`. Gemini 2.0 models are shut down and 2.5 models are being shut down, so never use them.
- **Check the docs first:** before writing any Gemini code, read the current official docs at https://ai.google.dev/gemini-api/docs/latest-model. The SDK and API have changed recently, so do not rely on older examples from memory.
- **Thinking level:** use `thinking_level` set to `"low"` for chat responses to keep replies fast.

## Common commands

```
python -m venv venv && venv\Scripts\activate    # Windows
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
pytest
python scripts/validate_data.py                 # validate data/schemes.json
python scripts/ingest.py                        # (re)build the ChromaDB search index from schemes.json
python scripts/test_search.py                    # print search results for a fixed set of test queries
```

## Scheme data

- The `Scheme` model is in `backend/models/scheme.py`; the dataset is `data/schemes.json`; see `DATA_GUIDE.md`.
- Scheme facts (amounts, income limits, age limits, eligibility) are entered by the project owner from official sources. **Never invent or guess real scheme details** — use `null` or clearly marked `PLACEHOLDER` text.

## Search (RAG retrieval)

- `backend/services/embeddings.py` loads the shared multilingual embedding model (`paraphrase-multilingual-MiniLM-L12-v2`, ~470 MB, downloaded once and cached).
- `backend/services/ingest.py` builds one search document per scheme (name, description, benefits, and a plain-language eligibility summary) and rebuilds the persistent ChromaDB collection at `data/chroma/` (gitignored — never commit it; re-run `scripts/ingest.py` after any change to `data/schemes.json`).
- `backend/services/retriever.py`'s `search_schemes(query, top_k=5, category=None)` embeds the query and returns the closest schemes with a cosine-similarity `score` (1.0 = identical meaning, 0.0 = unrelated). The model and the Chroma collection are loaded once per process, not per call.
- Hinglish written in Roman script (e.g. "budhape mein pension") embeds poorly with this model. Step 4's LLM is expected to rewrite the user's question into clear Hindi/English before it reaches `search_schemes`, rather than the retriever trying to handle Romanized Hinglish itself.
