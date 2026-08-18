"""Draft-usage inference: the only metric that says whether this tool helps."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.notifications.resolution import (
    DRAFT_MARKER,
    DraftUsage,
    build_summary,
    classify_draft_usage,
    extract_draft,
    similarity,
)
from app.zendesk.models import ZendeskComment, ZendeskTicket, parse_zendesk_timestamp

DRAFT_TEXT = (
    "Thanks for flagging this. I can see two charges against the same billing "
    "period, which points to a duplicate authorisation. I have asked our billing "
    "team to reverse the second one; refunds usually settle within 5-10 business "
    "days depending on your bank."
)


def note(body: str, public: bool = False) -> ZendeskComment:
    return ZendeskComment.from_api(
        {"id": 1, "body": body, "public": public, "created_at": "2026-08-18T12:00:00Z"}
    )


def ai_note(draft: str = DRAFT_TEXT) -> ZendeskComment:
    return note(f"AI triage (review before sending).\n{DRAFT_MARKER}\n{draft}")


def a_ticket(**overrides) -> ZendeskTicket:
    payload = {
        "id": 4242,
        "subject": "Charged twice this month",
        "tags": ["ai-triaged", "ai-category-billing"],
        "created_at": "2026-08-18T09:00:00Z",
        "updated_at": "2026-08-18T12:30:00Z",
    }
    payload.update(overrides)
    return ZendeskTicket.from_api(payload)


class TestSimilarity:
    def test_identical_text_scores_one(self):
        assert similarity(DRAFT_TEXT, DRAFT_TEXT) == 1.0

    def test_unrelated_text_scores_low(self):
        assert similarity(DRAFT_TEXT, "Your parcel is out for delivery today.") < 0.2

    def test_empty_input_scores_zero(self):
        assert similarity("", DRAFT_TEXT) == 0.0
        assert similarity(DRAFT_TEXT, "") == 0.0

    def test_is_case_insensitive(self):
        assert similarity("Refund Issued", "refund issued") == 1.0


class TestExtractDraft:
    def test_pulls_the_draft_out_of_an_ai_note(self):
        assert extract_draft([ai_note()]) == DRAFT_TEXT

    def test_ignores_notes_without_the_marker(self):
        assert extract_draft([note("Just an ordinary internal note.")]) is None

    def test_ignores_public_comments_even_with_the_marker(self):
        """The marker in a public reply would mean an agent pasted our own
        note to the customer; it is not a staged draft."""
        assert extract_draft([note(f"{DRAFT_MARKER}\nhello", public=True)]) is None

    def test_takes_the_most_recent_ai_note(self):
        comments = [ai_note("first draft"), ai_note("second draft")]
        assert extract_draft(comments) == "second draft"

    def test_an_empty_draft_section_is_treated_as_absent(self):
        assert extract_draft([note(f"AI triage.\n{DRAFT_MARKER}\n   ")]) is None

    def test_returns_none_for_no_comments(self):
        assert extract_draft([]) is None


class TestClassifyDraftUsage:
    def test_a_verbatim_paste_counts_as_used(self):
        usage, score = classify_draft_usage([ai_note(), note(DRAFT_TEXT, public=True)])

        assert usage is DraftUsage.USED
        assert score == 1.0

    def test_a_lightly_edited_reply_counts_as_used(self):
        edited = DRAFT_TEXT.replace("Thanks for flagging this.", "Hi Sam, thanks for writing in.")
        usage, _ = classify_draft_usage([ai_note(), note(edited, public=True)])
        assert usage is DraftUsage.USED

    def test_a_completely_different_reply_counts_as_not_used(self):
        other = "We have escalated this to our payments team and will follow up."
        usage, _ = classify_draft_usage([ai_note(), note(other, public=True)])
        assert usage is DraftUsage.NOT_USED

    def test_a_flagged_ticket_reports_no_draft_offered(self):
        comments = [note("AI: could not process, needs manual triage.")]
        usage, score = classify_draft_usage(comments)

        assert usage is DraftUsage.NO_DRAFT_OFFERED
        assert score is None

    def test_a_solved_ticket_with_no_public_reply_is_unknown(self):
        """Duplicates, spam, and phone resolutions are real. Calling these
        NOT_USED would count them as rejections and deflate the metric."""
        usage, score = classify_draft_usage([ai_note()])

        assert usage is DraftUsage.UNKNOWN
        assert score is None

    def test_an_empty_public_reply_does_not_count_as_a_reply(self):
        usage, _ = classify_draft_usage([ai_note(), note("   ", public=True)])
        assert usage is DraftUsage.UNKNOWN

    def test_the_best_matching_reply_wins(self):
        comments = [
            ai_note(),
            note("Looking into this for you.", public=True),
            note(DRAFT_TEXT, public=True),
        ]
        usage, score = classify_draft_usage(comments)

        assert usage is DraftUsage.USED
        assert score == 1.0

    def test_the_threshold_is_configurable(self):
        other = "We have escalated this to our payments team and will follow up."
        usage, _ = classify_draft_usage(
            [ai_note(), note(other, public=True)], threshold=0.01
        )
        assert usage is DraftUsage.USED

    def test_a_shared_topic_alone_does_not_count_as_used(self):
        """The threshold is high on purpose: two replies about the same billing
        problem share vocabulary without one being derived from the other."""
        independent = (
            "I have checked your billing period and can confirm a duplicate "
            "charge. Our team will process the reversal shortly."
        )
        usage, _ = classify_draft_usage([ai_note(), note(independent, public=True)])
        assert usage is DraftUsage.NOT_USED


class TestResolutionTime:
    def test_computes_the_elapsed_time(self):
        summary = build_summary(a_ticket(), [ai_note()])
        assert summary.resolution_time == timedelta(hours=3, minutes=30)
        assert summary.resolution_time_human == "3h 30m"

    def test_an_explicit_solved_at_overrides_updated_at(self):
        solved = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
        summary = build_summary(a_ticket(), [ai_note()], solved_at=solved)
        assert summary.resolution_time == timedelta(hours=1)

    def test_a_missing_created_at_yields_unknown(self):
        summary = build_summary(a_ticket(created_at=None), [ai_note()])

        assert summary.resolution_time is None
        assert summary.resolution_time_human == "unknown"

    @pytest.mark.parametrize(
        ("delta", "expected"),
        [
            (timedelta(minutes=7), "7m"),
            (timedelta(hours=2, minutes=5), "2h 5m"),
            (timedelta(days=2, hours=3), "2d 3h"),
            (timedelta(0), "0m"),
        ],
    )
    def test_human_formatting(self, delta, expected):
        summary = build_summary(
            a_ticket(created_at="2026-08-18T00:00:00Z"),
            [ai_note()],
            solved_at=datetime(2026, 8, 18, tzinfo=UTC) + delta,
        )
        assert summary.resolution_time_human == expected

    def test_a_negative_span_is_reported_as_unknown_not_as_a_negative(self):
        """Clock skew between Zendesk and us should not print '-3h'."""
        summary = build_summary(
            a_ticket(created_at="2026-08-18T12:00:00Z"),
            [ai_note()],
            solved_at=datetime(2026, 8, 18, 9, tzinfo=UTC),
        )
        assert summary.resolution_time_human == "unknown"


class TestTimestampParsing:
    def test_a_zulu_timestamp_becomes_aware_utc(self):
        parsed = parse_zendesk_timestamp("2026-08-18T12:00:00Z")
        assert parsed == datetime(2026, 8, 18, 12, tzinfo=UTC)

    def test_a_naive_timestamp_is_assumed_utc_not_local(self):
        """A naive datetime here makes every resolution time wrong by the
        host's offset, and wrong in a way that still looks plausible."""
        assert parse_zendesk_timestamp("2026-08-18T12:00:00") == datetime(
            2026, 8, 18, 12, tzinfo=UTC
        )

    def test_an_offset_timestamp_is_normalised_to_utc(self):
        assert parse_zendesk_timestamp("2026-08-18T14:00:00+02:00") == datetime(
            2026, 8, 18, 12, tzinfo=UTC
        )

    @pytest.mark.parametrize("raw", [None, "", "not a date", "18/08/2026"])
    def test_unparseable_values_yield_none_rather_than_raising(self, raw):
        assert parse_zendesk_timestamp(raw) is None


class TestSummaryFields:
    def test_category_is_read_back_off_the_tags(self):
        assert build_summary(a_ticket(), [ai_note()]).category == "billing"

    def test_an_unclassified_ticket_has_no_category(self):
        summary = build_summary(a_ticket(tags=["ai-needs-manual-triage"]), [ai_note()])
        assert summary.category is None

    def test_carries_the_ticket_id_and_subject(self):
        summary = build_summary(a_ticket(), [ai_note()])
        assert summary.ticket_id == 4242
        assert summary.subject == "Charged twice this month"
