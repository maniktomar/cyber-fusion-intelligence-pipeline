"""End-to-end triage with the LLM stubbed.

The contract under test: `triage()` always returns a decision. There is no
input and no failure that makes it raise, return None, or leave a ticket in a
state where nothing happened and nothing was recorded.
"""

from __future__ import annotations

import pytest

from app.circuit import CircuitBreaker
from app.llm.errors import (
    CircuitOpenError,
    LLMMalformedResponseError,
    LLMRefusedError,
    LLMUnavailableError,
)
from app.llm.schemas import Category, Classification, Draft, Sentiment, Urgency
from app.triage.decision import MANUAL_TRIAGE_TAG, Action, FallbackReason, Thresholds
from app.triage.knowledge_base import KnowledgeBase
from app.triage.service import Ticket, TriageService

DUPLICATE_CHARGE = Ticket(
    id=4242,
    subject="Charged twice this month",
    body="My card shows two identical charges for the same billing period. Please help.",
)


def a_classification(confidence: float = 0.93) -> Classification:
    return Classification(
        category=Category.BILLING,
        urgency=Urgency.NORMAL,
        sentiment=Sentiment.FRUSTRATED,
        confidence=confidence,
        reasoning="Two identical charges for one billing period.",
    )


def a_draft(confidence: float = 0.88, grounded_in=("kb-001",)) -> Draft:
    return Draft(
        body=(
            "Thanks for flagging this. I can see two charges against the same "
            "billing period, which points to a duplicate authorisation. I've "
            "asked our billing team to reverse the second one; refunds usually "
            "settle within 5-10 business days depending on your bank."
        ),
        confidence=confidence,
        grounded_in=list(grounded_in),
    )


class StubLLM:
    """Returns a queued value per call, or raises it if it is an exception."""

    def __init__(self, *outcomes):
        self._outcomes = list(outcomes)
        self.call_count = 0
        self.prompts: list[str] = []

    def complete(self, *, schema, system, user, max_tokens=8192, effort="low"):
        self.call_count += 1
        self.prompts.append(user)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase.from_file("data/knowledge_base.json")


def service(llm, kb, **kwargs) -> TriageService:
    return TriageService(llm, kb, **kwargs)


class TestHighConfidenceSuccess:
    def test_a_clear_ticket_is_triaged_and_drafted(self, kb):
        llm = StubLLM(a_classification(), a_draft())
        decision = service(llm, kb).triage(DUPLICATE_CHARGE)

        assert decision.action is Action.APPLY
        assert decision.classification.category is Category.BILLING
        assert llm.call_count == 2

    def test_retrieved_articles_reach_the_draft_prompt(self, kb):
        llm = StubLLM(a_classification(), a_draft())
        service(llm, kb).triage(DUPLICATE_CHARGE)

        draft_prompt = llm.prompts[1]
        assert "kb-001" in draft_prompt
        assert "Knowledge base articles" in draft_prompt

    def test_the_classification_is_passed_into_the_draft_prompt(self, kb):
        llm = StubLLM(a_classification(), a_draft())
        service(llm, kb).triage(DUPLICATE_CHARGE)
        assert "billing" in llm.prompts[1]


class TestEmptyInput:
    @pytest.mark.parametrize(
        "ticket",
        [
            Ticket(id=1, subject="", body=""),
            Ticket(id=2, subject="   ", body="\n\t  "),
        ],
    )
    def test_an_empty_ticket_is_flagged_without_calling_the_model(self, kb, ticket):
        llm = StubLLM()
        decision = service(llm, kb).triage(ticket)

        assert decision.reason is FallbackReason.EMPTY_TICKET
        assert llm.call_count == 0

    def test_a_subject_alone_is_enough_to_proceed(self, kb):
        llm = StubLLM(a_classification(), a_draft())
        ticket = Ticket(id=3, subject="Charged twice", body="")
        assert service(llm, kb).triage(ticket).action is Action.APPLY


class TestLLMFailureFallbacks:
    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (LLMUnavailableError("503"), FallbackReason.LLM_UNAVAILABLE),
            (LLMMalformedResponseError("bad json"), FallbackReason.MALFORMED_RESPONSE),
            (LLMRefusedError("cyber", "declined"), FallbackReason.MODEL_REFUSED),
            (CircuitOpenError(30.0), FallbackReason.CIRCUIT_OPEN),
        ],
    )
    def test_classification_failures_map_to_the_right_reason(self, kb, error, expected):
        decision = service(StubLLM(error), kb).triage(DUPLICATE_CHARGE)

        assert decision.action is Action.FLAG_ONLY
        assert decision.reason is expected
        assert decision.tags == [MANUAL_TRIAGE_TAG]

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (LLMUnavailableError("503"), FallbackReason.LLM_UNAVAILABLE),
            (LLMMalformedResponseError("bad json"), FallbackReason.MALFORMED_RESPONSE),
            (LLMRefusedError(None, None), FallbackReason.MODEL_REFUSED),
        ],
    )
    def test_draft_failures_also_flag_only(self, kb, error, expected):
        """A good classification does not license applying a missing draft."""
        llm = StubLLM(a_classification(), error)
        decision = service(llm, kb).triage(DUPLICATE_CHARGE)

        assert decision.action is Action.FLAG_ONLY
        assert decision.reason is expected
        assert decision.classification is None

    def test_a_classification_failure_skips_the_draft_call(self, kb):
        llm = StubLLM(LLMUnavailableError("down"))
        service(llm, kb).triage(DUPLICATE_CHARGE)
        assert llm.call_count == 1

    def test_an_unexpected_exception_still_flags_rather_than_escaping(self, kb):
        """The catch-all: a bug in our own code must not drop the ticket."""
        llm = StubLLM(ZeroDivisionError("a bug nobody predicted"))
        decision = service(llm, kb).triage(DUPLICATE_CHARGE)

        assert decision.action is Action.FLAG_ONLY
        assert "ZeroDivisionError" in decision.detail

    def test_no_expected_failure_escapes_as_an_exception(self, kb):
        for error in (
            LLMUnavailableError("x"),
            LLMMalformedResponseError("x"),
            LLMRefusedError("bio", None),
            CircuitOpenError(1.0),
            RuntimeError("x"),
            KeyError("x"),
        ):
            decision = service(StubLLM(error), kb).triage(DUPLICATE_CHARGE)
            assert decision.action is Action.FLAG_ONLY


class TestConfidenceGateEndToEnd:
    def test_low_classification_confidence_still_calls_the_draft_then_falls_back(
        self, kb
    ):
        """The gate runs after both calls, so a weak classification is not a
        reason to skip drafting -- but it is a reason not to apply either."""
        llm = StubLLM(a_classification(0.30), a_draft())
        decision = service(llm, kb).triage(DUPLICATE_CHARGE)

        assert decision.action is Action.FLAG_ONLY
        assert decision.reason is FallbackReason.LOW_CONFIDENCE_CLASSIFICATION

    def test_low_draft_confidence_falls_back(self, kb):
        llm = StubLLM(a_classification(), a_draft(confidence=0.20))
        decision = service(llm, kb).triage(DUPLICATE_CHARGE)
        assert decision.reason is FallbackReason.LOW_CONFIDENCE_DRAFT

    def test_thresholds_are_configurable(self, kb):
        lenient = Thresholds(classification=0.2, draft=0.2)
        llm = StubLLM(a_classification(0.30), a_draft(0.25))
        decision = service(llm, kb, thresholds=lenient).triage(DUPLICATE_CHARGE)
        assert decision.action is Action.APPLY


class TestCitationVerification:
    def test_a_citation_the_retriever_never_returned_is_dropped(self, kb):
        """Guards against the model inventing a plausible-looking article ID."""
        llm = StubLLM(a_classification(), a_draft(grounded_in=["kb-999"]))
        decision = service(llm, kb).triage(DUPLICATE_CHARGE)

        assert decision.action is Action.FLAG_ONLY
        assert decision.reason is FallbackReason.UNGROUNDED_DRAFT

    def test_real_citations_survive_verification(self, kb):
        llm = StubLLM(a_classification(), a_draft(grounded_in=["kb-001"]))
        decision = service(llm, kb).triage(DUPLICATE_CHARGE)

        assert decision.action is Action.APPLY
        assert decision.draft.grounded_in == ["kb-001"]

    def test_a_mix_keeps_only_the_verifiable_ones(self, kb):
        llm = StubLLM(a_classification(), a_draft(grounded_in=["kb-001", "kb-404"]))
        decision = service(llm, kb).triage(DUPLICATE_CHARGE)

        assert decision.action is Action.APPLY
        assert decision.draft.grounded_in == ["kb-001"]

    def test_a_ticket_the_kb_cannot_answer_falls_back(self, kb):
        """No retrieval means no grounding means no draft applied."""
        off_topic = Ticket(
            id=99,
            subject="Question about photosynthesis",
            body="How do chloroplasts convert sunlight into chemical energy?",
        )
        llm = StubLLM(a_classification(), a_draft(grounded_in=["kb-001"]))
        decision = service(llm, kb).triage(off_topic)

        assert decision.action is Action.FLAG_ONLY
        assert decision.reason is FallbackReason.UNGROUNDED_DRAFT


class TestUnsafeDraftEndToEnd:
    def test_a_draft_with_a_placeholder_never_reaches_the_agent(self, kb):
        unsafe = Draft(
            body="Hi [CUSTOMER NAME], I've looked into the duplicate charge for you.",
            confidence=0.99,
            grounded_in=["kb-001"],
        )
        llm = StubLLM(a_classification(), unsafe)
        decision = service(llm, kb).triage(DUPLICATE_CHARGE)

        assert decision.action is Action.FLAG_ONLY
        assert "[CUSTOMER NAME]" not in decision.internal_note

    def test_a_draft_promising_a_refund_never_reaches_the_agent(self, kb):
        unsafe = Draft(
            body=(
                "Thanks for getting in touch. I have issued a refund for the "
                "duplicate charge and it will land in your account shortly."
            ),
            confidence=0.99,
            grounded_in=["kb-001"],
        )
        llm = StubLLM(a_classification(), unsafe)
        decision = service(llm, kb).triage(DUPLICATE_CHARGE)

        assert decision.action is Action.FLAG_ONLY
        assert decision.reason is FallbackReason.DRAFT_REJECTED


class TestBreakerIntegration:
    def test_an_open_breaker_short_circuits_the_whole_ticket(self, kb):
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure()
        llm = StubLLM(CircuitOpenError(breaker.retry_after()))
        decision = service(llm, kb).triage(DUPLICATE_CHARGE)

        assert decision.reason is FallbackReason.CIRCUIT_OPEN
        assert llm.call_count == 1
