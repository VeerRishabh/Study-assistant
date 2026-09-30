# 🧠 Personal AI Study Assistant

Upload PDFs/notes, ask questions grounded in them, generate quizzes and flashcards, and track quiz performance.

## Features (all implemented)
User accounts (PBKDF2 passwords, bearer tokens) · PDF/text upload · page-aware chunking · search across all documents · grounded Q&A with sources · quiz generation + server-side grading · flashcards · performance stats · per-user data isolation · runs **without an API key** (offline mode).

## Run it
```bash
cd study-assistant
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # optional
uvicorn app.main:app --reload
```
Open http://localhost:8000 (simple UI) or http://localhost:8000/docs (interactive API docs).

**Environment variables** (export them or use `set -a; source .env; set +a`; the app does not auto-read `.env`):

| Var | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./study.db` | Use `postgresql+psycopg2://user:pass@host:5432/db` for PostgreSQL (`pip install psycopg2-binary`) |
| `ANTHROPIC_API_KEY` | unset | Enables LLM answers/quizzes. Unset = offline mode |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-5` | Any model id your key can use |
| `MIN_SCORE` | `0.08` | Retrieval similarity below which the app refuses to answer |

**Test:** `pytest -q` (uses a temp database).

## Try it with curl
```bash
B=http://localhost:8000
TOKEN=$(curl -s -X POST $B/register -H 'content-type: application/json' -d '{"username":"alice","password":"password123"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
curl -s -X POST $B/documents -H "Authorization: Bearer $TOKEN" -F file=@lecture.pdf
curl -s -X POST $B/ask -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{"question":"What is the Krebs cycle?"}'
curl -s -X POST $B/quizzes -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{"document_id":1,"count":5}'
```

## API
| Method & path | Purpose |
|---|---|
| `POST /register`, `POST /login` | JSON `{username,password}` returns `{token}` |
| `POST /documents` (multipart `file`) | Upload `.pdf`/`.txt`. `GET /documents`, `DELETE /documents/{id}` |
| `POST /ask` `{question, document_id?, k?}` | Grounded answer + sources + `grounded` flag |
| `GET /search?q=` | Ranked passages across all your documents |
| `POST /quizzes` `{document_id,count}` | Returns questions **without** answers |
| `POST /quizzes/{id}/submit` `{answers:[idx,...]}` | Graded server-side and recorded |
| `GET /stats` | Attempt count, average %, history |
| `POST /flashcards`, `GET /flashcards` | Generate / list |

## Architecture
```
Browser / curl
     │  Bearer token
     ▼
 FastAPI (main.py) ── auth.py (PBKDF2, tokens)
     │
     ├─ ingest.py    PDF → pages → sentence-aware chunks (900 chars, 150 overlap)
     ├─ retrieval.py TF-IDF cosine similarity over the USER'S chunks only
     ├─ llm.py       grounded prompt  |  offline extractive/cloze fallback
     └─ db.py        SQLAlchemy → SQLite / PostgreSQL
```

## How hallucinations are reduced (the portfolio write-up)
1. **Retrieval first, generation second.** The model never sees the whole corpus, only the top-k retrieved passages.
2. **Relevance gate before the LLM.** If the best similarity is under `MIN_SCORE`, the API answers "I can't find that in your documents" *without calling the model at all*. Test: `test_flow` asks an off-topic question and asserts `grounded == False`.
3. **Constrained prompt.** The model is told to use only the numbered passages, cite `[n]`, and emit a fixed refusal when the context lacks the answer.
4. **Verifiable output.** Each answer returns source document, page, score and snippet so a human can check it.
5. **Quizzes are generated from the text and graded server-side**, so the answer key can't be tampered with from the client.

**Limitations:** TF-IDF is lexical, so "automobile" won't match "car" (use embeddings to fix this). Scanned PDFs need OCR. Retrieval rebuilds the index per query (fine for hundreds of chunks; cache or use pgvector beyond that). Tokens never expire. The quiz generator reads only the first 12 chunks of a document.

## Troubleshooting: errors you are likely to hit
| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeError: Form data requires "python-multipart" to be installed` | Missing dependency | `pip install python-multipart` |
| `ModuleNotFoundError: No module named 'app'` | Running from the wrong folder | `cd study-assistant` first; run `uvicorn app.main:app`, not `python main.py` |
| `ImportError: attempted relative import with no known parent package` | Ran `python app/main.py` | Use `uvicorn app.main:app` |
| `ModuleNotFoundError: psycopg2` | PostgreSQL URL without a driver | `pip install psycopg2-binary` |
| `OperationalError: connection refused` (Postgres) | Server not running / wrong host / DB doesn't exist | Start Postgres, `createdb study`, check the URL |
| `sqlite3.OperationalError: database is locked` | Two writers/processes or `--reload` plus a shell holding the DB | Stop other processes; use PostgreSQL for concurrency |
| `ERROR: [Errno 98] Address already in use` | Port 8000 busy | `uvicorn app.main:app --port 8001` |
| `401 Missing 'Authorization: Bearer ...'` | No/bad header | Send `-H "Authorization: Bearer $TOKEN"` (note the space) |
| `409 Username taken` on register | User exists | Use `/login` |
| `422 No extractable text` | Scanned/image-only PDF | OCR first (e.g. `ocrmypdf in.pdf out.pdf`) |
| `400 Could not read file: EOF marker not found` | Corrupt/encrypted PDF | Re-export the PDF, remove its password |
| `/ask` always says "I can't find that" | Question words don't appear in notes (lexical search) or `MIN_SCORE` too high | Rephrase with terms from the notes; lower `MIN_SCORE` to `0.03`; or add embeddings |
| Answers start with `(offline mode)` | No `ANTHROPIC_API_KEY` | Export the key **in the same shell** that runs uvicorn |
| Quiz silently looks like fill-in-the-blank | LLM call failed, so the fallback generator was used | Check key/model name/network; the fallback is deliberate |
| `httpx.HTTPStatusError 401/404` from LLM | Bad key or unknown model id | Fix `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` |
| Browser UI: "Not Found" for static files | Started uvicorn from another directory | Start it from `study-assistant/` |
| Tests fail with `no such table` | `DATABASE_URL` set before import was overridden | Run `pytest` from the project root with no `DATABASE_URL` exported |

## Ideas to extend
Embeddings + pgvector, refresh-token expiry, rate limiting, background ingestion for big PDFs, spaced-repetition scheduling for flashcards.
