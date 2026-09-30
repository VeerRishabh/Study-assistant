import json, os
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from . import llm, retrieval
from .auth import get_db, current_user, hash_password, verify_password, new_token
from .db import init_db, User, Document, Chunk, Flashcard, Quiz, QuizAttempt
from .ingest import extract_pages, chunk_text

app = FastAPI(title="Study Assistant")
init_db()
MAX_UPLOAD = 20 * 1024 * 1024
STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
def min_score(): return float(os.getenv("MIN_SCORE", "0.08"))

class Creds(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8)

@app.post("/register")
def register(c: Creds, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.username == c.username)):
        raise HTTPException(409, "Username taken")
    u = User(username=c.username, password_hash=hash_password(c.password)); db.add(u); db.commit()
    return {"token": new_token(db, u)}

@app.post("/login")
def login(c: Creds, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.username == c.username))
    if not u or not verify_password(c.password, u.password_hash):
        raise HTTPException(401, "Bad credentials")
    return {"token": new_token(db, u)}

@app.post("/documents")
async def upload(file: UploadFile = File(...), user=Depends(current_user), db: Session = Depends(get_db)):
    data = await file.read()
    if len(data) > MAX_UPLOAD: raise HTTPException(413, "File too large (max 20MB)")
    try: pages = extract_pages(file.filename, data)
    except Exception as e: raise HTTPException(400, f"Could not read file: {e}")
    chunks = chunk_text(pages)
    if not chunks: raise HTTPException(422, "No extractable text (scanned PDF? you need OCR)")
    doc = Document(user_id=user.id, filename=file.filename); db.add(doc); db.flush()
    db.add_all(Chunk(document_id=doc.id, user_id=user.id, idx=i, page=p, text=t) for i, (p, t) in enumerate(chunks))
    db.commit()
    return {"id": doc.id, "filename": doc.filename, "chunks": len(chunks)}

@app.get("/documents")
def list_docs(user=Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": d.id, "filename": d.filename} for d in db.scalars(select(Document).where(Document.user_id == user.id))]

@app.delete("/documents/{doc_id}")
def delete_doc(doc_id: int, user=Depends(current_user), db: Session = Depends(get_db)):
    d = db.get(Document, doc_id)
    if not d or d.user_id != user.id: raise HTTPException(404, "Not found")
    db.delete(d); db.commit(); return {"deleted": doc_id}

def _chunks(db, user, doc_id=None):
    q = select(Chunk).where(Chunk.user_id == user.id)  # always scoped to the user
    if doc_id is not None: q = q.where(Chunk.document_id == doc_id)
    return list(db.scalars(q.order_by(Chunk.document_id, Chunk.idx)))

class Ask(BaseModel):
    question: str
    document_id: int | None = None
    k: int = 4

@app.post("/ask")
def ask(a: Ask, user=Depends(current_user), db: Session = Depends(get_db)):
    hits = retrieval.rank(a.question, _chunks(db, user, a.document_id), a.k)
    # Grounding gate #1: refuse before calling the LLM if nothing relevant was retrieved.
    if not hits or hits[0][1] < min_score():
        return {"answer": "I can't find that in your documents.", "sources": [], "grounded": False}
    text = llm.answer(a.question, [c.text for c, _ in hits])
    return {"answer": text, "grounded": True, "llm": llm.llm_enabled(),
            "sources": [{"document_id": c.document_id, "page": c.page, "score": round(s, 3), "snippet": c.text[:200]} for c, s in hits]}

@app.get("/search")
def search(q: str, user=Depends(current_user), db: Session = Depends(get_db)):
    hits = retrieval.rank(q, _chunks(db, user), 10)
    return [{"document_id": c.document_id, "page": c.page, "score": round(s, 3), "snippet": c.text[:250]} for c, s in hits]

class Gen(BaseModel):
    document_id: int
    count: int = Field(5, ge=1, le=20)

def _doc_passages(db, user, doc_id):
    ch = _chunks(db, user, doc_id)
    if not ch: raise HTTPException(404, "Document not found")
    return [c.text for c in ch[:12]]

@app.post("/quizzes")
def gen_quiz(g: Gen, user=Depends(current_user), db: Session = Depends(get_db)):
    qs = llm.make_quiz(_doc_passages(db, user, g.document_id), g.count)
    if not qs: raise HTTPException(422, "Could not generate questions from this document")
    quiz = Quiz(user_id=user.id, document_id=g.document_id, questions_json=json.dumps(qs)); db.add(quiz); db.commit()
    # answers are withheld; grading happens server-side
    return {"quiz_id": quiz.id, "questions": [{"question": q["question"], "options": q["options"]} for q in qs]}

class Submit(BaseModel):
    answers: list[int]

@app.post("/quizzes/{quiz_id}/submit")
def submit(quiz_id: int, s: Submit, user=Depends(current_user), db: Session = Depends(get_db)):
    quiz = db.get(Quiz, quiz_id)
    if not quiz or quiz.user_id != user.id: raise HTTPException(404, "Quiz not found")
    qs = json.loads(quiz.questions_json)
    if len(s.answers) != len(qs): raise HTTPException(422, f"Expected {len(qs)} answers")
    correct = sum(1 for a, q in zip(s.answers, qs) if a == q["answer_index"])
    db.add(QuizAttempt(quiz_id=quiz.id, user_id=user.id, score=correct, total=len(qs))); db.commit()
    return {"score": correct, "total": len(qs),
            "review": [{"correct_index": q["answer_index"], "explanation": q.get("explanation", "")} for q in qs]}

@app.get("/stats")
def stats(user=Depends(current_user), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(QuizAttempt).where(QuizAttempt.user_id == user.id).order_by(QuizAttempt.created)))
    return {"attempts": len(rows),
            "average_pct": round(100 * sum(r.score / r.total for r in rows) / len(rows), 1) if rows else None,
            "history": [{"quiz_id": r.quiz_id, "score": r.score, "total": r.total, "at": r.created.isoformat()} for r in rows]}

@app.post("/flashcards")
def gen_cards(g: Gen, user=Depends(current_user), db: Session = Depends(get_db)):
    cards = llm.make_flashcards(_doc_passages(db, user, g.document_id), g.count)
    rows = [Flashcard(user_id=user.id, document_id=g.document_id, front=c["front"], back=c["back"]) for c in cards]
    db.add_all(rows); db.commit()
    return [{"id": r.id, "front": r.front, "back": r.back} for r in rows]

@app.get("/flashcards")
def list_cards(user=Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": r.id, "front": r.front, "back": r.back} for r in db.scalars(select(Flashcard).where(Flashcard.user_id == user.id))]

@app.get("/health")
def health(): return {"ok": True, "llm": llm.llm_enabled()}

app.mount("/static", StaticFiles(directory=STATIC), name="static")
@app.get("/")
def index(): return FileResponse(os.path.join(STATIC, "index.html"))
