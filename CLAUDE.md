# Yojana Saathi

AI assistant that helps Indian citizens (especially rural users) discover government schemes they are eligible for. It understands Hindi, Hinglish, and English, and supports voice input/output.

## Stack (all free)

- **Backend:** Python 3.14, FastAPI (confirmed: `requirements.txt` installs cleanly on 3.14, including torch, sentence-transformers and chromadb — stay on 3.14)
- **Vector DB:** ChromaDB (persistent, local)
- **Embeddings:** sentence-transformers multilingual model, run locally
- **LLM:** Google Gemini API (free tier), model `gemini-3.5-flash-lite`, configured via `GEMINI_API_KEY` / `GEMINI_MODEL`
- **Frontend:** plain HTML + Tailwind CSS + vanilla JS (no build step)
- **Voice:** browser Web Speech API (speech recognition + synthesis)
- **Deployment:** Hugging Face Spaces (Docker)

## Folder structure

```
backend/
  app/        FastAPI app: entrypoint, routes, config loading (backend/app/config.py)
  services/   Business logic: retrieval, embeddings, Gemini calls, eligibility
  models/     Pydantic schemas (request/response, scheme records)
  prompts/    System prompt(s) for Gemini, as plain Markdown files
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

- **Real Gemini calls are budgeted: at most 20 per day, at least 8 seconds apart** (standing rule from the project owner; failed or timed-out calls count too). The free tier's daily quota is small (a real 429 on the old `gemini-3.8-flash` reported "limit: 20 requests per day on Free Tier"). Mocked tests (`backend/tests/`) are the default; spend real calls only where they give real signal, mainly `scripts/e2e_conversation.py` (3 calls per run), which counts every call in a local ledger (`.cache/gemini_calls.json`, gitignored) and refuses to go past the limit. When the pipeline did not change, use `--replay` (free) instead.
- **Model:** `gemini-3.5-flash-lite` (stable; supports structured JSON output and `thinking_level`). We switched from `gemini-3.8-flash` because its free tier allows only about 20 requests per day, and every chat turn makes 2 calls (understand + reply), so that was only ~10 conversation turns a day. Flash-Lite's free tier allows about 500 requests per day (~250 turns). These limits are approximate and change; see the AI Studio rate-limit page for the current ones. Gemini 2.0 models are shut down and 2.5 models are being shut down, so never use them.
- **Check the docs first:** before writing any Gemini code, read the current official docs at https://ai.google.dev/gemini-api/docs/latest-model. The SDK and API have changed recently, so do not rely on older examples from memory.
- **Thinking level:** use `thinking_level` set to `"low"` for chat responses to keep replies fast.
- **All calls go through `backend/services/llm.py`** (`generate_text` / `generate_structured`) — nothing else imports `google.genai` directly. It uses `client.interactions.create(...)`, a top-level `system_instruction`, and `generation_config={"thinking_level": ...}`; it never passes `temperature`, `top_p`, `top_k`, `candidate_count`, or `thinking_budget` (all deprecated/unsupported on Gemini 3+).
- **SDK version matters:** the Interactions API had a breaking change in May 2026 that requires `google-genai>=2.0`; an older 1.x SDK gets a clear 400 error telling you to upgrade. If a Gemini call fails with a confusing error, check the installed SDK version first (`pip show google-genai`) before assuming the code is wrong.
- The SDK's own HTTP error classes live in a private module that can move between versions, so `llm.py` detects failures via the exception's `status_code` attribute rather than importing those classes.
- **The SDK has its own internal retry loop, and it must stay disabled.** Left on, it once turned a single 429 into a real multi-minute hang, because `llm.py`'s own retry loop would retry a call that was itself silently retrying inside the SDK. `_get_client()` disables this via `HttpOptions(retry_options=HttpRetryOptions(attempts=0))`, so `llm.py`'s `MAX_ATTEMPTS`/`RETRY_DELAYS_SECONDS` are the only retry logic that ever runs, bounding the whole call (including retries) to well under 30 seconds.

## Common commands

```
python -m venv venv && venv\Scripts\activate    # Windows
pip install -r requirements.txt
uvicorn backend.app.main:app --reload
pytest
python scripts/validate_data.py                 # validate data/schemes.json
python scripts/ingest.py                        # (re)build the ChromaDB search index from schemes.json
python scripts/test_search.py                    # print search results for a fixed set of test queries
python scripts/chat_cli.py                       # chat with the assistant in the terminal
python scripts/e2e_conversation.py               # live scripted conversation with profile checks (3 real calls)
python scripts/e2e_conversation.py --replay      # same conversation from recorded Gemini answers (free; also run by pytest)
```

## Scheme data

- The `Scheme` model is in `backend/models/scheme.py`; the dataset is `data/schemes.json`; see `DATA_GUIDE.md`.
- Scheme facts (amounts, income limits, age limits, eligibility) are entered by the project owner from official sources. **Never invent or guess real scheme details** — use `null` or clearly marked `PLACEHOLDER` text.

## Search (RAG retrieval)

- `backend/services/embeddings.py` loads the shared multilingual embedding model (`paraphrase-multilingual-MiniLM-L12-v2`, ~470 MB, downloaded once and cached).
- `backend/services/ingest.py` builds one search document per scheme (name, description, benefits, and a plain-language eligibility summary) and rebuilds the persistent ChromaDB collection at `data/chroma/` (gitignored — never commit it; re-run `scripts/ingest.py` after any change to `data/schemes.json`).
- `backend/services/retriever.py`'s `search_schemes(query, top_k=5, category=None)` embeds the query and returns the closest schemes with a cosine-similarity `score` (1.0 = identical meaning, 0.0 = unrelated). The model and the Chroma collection are loaded once per process, not per call.
- Hinglish written in Roman script (e.g. "budhape mein pension") embeds poorly with this model. Step 4's LLM is expected to rewrite the user's question into clear Hindi/English before it reaches `search_schemes`, rather than the retriever trying to handle Romanized Hinglish itself.

## Chat pipeline (Gemini + eligibility + search)

One chat turn (`backend/services/assistant.py`'s `handle_message`) runs, in order:

0. `backend/services/quick_answer.py` — if the message is a bare yes/no (English, Hinglish or Devanagari) and the last question's field key is known, it is answered in Python and step 1 is skipped (saves one Gemini call per such turn).
1. `backend/services/understand.py` — one structured Gemini call turns the raw message into `profile_updates` (only facts actually stated, never guessed), `search_query_en`/`search_query_hi`, and `language_style`.
2. The new `profile_updates` are merged onto the running `UserProfile` (a fact learned in an earlier turn is never overwritten by "unknown" in a later one).
3. `search_schemes` runs with both queries; the best score per scheme is kept, and anything below 0.35 is dropped (see Step 3's report for why).
4. `backend/services/eligibility.py`'s `check_eligibility` (pure Python, no LLM) decides each matched scheme's status.
5. A second Gemini call (`backend/prompts/system_prompt.md` as the system instruction) writes the reply from that decided status — **Gemini explains, it never decides eligibility.**

`scripts/chat_cli.py` chats with this pipeline in the terminal, for manual testing. It prints our own warnings (e.g. a dropped profile field), `debug` shows the live profile first, and `YS_DEBUG_RAW=1` also prints Gemini's raw `profile_updates` each turn (local debugging only, off by default).

Gemini's `profile_updates` are parsed leniently in `understand.py`: nulls mean "not mentioned", exclusion answers are accepted with an `exclusion:` prefix, at the top level, or as "yes"/"no" strings, and anything invalid is dropped one field (or one exclusion key) at a time, never the whole update. A land size stated with a clear unit ("1 hectare", "2 acres", "2 एकड़") is read in Python by `backend/services/land.py` (exact acre conversion; local units like bigha are never converted), because Flash-Lite often leaves `landholding_hectares` out. The schema sent to Gemini replaces the unsupported `exclusiveMinimum`/`exclusiveMaximum` with `minimum`/`maximum`, lists every exclusion key explicitly, and each turn passes the last question's field key (`next_question_field`) so Gemini knows exactly where a yes/no answer goes.
