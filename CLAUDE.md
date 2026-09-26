# Yojana Saathi

AI assistant that helps Indian citizens (especially rural users) discover government schemes they are eligible for. It understands Hindi, Hinglish, and English, and supports voice input/output.

## Stack (all free)

- **Backend:** Python 3.14, FastAPI
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
```
