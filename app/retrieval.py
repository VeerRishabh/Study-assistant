"""Retrieval: TF-IDF (word + bigram) cosine similarity, built per-user on demand.
Swap `rank` for an embedding model (sentence-transformers / pgvector) without touching callers."""
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

def rank(query: str, chunks: list, k: int = 4):
    """chunks: ORM Chunk rows. Returns [(chunk, score)] best first."""
    if not chunks: return []
    texts = [c.text for c in chunks]
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    try:
        m = vec.fit_transform(texts + [query])
    except ValueError:  # empty vocabulary (e.g. only stop-words)
        return []
    sims = cosine_similarity(m[-1], m[:-1]).ravel()
    order = sims.argsort()[::-1][:k]
    return [(chunks[i], float(sims[i])) for i in order if sims[i] > 0]
