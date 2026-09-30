"""LLM wrapper with an offline fallback so the project runs without any API key."""
import json, os, re, random
import httpx

KEY = lambda: os.getenv("ANTHROPIC_API_KEY", "")
MODEL = lambda: os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

def llm_enabled() -> bool: return bool(KEY())

def _call(system: str, user: str, max_tokens=1500) -> str:
    r = httpx.post("https://api.anthropic.com/v1/messages", timeout=60,
        headers={"x-api-key": KEY(), "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL(), "max_tokens": max_tokens, "system": system,
              "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"])

GROUND = ("Answer ONLY from the numbered context passages. Cite passages like [1]. "
          "If the context does not contain the answer, reply exactly: I can't find that in your documents.")

def answer(question: str, passages: list[str]) -> str:
    if llm_enabled():
        ctx = "\n\n".join(f"[{i+1}] {p}" for i, p in enumerate(passages))
        return _call(GROUND, f"Context:\n{ctx}\n\nQuestion: {question}")
    # Offline extractive mode: return the most relevant sentences verbatim.
    qwords = set(re.findall(r"\w{4,}", question.lower()))
    sents = [s for p in passages for s in re.split(r"(?<=[.!?])\s+", p)]
    sents.sort(key=lambda s: -len(qwords & set(re.findall(r"\w{4,}", s.lower()))))
    return "(offline mode) " + " ".join(sents[:3])

def _json(text: str):
    return json.loads(re.search(r"[\[{].*[\]}]", text, re.S).group(0))

def make_quiz(passages: list[str], n: int) -> list[dict]:
    if llm_enabled():
        try:
            out = _call("Return ONLY JSON: a list of objects {question, options:[4 strings], answer_index:int, explanation}. "
                        "Base every question strictly on the text.", f"Make {n} multiple-choice questions from:\n" + "\n\n".join(passages))
            return _json(out)[:n]
        except Exception: pass  # fall through to offline generator
    return _cloze_quiz(passages, n)

def make_flashcards(passages: list[str], n: int) -> list[dict]:
    if llm_enabled():
        try:
            out = _call("Return ONLY JSON: a list of {front, back}. Use only the text.", f"Make {n} flashcards from:\n" + "\n\n".join(passages))
            return _json(out)[:n]
        except Exception: pass
    return [{"front": q["question"], "back": q["options"][q["answer_index"]]} for q in _cloze_quiz(passages, n)]

def _cloze_quiz(passages, n):
    """Offline fallback: blank out a key term in a sentence; distractors are other terms from the text."""
    sents = [s.strip() for p in passages for s in re.split(r"(?<=[.!?])\s+", p) if 40 < len(s) < 250]
    vocab = sorted({w for s in sents for w in re.findall(r"\b[A-Za-z]{6,}\b", s)})
    rng, qs = random.Random(1), []
    for s in sents:
        words = [w for w in re.findall(r"\b[A-Za-z]{6,}\b", s)]
        if not words or len(vocab) < 4: continue
        ans = max(words, key=len)
        opts = rng.sample([w for w in vocab if w != ans], 3) + [ans]; rng.shuffle(opts)
        qs.append({"question": s.replace(ans, "_____", 1), "options": opts,
                   "answer_index": opts.index(ans), "explanation": f"Source sentence: {s}"})
        if len(qs) >= n: break
    return qs
