"""BM25 search over one task's own paragraphs.

Every task brings its own ten paragraphs, so the paragraphs are an explicit argument
rather than a process-wide index. That also means they enter the node's content hash:
the same query over a different task's paragraphs can never collide in the cache.
"""

from __future__ import annotations

import re

from rank_bm25 import BM25Okapi

# Hyphens and dots are kept inside tokens (identifiers, decimals), stripped at the edges.
_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9._-]+")
_EDGE_PUNCT = re.compile(r"^[._-]+|[._-]+$")

# Deliberately short: BM25's IDF already discounts common terms.
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for
from by with without is are was were be been being am do does did doing
have has had having i you he she it we they me him her them my your his
its our their what which who whom how when where why can could should
would will shall may might must please tell show give me us about
""".split())  # noqa: SIM905


def tokenize(text: str) -> list[str]:
    """Lowercased, punctuation-stripped tokens. Used for BOTH paragraphs and queries:
    an index and a query tokenised differently cannot match."""
    out: list[str] = []
    for raw in _TOKEN_SPLIT.split(text or ""):
        token = _EDGE_PUNCT.sub("", raw).lower()
        if token:
            out.append(token)
    return out


def content_terms(query: str) -> list[str]:
    """Query tokens that carry meaning, in order, without duplicates."""
    seen: set[str] = set()
    out: list[str] = []
    for token in tokenize(query):
        if token in _STOPWORDS or len(token) < 2 or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def search(query: str, paragraphs: list[dict], k: int = 3, exclude: list[int] | None = None) -> list[dict]:
    """Top-k of `paragraphs` ([{"title", "text"}]), best first, as [{"id", "title", "text", "score"}].
    `exclude`: paragraph ids already read, so a later search brings new ones."""
    tokenized = [tokenize(f"{p['title']} {p['text']}") for p in paragraphs]
    terms = content_terms(query)
    if not terms or not any(tokenized):
        return []
    scores = BM25Okapi(tokenized).get_scores(terms)
    skip = set(exclude or [])
    # a paragraph sharing no query word is not a result
    hits = [i for i, doc in enumerate(tokenized) if i not in skip and any(t in doc for t in terms)]
    hits.sort(key=lambda i: (-scores[i], i))
    return [
        {"id": i, "title": paragraphs[i]["title"], "text": paragraphs[i]["text"], "score": round(float(scores[i]), 4)}
        for i in hits[: max(0, int(k))]
    ]
