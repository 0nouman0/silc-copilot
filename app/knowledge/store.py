"""Document knowledge base (RAG) with metadata-aware chunking and retrieval.

Pipeline: register approved document -> parse metadata -> split by headings ->
index chunks with metadata -> retrieve with filters -> return chunks + source refs.

The default `KeywordStore` is dependency-free (BM25-style lexical scoring) so the
app runs anywhere. `KnowledgeStore` is the seam for a vector store: implement
`search()` with embeddings (pgvector / Pinecone) and swap it in `get_store()`.
Retrieved text is REFERENCE DATA only; it is never treated as instructions.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

DOCS_DIR = Path(__file__).parent / "docs"
_STOP = {"the", "a", "an", "of", "to", "is", "are", "and", "or", "in", "on", "for", "i", "my", "do", "what",
         "how", "can", "it", "me", "with", "if", "be", "this", "that", "at", "by", "from"}
MIN_SCORE = 1.0  # below this the result is treated as low-confidence / no result


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1]


@dataclass
class Chunk:
    doc_id: str
    title: str
    heading: str
    text: str
    category: str
    academic_year: str | None
    approved: bool
    demo: bool
    tokens: Counter = field(default_factory=Counter, repr=False)


@dataclass
class Hit:
    chunk: Chunk
    score: float


class KnowledgeStore(Protocol):
    def search(self, query: str, *, category: str | None = None, k: int = 4) -> list[Hit]: ...


def parse_document(raw: str, fallback_id: str) -> list[Chunk]:
    """Parse '---' front matter (key: value) then split the body by markdown headings."""
    meta: dict[str, str] = {}
    body = raw
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = raw[m.end():]
    doc_id = meta.get("id", fallback_id)
    title = meta.get("title", doc_id)
    chunks: list[Chunk] = []
    heading, buf = title, []

    def flush():
        text = "\n".join(buf).strip()
        if text:
            c = Chunk(doc_id=doc_id, title=title, heading=heading, text=text,
                      category=meta.get("category", "general"), academic_year=meta.get("academic_year"),
                      approved=meta.get("approved", "false").lower() == "true",
                      demo=meta.get("demo", "true").lower() == "true")
            c.tokens = Counter(_tokens(f"{heading} {text}"))
            chunks.append(c)

    for line in body.splitlines():
        h = re.match(r"^#{1,3}\s+(.*)", line)
        if h:
            flush()
            heading, buf = h.group(1).strip(), []
        else:
            buf.append(line)
    flush()
    return chunks


class KeywordStore:
    def __init__(self, chunks: list[Chunk] | None = None):
        self.chunks: list[Chunk] = chunks or []

    def ingest(self, raw: str, fallback_id: str) -> int:
        new = parse_document(raw, fallback_id)
        self.chunks.extend(new)
        return len(new)

    def search(self, query: str, *, category: str | None = None, k: int = 4) -> list[Hit]:
        q = _tokens(query)
        if not q:
            return []
        pool = [c for c in self.chunks if c.approved and (not category or c.category == category)]
        n = len(pool) or 1
        df = Counter(t for c in pool for t in set(c.tokens))
        hits = []
        for c in pool:
            s = sum((1 + math.log(c.tokens[t])) * (1 + math.log(1 + n / df[t])) for t in set(q) if c.tokens[t])
            if s >= MIN_SCORE:
                hits.append(Hit(c, round(s, 3)))
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:k]


_store: KeywordStore | None = None


def get_store() -> KnowledgeStore:
    global _store
    if _store is None:
        _store = KeywordStore()
        for p in sorted(DOCS_DIR.glob("*.md")):
            _store.ingest(p.read_text(encoding="utf-8"), p.stem)
    return _store
