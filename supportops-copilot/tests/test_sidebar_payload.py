"""Reading a triage decision back off the ticket that carries it."""

from __future__ import annotations

import pytest

from app.llm.schemas import Category, Classification, Draft, Sentiment, Urgency
from app.sidebar.payload import SidebarState, build_sidebar_payload
from app.triage.decision import (
    NOTE_DATA_MARKER,
    FallbackReason,
    apply_decision,
    decode_note_data,
    flag_only,
)
from app.zendesk.models import ZendeskComment, ZendeskTicket

DRAFT_BODY = (
    "Thanks for flagging this. I can see two charges against the same billing "
    "period, which points to a duplicate authorisation."
)


def a_classification(confidence: float = 0.92) -> Classification:
    return Classification(
        category=Category.BILLING,
        urgency=Urgency.HIGH,
        sentiment=Sentiment.FRUSTRATED,
        confidence=confidence,
        reasoning="Two identical charges for one billing period.",
    )


def a_draft(confidence: float = 0.85) -> Draft:
    return Draft(body=DRAFT_BODY, confidence=confidence, grounded_in=["kb-001"])


def comment(body: str, public: bool = False, cid: int = 1) -> ZendeskComment:
    return ZendeskComment.from_api({"id": cid, "body": body, "public": public})


def a_ticket(status: str = "open") -> ZendeskTicket:
    return ZendeskTicket.from_api(
        {"id": 4242, "subject": "Charged twice", "status": status}
    )


class TestNoteRoundTrip:
    def test_an_apply_decision_round_trips(self):
        note = apply_decision(a_classification(), a_draft()).internal_note
        data = decode_note_data(note)

        assert data["action"] == "apply"
        assert data["category"] == "billing"
        assert data["draft"] == DRAFT_BODY
        assert data["grounded_in"] == ["kb-001"]

    def test_a_fallback_decision_round_trips(self):
        note = flag_only(FallbackReason.MODEL_REFUSED, "category: cyber").internal_note
        data = decode_note_data(note)

        assert data["action"] == "flag_only"
        assert data["reason"] == "model_refused"
        assert data["detail"] == "category: cyber"

    def test_the_footer_is_the_last_line(self):
        note = apply_decision(a_classification(), a_draft()).internal_note
        assert note.splitlines()[-1].startswith(NOTE_DATA_MARKER)

    def test_the_human_readable_note_still_reads_normally(self):
        """The footer must not swallow the prose an agent actually reads."""
        note = apply_decision(a_classification(), a_draft()).internal_note

        assert "Suggested reply" in note
        assert "nothing has been sent to the customer" in note.lower()

    @pytest.mark.parametrize(
        "note",
        [
            "just an ordinary internal note",
            "",
            f"{NOTE_DATA_MARKER}",
            f"{NOTE_DATA_MARKER} not json at all",
            f"{NOTE_DATA_MARKER} [1, 2, 3]",
            f"{NOTE_DATA_MARKER} null",
        ],
    )
    def test_unreadable_footers_decode_to_none_rather_than_raising(self, note):
        assert decode_note_data(note) is None

    def test_the_most_recent_footer_wins_within_one_note(self):
        note = f"{NOTE_DATA_MARKER} {{\"v\":1,\"action\":\"old\"}}\nlater\n{NOTE_DATA_MARKER} {{\"v\":1,\"action\":\"new\"}}"
        assert decode_note_data(note)["action"] == "new"


class TestSidebarStates:
    def test_a_triaged_ticket_shows_the_full_decision(self):
        note = apply_decision(a_classification(), a_draft()).internal_note
        payload = build_sidebar_payload(a_ticket(), [comment(note)])

        assert payload.state is SidebarState.TRIAGED
        assert payload.category == "billing"
        assert payload.urgency == "high"
        assert payload.sentiment == "frustrated"
        assert payload.classification_confidence == 0.92
        assert payload.draft_confidence == 0.85
        assert payload.draft == DRAFT_BODY
        assert payload.grounded_in == ["kb-001"]

    def test_a_flagged_ticket_shows_the_reason_and_no_draft(self):
        note = flag_only(FallbackReason.LOW_CONFIDENCE_DRAFT, "0.31 < 0.70").internal_note
        payload = build_sidebar_payload(a_ticket(), [comment(note)])

        assert payload.state is SidebarState.NEEDS_MANUAL_TRIAGE
        assert payload.fallback_reason == "low_confidence_draft"
        assert payload.draft is None
        assert payload.category is None

    def test_an_untriaged_ticket_says_so(self):
        payload = build_sidebar_payload(a_ticket(), [comment("customer wrote in")])
        assert payload.state is SidebarState.NOT_TRIAGED

    def test_a_ticket_with_no_comments_at_all(self):
        assert build_sidebar_payload(a_ticket(), []).state is SidebarState.NOT_TRIAGED

    def test_an_unknown_schema_version_degrades_rather_than_half_rendering(self):
        """Showing half-understood fields is worse than admitting we cannot read it."""
        note = f"AI triage\n\n{NOTE_DATA_MARKER} {{\"v\":99,\"action\":\"apply\",\"category\":\"billing\"}}"
        payload = build_sidebar_payload(a_ticket(), [comment(note)])

        assert payload.state is SidebarState.UNREADABLE
        assert payload.category is None
        assert "v99" in payload.fallback_detail

    def test_a_corrupt_footer_reads_as_not_triaged(self):
        note = f"AI triage\n\n{NOTE_DATA_MARKER} {{broken"
        payload = build_sidebar_payload(a_ticket(), [comment(note)])
        assert payload.state is SidebarState.NOT_TRIAGED


class TestNoteSelection:
    def test_the_most_recent_note_wins(self):
        first = flag_only(FallbackReason.LLM_UNAVAILABLE).internal_note
        second = apply_decision(a_classification(), a_draft()).internal_note
        payload = build_sidebar_payload(
            a_ticket(), [comment(first, cid=1), comment(second, cid=2)]
        )

        assert payload.state is SidebarState.TRIAGED

    def test_a_footer_in_a_public_comment_is_ignored(self):
        """A footer in a public comment means an agent pasted our internal note
        to the customer. Not a decision, and not worth compounding."""
        note = apply_decision(a_classification(), a_draft()).internal_note
        payload = build_sidebar_payload(a_ticket(), [comment(note, public=True)])

        assert payload.state is SidebarState.NOT_TRIAGED

    def test_ordinary_notes_between_triage_notes_do_not_confuse_it(self):
        note = apply_decision(a_classification(), a_draft()).internal_note
        comments = [
            comment(note, cid=1),
            comment("agent added some context", cid=2),
            comment("another agent replied", public=True, cid=3),
        ]
        assert build_sidebar_payload(a_ticket(), comments).state is SidebarState.TRIAGED


class TestHistorySummary:
    def test_counts_public_replies(self):
        note = apply_decision(a_classification(), a_draft()).internal_note
        comments = [comment(note), comment("hi", public=True, cid=2)]
        payload = build_sidebar_payload(a_ticket(), comments)

        assert "1 public reply" in payload.history_summary

    def test_pluralises_correctly(self):
        comments = [comment("a", public=True, cid=1), comment("b", public=True, cid=2)]
        payload = build_sidebar_payload(a_ticket(), comments)
        assert "2 public replies" in payload.history_summary

    def test_says_so_when_there_are_none(self):
        payload = build_sidebar_payload(a_ticket(), [])
        assert "no public replies" in payload.history_summary

    def test_includes_the_ticket_status(self):
        payload = build_sidebar_payload(a_ticket(status="pending"), [])
        assert "pending" in payload.history_summary


class TestSerialisation:
    def test_to_dict_is_json_serialisable(self):
        import json

        note = apply_decision(a_classification(), a_draft()).internal_note
        payload = build_sidebar_payload(a_ticket(), [comment(note)])
        assert json.loads(json.dumps(payload.to_dict()))["state"] == "triaged"

    def test_the_state_serialises_as_a_plain_string(self):
        payload = build_sidebar_payload(a_ticket(), [])
        assert payload.to_dict()["state"] == "not_triaged"
