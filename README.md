# Yojana Saathi (योजना साथी)

An AI assistant that helps Indian citizens — especially in rural areas — find government
schemes they may be eligible for, in **Hindi, Hinglish, and English**, with voice support.

Python decides eligibility; the LLM only explains. Every eligibility rule is checked by a
plain, testable Python engine against official scheme data — Gemini never decides who is
eligible, it just turns that decision into a simple, friendly reply.

## Stack (all free)

- **Backend:** Python 3.14, FastAPI
- **Vector DB:** ChromaDB (persistent, local)
- **Embeddings:** `paraphrase-multilingual-MiniLM-L12-v2` (sentence-transformers), runs locally on CPU
- **LLM:** Google Gemini API (free tier)
- **Frontend:** plain HTML + Tailwind CSS + vanilla JS
- **Voice:** browser Web Speech API
- **Deployment target:** Hugging Face Spaces (Docker)

## How it works

1. **Understand** — one Gemini call turns a raw message into structured facts (only what
   the user actually said, never guessed), plus English/Hindi search queries.
2. **Search** — a multilingual embedding search over `data/schemes.json` finds candidate
   schemes, in whichever language the user asked in.
3. **Decide** — a rule-based Python engine (`backend/services/eligibility.py`) checks the
   candidates against the user's known facts and each scheme's official eligibility rules,
   sorting them into eligible / possibly eligible / not eligible, with plain-English
   reasons and, if something is missing, the single most useful follow-up question.
4. **Reply** — a second Gemini call explains that decision in the user's own language and
   tone, always including the scheme's official link, and never asking for Aadhaar, bank,
   or phone details.

## Getting started

See [`SETUP.md`](SETUP.md) for the full virtual-environment setup guide. Quick version:

```
py -3.14 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then fill in GEMINI_API_KEY
```

Then, to try it out:

```
python scripts/ingest.py       # build the search index from data/schemes.json
python scripts/chat_cli.py     # chat with the assistant in the terminal
```

## Project layout

```
backend/
  app/        FastAPI app: entrypoint, routes, config loading
  services/   Business logic: retrieval, embeddings, Gemini calls, eligibility
  models/     Pydantic schemas (scheme records, user profile)
  prompts/    System prompt(s) for Gemini
  tests/      pytest tests
data/         Scheme data (data/schemes.json) and its source documents
frontend/     Static HTML/CSS/JS served to the browser
scripts/      CLI tools: validate data, build the search index, run test queries, chat
```

See [`CLAUDE.md`](CLAUDE.md) for detailed coding rules and architecture notes, and
[`DATA_GUIDE.md`](DATA_GUIDE.md) for how scheme data is added and verified.

## Data integrity

Every scheme in `data/schemes.json` is entered from official government sources, with the
source documents kept alongside it under `data/sources/`. No amounts, dates, or
eligibility rules are ever invented or guessed — a field is left `null` rather than
estimated when the official source doesn't state it.

## Testing

```
pytest
```

All Gemini calls are mocked in tests — no real API calls are made by the test suite.
