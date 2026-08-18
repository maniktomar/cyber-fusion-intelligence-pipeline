"""The confidence gate. These are the tests the whole project exists to pass."""

from __future__ import annotations

import pytest

from app.llm.schemas import Category, Classification, Draft, Sentiment, Urgency
from app.triage.decision import (
    AI_TRIAGED_TAG,
    MANUAL_TRIAGE_TAG,
    Action,
    FallbackReason,
    Thresholds,
    decide,
    flag_only,
    validate_draft,
)

THRESHOLDS = Thresholds(classification=0.75, draft=0.70)


def classification(confidence: float = 0.95) -> Classification:
    return Classification(
        category=Category.BILLING,
        urgency=Urgency.NORMAL,
        sentiment=Sentiment.NEUTRAL,
        confidence=confidence,
        reasoning="Customer describes a duplicate charge on one billing period.",
    )


def draft(
    confidence: float = 0.9,
    body: str | None = None,
    grounded_in: list[str] | None = None,
) -> Draft:
    return Draft(
        body=body
        if body is not None
        else (
            "Thanks for flagging this. I can see two charges against the same "
            "billing period, which means the second one was a duplicate "
            "authorisation. I've asked our billing team to reverse it."
        ),
        confidence=confidence,
        grounded_in=["kb-001"] if grounded_in is None else grounded_in,
    )


class TestHighConfidenceSuccess:
    def test_applies_when_everything_clears_the_thresholds(self):
        decision = decide(classification(), draft(), THRESHOLDS)

        assert decision.action is Action.APPLY
        assert decision.is_fallback is False
        assert decision.reason is None

    def test_tags_encode_the_classification(self):
        decision = decide(classification(), draft(), THRESHOLDS)

        assert AI_TRIAGED_TAG in decision.tags
        assert "ai-category-billing" in decision.tags
        assert "ai-urgency-normal" in decision.tags
        assert "ai-sentiment-neutral" in decision.tags
        assert MANUAL_TRIAGE_TAG not in decision.tags

    def test_note_carries_the_draft_and_both_confidences(self):
        decision = decide(classification(0.91), draft(0.88), THRESHOLDS)

        assert "0.91" in decision.internal_note
        assert "0.88" in decision.internal_note
        assert "duplicate authorisation" in decision.internal_note
        assert "kb-001" in decision.internal_note

    def test_note_says_nothing_was_sent_to_the_customer(self):
        decision = decide(classification(), draft(), THRESHOLDS)
        assert "nothing has been sent to the customer" in decision.internal_note.lower()

    def test_confidence_exactly_at_the_threshold_passes(self):
        decision = decide(classification(0.75), draft(0.70), THRESHOLDS)
        assert decision.action is Action.APPLY


class TestLowConfidenceFallback:
    def test_low_classification_confidence_flags_only(self):
        decision = decide(classification(0.60), draft(), THRESHOLDS)

        assert decision.action is Action.FLAG_ONLY
        assert decision.reason is FallbackReason.LOW_CONFIDENCE_CLASSIFICATION
        assert decision.tags == [MANUAL_TRIAGE_TAG]

    def test_low_draft_confidence_flags_only(self):
        decision = decide(classification(0.95), draft(0.40), THRESHOLDS)

        assert decision.action is Action.FLAG_ONLY
        assert decision.reason is FallbackReason.LOW_CONFIDENCE_DRAFT

    def test_a_hair_below_the_threshold_still_falls_back(self):
        decision = decide(classification(0.749), draft(), THRESHOLDS)
        assert decision.action is Action.FLAG_ONLY

    def test_fallback_carries_no_classification_or_draft(self):
        """The whole point: an uncertain read must not leak onto the ticket."""
        decision = decide(classification(0.10), draft(), THRESHOLDS)

        assert decision.classification is None
        assert decision.draft is None

    def test_fallback_note_never_contains_the_draft_body(self):
        decision = decide(classification(0.10), draft(), THRESHOLDS)
        assert "duplicate authorisation" not in decision.internal_note

    def test_fallback_states_the_actual_numbers(self):
        decision = decide(classification(0.42), draft(), THRESHOLDS)

        assert "0.42" in decision.detail
        assert "0.75" in decision.detail

    def test_classification_is_checked_before_the_draft(self):
        """Both are bad; the reported reason should be the first failure."""
        decision = decide(classification(0.10), draft(0.10), THRESHOLDS)
        assert decision.reason is FallbackReason.LOW_CONFIDENCE_CLASSIFICATION


class TestGrounding:
    def test_ungrounded_draft_flags_only(self):
        decision = decide(classification(), draft(grounded_in=[]), THRESHOLDS)

        assert decision.action is Action.FLAG_ONLY
        assert decision.reason is FallbackReason.UNGROUNDED_DRAFT

    def test_grounding_can_be_turned_off(self):
        lenient = Thresholds(classification=0.75, draft=0.70, require_grounding=False)
        decision = decide(classification(), draft(grounded_in=[]), lenient)
        assert decision.action is Action.APPLY


class TestDraftValidation:
    @pytest.mark.parametrize(
        "body",
        [
            "Hi [CUSTOMER NAME], thanks for reaching out about your recent order.",
            "Your order {{order_id}} has shipped and should arrive shortly.",
            "TODO: check the billing dashboard before replying to this customer.",
            "Please see XXX for the refund policy that applies to your purchase.",
            "Thanks for your patience. Insert apology here and then explain policy.",
        ],
    )
    def test_unresolved_placeholders_are_rejected(self, body):
        rejection = validate_draft(draft(body=body))

        assert rejection is not None
        assert rejection.reason is FallbackReason.DRAFT_REJECTED

    @pytest.mark.parametrize(
        "body",
        [
            "I have issued a refund to your original payment method this morning.",
            "We guarantee this will not happen again on your account going forward.",
            "A full refund has been issued and will appear within three days.",
            "This will definitely be fixed by Friday, so no action is needed.",
        ],
    )
    def test_unauthorised_commitments_are_rejected(self, body):
        rejection = validate_draft(draft(body=body))

        assert rejection is not None
        assert rejection.reason is FallbackReason.DRAFT_REJECTED

    def test_a_too_short_draft_is_rejected(self):
        rejection = validate_draft(draft(body="Sorry about that!"))
        assert rejection is not None
        assert "too short" in rejection.detail

    def test_an_absurdly_long_draft_is_rejected(self):
        rejection = validate_draft(draft(body="word " * 1500))
        assert rejection is not None
        assert "over the" in rejection.detail

    def test_a_clean_draft_passes(self):
        assert validate_draft(draft()) is None

    def test_a_rejected_draft_makes_the_whole_decision_fall_back(self):
        bad = draft(body="Hi [CUSTOMER NAME], I have issued a refund for you today.")
        decision = decide(classification(), bad, THRESHOLDS)

        assert decision.action is Action.FLAG_ONLY
        assert decision.reason is FallbackReason.DRAFT_REJECTED

    def test_validation_runs_even_at_maximum_confidence(self):
        """Confidence is the model's opinion; the safety check is not negotiable."""
        bad = draft(confidence=1.0, body="We guarantee a full resolution by tomorrow.")
        decision = decide(classification(1.0), bad, THRESHOLDS)
        assert decision.action is Action.FLAG_ONLY


class TestFlagOnlyShape:
    @pytest.mark.parametrize("reason", list(FallbackReason))
    def test_every_reason_produces_a_usable_note(self, reason):
        """No fallback path may produce a blank or unexplained note."""
        decision = flag_only(reason)

        assert decision.action is Action.FLAG_ONLY
        assert decision.tags == [MANUAL_TRIAGE_TAG]
        assert "needs manual triage" in decision.internal_note
        assert "Reason:" in decision.internal_note
        assert len(decision.internal_note) > 60

    def test_detail_is_included_when_provided(self):
        decision = flag_only(FallbackReason.LLM_UNAVAILABLE, "connection reset by peer")
        assert "connection reset by peer" in decision.internal_note

    def test_note_is_explicit_that_the_ticket_was_not_modified(self):
        decision = flag_only(FallbackReason.MODEL_REFUSED)
        assert "untouched" in decision.internal_note


class TestNoDraftContentLeaksIntoAFallbackNote:
    """A rejected draft was judged unsafe. Quoting any of it back onto the
    ticket puts that text in front of an agent who may skim it as a suggestion."""

    @pytest.mark.parametrize(
        ("body", "forbidden"),
        [
            ("Hi [CUSTOMER NAME], about your recent order with us.", "[CUSTOMER NAME]"),
            ("Your order {{order_id}} has now shipped to your address.", "order_id"),
            ("I have issued a refund to your card for the duplicate charge.", "refund"),
            ("We guarantee this will not happen again on your account.", "guarantee"),
        ],
    )
    def test_the_offending_fragment_is_not_echoed(self, body, forbidden):
        decision = decide(classification(), draft(body=body), THRESHOLDS)

        assert decision.action is Action.FLAG_ONLY
        assert forbidden.lower() not in decision.internal_note.lower()

    def test_the_note_still_says_why(self):
        decision = decide(
            classification(), draft(body="Hi [CUSTOMER NAME], thanks for writing in."), THRESHOLDS
        )
        assert "placeholder" in decision.internal_note
