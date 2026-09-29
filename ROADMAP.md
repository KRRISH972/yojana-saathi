# Roadmap

Each step: plan briefly, build, run all tests and the end-to-end conversation
(`scripts/e2e_conversation.py`, live when the pipeline changed, `--replay` otherwise),
fix until green, commit and push, then tick it off here.

## Done

- [x] **Step 1 — Scheme data layer.** `Scheme` model, validator, PM-KISAN and PM-KMY from official sources.
- [x] **Step 2 — Eligibility engine.** Pure-Python rules: eligible / possibly eligible / not eligible, plus the most useful next question.
- [x] **Step 3 — RAG search.** Multilingual embeddings + ChromaDB.
- [x] **Step 4 — Gemini chat pipeline.** Understand → merge profile → search → eligibility → Gemini writes the reply.
- [x] **Step 4b — Hardening.** Contradiction-safe profile merge, lenient parsing of Gemini's answers, yes/no answered in Python, land size read in Python, scripted end-to-end conversation (live + offline replay).

## Remaining

- [x] **Step 5 — FastAPI backend.** `POST /api/chat` (stateless: the browser keeps the conversation state and sends it back each turn), `GET /api/health`, friendly error responses for Gemini failures and rate limits, a per-visitor rate limit to protect the free quota, API tests, and the e2e conversation run through the API.
- [ ] **Step 6 — Mobile-first web UI.** Plain HTML + Tailwind + vanilla JS served by FastAPI; chat screen, scheme result cards with official links, Hindi/English voice input (Web Speech recognition) and spoken replies (speech synthesis), works on low-end phones and slow networks.
- [ ] **Step 7 — Evaluation + CI.** An offline evaluation set (understanding, yes/no shortcut, land parsing, eligibility, search) with a printed score report, and GitHub Actions running the tests and the replayed conversation on every push.
- [ ] **Step 8 — Docker + Hugging Face Spaces + README.** Dockerfile (embedding model baked into the image), Spaces config, and a README with architecture, setup, and screenshots. Creating the Hugging Face account/Space and adding the API key as a Space secret is done by the project owner.
