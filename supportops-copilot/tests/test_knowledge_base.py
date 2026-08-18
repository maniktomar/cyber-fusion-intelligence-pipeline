"""Retrieval over the synthetic corpus."""

from __future__ import annotations

import json
import pathlib

import pytest

from app.triage.knowledge_base import Article, KnowledgeBase, tokenize

KB_PATH = "data/knowledge_base.json"


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase.from_file(KB_PATH)


def test_loads_every_article_from_disk(kb):
    assert len(kb) == 8
    assert kb.by_id("kb-001") is not None
    assert kb.by_id("kb-999") is None


def test_corpus_is_marked_synthetic(kb):
    """Guards the project's own rule: no real ticket data, ever."""
    assert kb.synthetic is True
    raw = json.loads(pathlib.Path(KB_PATH).read_text(encoding="utf-8"))
    assert "SYNTHETIC" in raw["_comment"]


def test_an_empty_corpus_is_refused():
    with pytest.raises(ValueError, match="empty"):
        KnowledgeBase([])


class TestSearch:
    def test_finds_the_obviously_relevant_article(self, kb):
        hits = kb.search("I was charged twice for the same month")
        assert hits[0].article.id == "kb-001"

    def test_finds_the_rate_limit_article(self, kb):
        hits = kb.search("my integration is getting 429 errors from the API")
        assert hits[0].article.id == "kb-004"

    def test_finds_the_two_factor_article(self, kb):
        hits = kb.search("lost my phone and cannot get past two-factor")
        assert hits[0].article.id == "kb-003"

    def test_respects_the_result_limit(self, kb):
        assert len(kb.search("refund billing charge account", limit=2)) == 2

    def test_returns_nothing_for_an_empty_query(self, kb):
        assert kb.search("") == []

    def test_returns_nothing_for_a_stopword_only_query(self, kb):
        assert kb.search("the and of it is") == []

    def test_returns_nothing_when_no_term_matches(self, kb):
        assert kb.search("photosynthesis chlorophyll stomata") == []

    def test_scores_descend(self, kb):
        hits = kb.search("refund charged twice on my subscription", limit=3)
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)

    def test_results_are_deterministic(self, kb):
        """Determinism is what makes the fallback tests trustworthy."""
        query = "password reset email never arrived"
        first = [h.article.id for h in kb.search(query)]
        for _ in range(5):
            assert [h.article.id for h in kb.search(query)] == first

    def test_ties_break_on_id_not_insertion_order(self):
        shared = "widget"
        articles = [
            Article(f"kb-{i}", shared, "other", shared, shared) for i in (3, 1, 2)
        ]
        hits = KnowledgeBase(articles).search(shared, limit=3)
        assert [h.article.id for h in hits] == ["kb-1", "kb-2", "kb-3"]


class TestTokenize:
    def test_lowercases_and_splits_on_punctuation(self):
        assert tokenize("Refund! My-Charge?") == ["refund", "charge"]

    def test_drops_stopwords(self):
        assert tokenize("the refund is for my account") == ["refund", "account"]

    def test_keeps_numbers(self):
        assert "429" in tokenize("getting 429 errors")

    def test_to_and_up_are_separate_stopwords(self):
        """Regression: these two were once merged into a bogus 'toup' token."""
        assert tokenize("to up") == []
        assert tokenize("toup") == ["toup"]


class TestPromptBlock:
    def test_block_contains_id_problem_and_resolution(self, kb):
        block = kb.by_id("kb-001").as_prompt_block()
        assert "[kb-001]" in block
        assert "Problem:" in block
        assert "Resolution:" in block


class TestStemming:
    """Regression cover for the under-retrieval bug: a query in one
    morphological form must reach an article written in another."""

    @pytest.mark.parametrize(
        "query",
        [
            "charged twice",
            "double charge on my card",
            "charges appearing twice",
            "I am being charged for two subscriptions",
        ],
    )
    def test_charge_variants_all_reach_the_duplicate_charge_article(self, kb, query):
        hits = kb.search(query)
        assert hits, f"no retrieval for {query!r}"
        assert "kb-001" in {h.article.id for h in hits}

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("charged", "charges"),
            ("shipping", "shipped"),
            ("deliveries", "delivery"),
            ("failing", "failed"),
        ],
    )
    def test_morphological_variants_share_a_stem(self, a, b):
        assert tokenize(a) == tokenize(b)

    def test_short_words_are_left_alone(self):
        assert tokenize("api") == ["api"]

    def test_numbers_are_not_stemmed(self):
        assert tokenize("4295") == ["4295"]

    @pytest.mark.parametrize("word", ["yours", "cans", "wills"])
    def test_stemming_does_not_resurrect_a_stopword(self, word):
        """These are not stopwords, but they stem into ones. The second filter
        pass exists so a term dropped from the documents cannot survive in the
        query and skew every score."""
        assert tokenize(word) == []

    def test_stemmer_does_not_collapse_doubled_consonants(self):
        """Documents the limit honestly: "getting" becomes "gett", not "get".
        Harmless because it is applied to queries and documents alike."""
        assert tokenize("getting") == ["gett"]
