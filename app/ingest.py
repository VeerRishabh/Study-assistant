"""PDF/text extraction and chunking."""
import io, re
from pypdf import PdfReader

def extract_pages(filename: str, data: bytes) -> list[tuple[int, str]]:
    if filename.lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        return [(i + 1, (p.extract_text() or "")) for i, p in enumerate(reader.pages)]
    return [(1, data.decode("utf-8", errors="replace"))]

def chunk_text(pages, size=900, overlap=150):
    """Sentence-aware sliding window. Returns [(page, text)]."""
    out = []
    for page, text in pages:
        text = re.sub(r"\s+", " ", text).strip()
        if not text: continue
        sents, buf = re.split(r"(?<=[.!?])\s+", text), ""
        for s in sents:
            if len(buf) + len(s) > size and buf:
                out.append((page, buf.strip()))
                buf = buf[-overlap:] + " "
            buf += s + " "
        if buf.strip(): out.append((page, buf.strip()))
    return out
