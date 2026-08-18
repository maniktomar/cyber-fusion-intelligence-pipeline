"""A small lexical retriever over the synthetic knowledge base.

Why BM25 and not embeddings: this knowledge base has eight articles. An
embedding index would add a second network dependency and a second failure mode
to a corpus that fits in a single prompt, and it would make retrieval
non-deterministic across model versions -- which makes the fallback tests
harder to write and harder to trust. Lexical scoring is deterministic, has no
external dependency, and is honestly good enough at this size. At a few
thousand articles this choice flips; that trade-off is in the README.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Words that appear in nearly every support ticket and carry no signal here.
_STOPWORDS = frozenset(
    """a an and are as at be been but by can cannot did do does for from get got
    has have i if in is it its me my no not of on or our so than that the their
    them then there they this to up us was we were what when where which who will
    with would you your""".split()
)


def _stem(token: str) -> str:
    """A deliberately small suffix stripper.

    Without this, "charged" never matches an article that says "charges" and
    the ticket retrieves nothing -- which the grounding gate then correctly
    turns into a fallback. Under-retrieval is therefore not a quiet quality
    problem here, it is a route straight to manual triage, so morphological
    matching has to work.

    Not a full Porter stemmer: at eight articles the extra rules buy nothing
    and each one is another way to collide two unrelated words.
    """
    if len(token) <= 3 or token.isdigit():
        return token
    for suffix, replacement in (("ies", "y"), ("ing", ""), ("es", ""), ("ed", ""), ("s", "")):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)] + replacement
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase, drop stopwords, stem, then drop stopwords again.

    The second filter matters: stemming can turn a content word into a
    stopword ("getting" -> "get"), and leaving those in would let a term that
    is filtered out of the documents survive in the query.
    """
    raw = (t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS)
    stemmed = (_stem(t) for t in raw)
    return [t for t in stemmed if t not in _STOPWORDS]


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    category: str
    problem: str
    resolution: str

    @property
    def searchable_text(self) -> str:
        return f"{self.title} {self.problem} {self.resolution}"

    def as_prompt_block(self) -> str:
        return (
            f"[{self.id}] {self.title}\n"
            f"  Problem: {self.problem}\n"
            f"  Resolution: {self.resolution}"
        )


@dataclass(frozen=True)
class ScoredArticle:
    article: Article
    score: float


class KnowledgeBase:
    """Loads the synthetic corpus and ranks it against a query with BM25."""

    K1 = 1.5
    B = 0.75

    def __init__(self, articles: list[Article], *, synthetic: bool = True) -> None:
        if not articles:
            raise ValueError("Knowledge base is empty; retrieval would always fail.")
        self.articles = articles
        self.synthetic = synthetic
        self._tokens = {a.id: tokenize(a.searchable_text) for a in articles}
        self._lengths = {aid: len(toks) for aid, toks in self._tokens.items()}
        self._avg_length = sum(self._lengths.values()) / len(self._lengths)
        self._doc_freq: Counter[str] = Counter()
        for toks in self._tokens.values():
            self._doc_freq.update(set(toks))

    @classmethod
    def from_file(cls, path: str | Path) -> KnowledgeBase:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        articles = [
            Article(
                id=item["id"],
                title=item["title"],
                category=item["category"],
                problem=item["problem"],
                resolution=item["resolution"],
            )
            for item in payload["articles"]
        ]
        return cls(articles, synthetic=payload.get("synthetic", True))

    def search(self, query: str, *, limit: int = 3) -> list[ScoredArticle]:
        """Top `limit` articles by BM25, best first. Zero-scoring hits are dropped."""
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        n = len(self.articles)
        scored: list[ScoredArticle] = []
        for article in self.articles:
            counts = Counter(self._tokens[article.id])
            length = self._lengths[article.id]
            score = 0.0
            for term in set(query_tokens):
                tf = counts.get(term, 0)
                if tf == 0:
                    continue
                df = self._doc_freq[term]
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                denominator = tf + self.K1 * (
                    1 - self.B + self.B * length / self._avg_length
                )
                score += idf * (tf * (self.K1 + 1)) / denominator
            if score > 0:
                scored.append(ScoredArticle(article=article, score=score))

        # Sort by score, then id, so equal scores rank deterministically.
        scored.sort(key=lambda s: (-s.score, s.article.id))
        return scored[:limit]

    def by_id(self, article_id: str) -> Article | None:
        return next((a for a in self.articles if a.id == article_id), None)

    def __len__(self) -> int:
        return len(self.articles)
