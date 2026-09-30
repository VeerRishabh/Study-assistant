import os, tempfile
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/t.db"
os.environ.pop("ANTHROPIC_API_KEY", None)
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)
NOTES = (b"Mitochondria are organelles that produce ATP through oxidative phosphorylation. "
         b"The Krebs cycle takes place in the mitochondrial matrix and generates electron carriers. "
         b"Photosynthesis occurs in chloroplasts where chlorophyll absorbs sunlight to fix carbon dioxide. "
         b"Ribosomes translate messenger RNA into proteins during translation in the cytoplasm. ") * 2

def signup(name):
    r = c.post("/register", json={"username": name, "password": "password123"}); assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}

def test_flow():
    h = signup("alice")
    assert c.get("/documents").status_code == 401
    d = c.post("/documents", files={"file": ("bio.txt", NOTES)}, headers=h).json()
    r = c.post("/ask", json={"question": "Where does the Krebs cycle take place?"}, headers=h).json()
    assert r["grounded"] and "matrix" in r["answer"]
    r = c.post("/ask", json={"question": "Who won the 1966 World Cup?"}, headers=h).json()
    assert not r["grounded"]                                  # hallucination gate
    q = c.post("/quizzes", json={"document_id": d["id"], "count": 3}, headers=h).json()
    s = c.post(f"/quizzes/{q['quiz_id']}/submit", json={"answers": [0] * len(q["questions"])}, headers=h).json()
    assert s["total"] == len(q["questions"])
    assert c.get("/stats", headers=h).json()["attempts"] == 1
    assert len(c.post("/flashcards", json={"document_id": d["id"], "count": 2}, headers=h).json()) >= 1

def test_isolation():
    a, b = signup("carol"), signup("dave")
    c.post("/documents", files={"file": ("x.txt", NOTES)}, headers=a)
    assert c.get("/search", params={"q": "mitochondria"}, headers=b).json() == []   # dave can't see carol's notes
