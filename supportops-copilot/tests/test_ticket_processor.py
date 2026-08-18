"""The processor runs in a background task, so it must never raise."""

from __future__ import annotations

import pytest

from app.llm.schemas import Category, Classification, Draft, Sentiment, Urgency
from app.triage.decision import (
    AI_TRIAGED_TAG,
    MANUAL_TRIAGE_TAG,
    Action,
    FallbackReason,
    apply_decision,
    flag_only,
)
from app.webhooks.events import EventType, WebhookEvent
from app.webhooks.processor import TicketProcessor
from app.zendesk.errors import (
    ZendeskAuthError,
    ZendeskNotFoundError,
    ZendeskUnavailableError,
)
from app.zendesk.models import ZendeskTicket

EVENT = WebhookEvent(
    type=EventType.TICKET_CREATED, ticket_id=4242, raw_type="ticket.created"
)


def a_ticket(tags=()) -> ZendeskTicket:
    return ZendeskTicket.from_api(
        {
            "id": 4242,
            "subject": "Charged twice",
            "description": "Two identical charges this month.",
            "tags": list(tags),
        }
    )


def an_apply_decision():
    return apply_decision(
        Classification(
            category=Category.BILLING,
            urgency=Urgency.NORMAL,
            sentiment=Sentiment.NEUTRAL,
            confidence=0.9,
            reasoning="Duplicate charge.",
        ),
        Draft(
            body="Thanks for flagging this duplicate charge; we are reversing it.",
            confidence=0.9,
            grounded_in=["kb-001"],
        ),
    )


class FakeZendesk:
    def __init__(self, ticket=None, get_error=None, apply_error=None):
        self._ticket = ticket if ticket is not None else a_ticket()
        self._get_error = get_error
        self._apply_error = apply_error
        self.applied = []

    async def get_ticket(self, ticket_id: int):
        if self._get_error:
            raise self._get_error
        return self._ticket

    async def apply(self, ticket_id: int, decision):
        if self._apply_error:
            raise self._apply_error
        self.applied.append((ticket_id, decision))


class FakeTriage:
    def __init__(self, decision=None, error=None):
        self._decision = decision if decision is not None else an_apply_decision()
        self._error = error
        self.calls = []

    def triage(self, ticket):
        self.calls.append(ticket)
        if self._error:
            raise self._error
        return self._decision


class TestHappyPath:
    async def test_fetches_triages_and_writes_back(self):
        zendesk, triage = FakeZendesk(), FakeTriage()
        await TicketProcessor(zendesk, triage).process(EVENT)

        assert len(triage.calls) == 1
        assert len(zendesk.applied) == 1
        assert zendesk.applied[0][1].action is Action.APPLY

    async def test_passes_subject_and_description_into_triage(self):
        triage = FakeTriage()
        await TicketProcessor(FakeZendesk(), triage).process(EVENT)

        assert triage.calls[0].subject == "Charged twice"
        assert "identical charges" in triage.calls[0].body

    async def test_a_fallback_decision_is_written_back_too(self):
        """The flag is the whole point; it must reach the ticket."""
        zendesk = FakeZendesk()
        triage = FakeTriage(flag_only(FallbackReason.LLM_UNAVAILABLE, "503"))
        await TicketProcessor(zendesk, triage).process(EVENT)

        _, decision = zendesk.applied[0]
        assert decision.tags == [MANUAL_TRIAGE_TAG]


class TestLoopGuard:
    @pytest.mark.parametrize("tags", [[AI_TRIAGED_TAG], [MANUAL_TRIAGE_TAG]])
    async def test_an_already_triaged_ticket_is_skipped(self, tags):
        """Our own write fires ticket.updated; without this it never terminates."""
        zendesk, triage = FakeZendesk(ticket=a_ticket(tags)), FakeTriage()
        await TicketProcessor(zendesk, triage).process(EVENT)

        assert triage.calls == []
        assert zendesk.applied == []

    async def test_an_untouched_ticket_is_processed(self):
        zendesk = FakeZendesk(ticket=a_ticket(["billing", "vip"]))
        triage = FakeTriage()
        await TicketProcessor(zendesk, triage).process(EVENT)
        assert len(triage.calls) == 1


class TestFailuresNeverEscape:
    async def test_a_deleted_ticket_is_skipped_quietly(self):
        zendesk = FakeZendesk(get_error=ZendeskNotFoundError("gone"))
        triage = FakeTriage()
        await TicketProcessor(zendesk, triage).process(EVENT)

        assert triage.calls == []

    async def test_a_fetch_failure_does_not_raise(self):
        zendesk = FakeZendesk(get_error=ZendeskUnavailableError("503"))
        await TicketProcessor(zendesk, FakeTriage()).process(EVENT)

    async def test_a_write_failure_does_not_raise(self):
        zendesk = FakeZendesk(apply_error=ZendeskAuthError("token revoked"))
        await TicketProcessor(zendesk, FakeTriage()).process(EVENT)

    async def test_a_write_failure_is_logged_with_the_ticket_id(self, caplog):
        """The one case where a ticket really is left untouched and unflagged --
        the log is the only place left to be loud."""
        zendesk = FakeZendesk(apply_error=ZendeskAuthError("token revoked"))
        with caplog.at_level("ERROR"):
            await TicketProcessor(zendesk, FakeTriage()).process(EVENT)

        assert "4242" in caplog.text
        assert "writing it back failed" in caplog.text

    async def test_a_fetch_failure_is_logged(self, caplog):
        zendesk = FakeZendesk(get_error=ZendeskUnavailableError("503"))
        with caplog.at_level("ERROR"):
            await TicketProcessor(zendesk, FakeTriage()).process(EVENT)
        assert "4242" in caplog.text


class TestFlagUnprocessable:
    async def test_writes_a_manual_triage_flag(self):
        zendesk = FakeZendesk()
        await TicketProcessor(zendesk, FakeTriage()).flag_unprocessable(4242, "boom")

        _, decision = zendesk.applied[0]
        assert decision.tags == [MANUAL_TRIAGE_TAG]
        assert "boom" in decision.internal_note

    async def test_a_failure_to_flag_does_not_raise(self):
        zendesk = FakeZendesk(apply_error=ZendeskUnavailableError("503"))
        await TicketProcessor(zendesk, FakeTriage()).flag_unprocessable(4242, "boom")


class TestTriageBugsStillFlag:
    """TriageService swallows its own failures, so an exception escaping it is
    a bug in our code. It must still leave the ticket flagged, not silent."""

    async def test_an_exception_from_triage_still_writes_a_flag(self):
        zendesk = FakeZendesk()
        triage = FakeTriage(error=ZeroDivisionError("a bug nobody predicted"))
        await TicketProcessor(zendesk, triage).process(EVENT)

        assert len(zendesk.applied) == 1
        _, decision = zendesk.applied[0]
        assert decision.tags == [MANUAL_TRIAGE_TAG]
        assert "ZeroDivisionError" in decision.internal_note

    async def test_an_exception_from_triage_does_not_escape(self):
        triage = FakeTriage(error=RuntimeError("boom"))
        await TicketProcessor(FakeZendesk(), triage).process(EVENT)

    async def test_the_bug_is_logged_with_a_traceback(self, caplog):
        triage = FakeTriage(error=RuntimeError("boom"))
        with caplog.at_level("ERROR"):
            await TicketProcessor(FakeZendesk(), triage).process(EVENT)
        assert "triage itself raised" in caplog.text


class TestUnexpectedErrorsFromOtherLayers:
    """Regression: a `NoStoredTokenError` from the OAuth layer sailed past
    `except ZendeskError` and crashed the background task. The tests missed it
    because the fake only ever raised Zendesk errors."""

    async def test_a_non_zendesk_fetch_error_does_not_escape(self):
        from app.auth.errors import NoStoredTokenError

        zendesk = FakeZendesk(get_error=NoStoredTokenError("no token stored"))
        await TicketProcessor(zendesk, FakeTriage()).process(EVENT)

    async def test_a_non_zendesk_fetch_error_is_logged(self, caplog):
        with caplog.at_level("ERROR"):
            await TicketProcessor(
                FakeZendesk(get_error=RuntimeError("something else")), FakeTriage()
            ).process(EVENT)
        assert "4242" in caplog.text

    async def test_a_non_zendesk_write_error_does_not_escape(self):
        from app.auth.errors import ReauthorizationRequiredError

        zendesk = FakeZendesk(apply_error=ReauthorizationRequiredError("expired"))
        await TicketProcessor(zendesk, FakeTriage()).process(EVENT)

    @pytest.mark.parametrize(
        "error",
        [RuntimeError("x"), KeyError("x"), ValueError("x"), TypeError("x")],
    )
    async def test_nothing_at_all_escapes_the_processor(self, error):
        await TicketProcessor(FakeZendesk(get_error=error), FakeTriage()).process(EVENT)
        await TicketProcessor(FakeZendesk(apply_error=error), FakeTriage()).process(EVENT)


class FakeSlack:
    def __init__(self, configured: bool = True, result: bool = True, error=None):
        self.configured = configured
        self._result = result
        self._error = error
        self.payloads = []

    async def notify(self, payload):
        if self._error:
            raise self._error
        self.payloads.append(payload)
        return self._result


class FakeZendeskWithComments(FakeZendesk):
    def __init__(self, comments=(), comments_error=None, **kwargs):
        super().__init__(**kwargs)
        self._comments = list(comments)
        self._comments_error = comments_error

    async def get_comments(self, ticket_id: int):
        if self._comments_error:
            raise self._comments_error
        return self._comments


SOLVED = WebhookEvent(
    type=EventType.TICKET_SOLVED, ticket_id=4242, raw_type="ticket.solved"
)


def solved_ticket():
    return ZendeskTicket.from_api(
        {
            "id": 4242,
            "subject": "Charged twice",
            "tags": ["ai-triaged", "ai-category-billing"],
            "created_at": "2026-08-18T09:00:00Z",
            "updated_at": "2026-08-18T12:00:00Z",
        }
    )


class TestResolutionNotification:
    async def test_posts_a_summary_to_slack(self):
        from app.zendesk.models import ZendeskComment

        zendesk = FakeZendeskWithComments(
            ticket=solved_ticket(),
            comments=[
                ZendeskComment.from_api(
                    {"id": 1, "body": "AI triage\n--- Suggested reply ---\nHello there",
                     "public": False}
                )
            ],
        )
        slack = FakeSlack()
        await TicketProcessor(zendesk, FakeTriage(), slack).notify_resolution(SOLVED)

        assert len(slack.payloads) == 1
        assert "4242" in str(slack.payloads[0])
        assert "billing" in str(slack.payloads[0])

    async def test_does_nothing_when_slack_is_unconfigured(self):
        zendesk = FakeZendeskWithComments(ticket=solved_ticket())
        slack = FakeSlack(configured=False)
        await TicketProcessor(zendesk, FakeTriage(), slack).notify_resolution(SOLVED)
        assert slack.payloads == []

    async def test_does_nothing_when_slack_is_absent_entirely(self):
        zendesk = FakeZendeskWithComments(ticket=solved_ticket())
        await TicketProcessor(zendesk, FakeTriage(), None).notify_resolution(SOLVED)

    async def test_includes_a_ticket_link_when_configured(self):
        zendesk = FakeZendeskWithComments(ticket=solved_ticket())
        slack = FakeSlack()
        processor = TicketProcessor(
            zendesk,
            FakeTriage(),
            slack,
            ticket_url_template="https://acme.zendesk.com/agent/tickets/{ticket_id}",
        )
        await processor.notify_resolution(SOLVED)
        assert "agent/tickets/4242" in str(slack.payloads[0])


class TestSlackCannotBreakAnything:
    """The decoupling requirement, enforced rather than promised."""

    async def test_a_slack_exception_does_not_escape(self):
        zendesk = FakeZendeskWithComments(ticket=solved_ticket())
        slack = FakeSlack(error=RuntimeError("slack exploded"))
        await TicketProcessor(zendesk, FakeTriage(), slack).notify_resolution(SOLVED)

    async def test_a_slack_rejection_does_not_escape(self):
        zendesk = FakeZendeskWithComments(ticket=solved_ticket())
        slack = FakeSlack(result=False)
        await TicketProcessor(zendesk, FakeTriage(), slack).notify_resolution(SOLVED)

    async def test_a_failed_ticket_fetch_does_not_escape(self):
        zendesk = FakeZendeskWithComments(get_error=ZendeskUnavailableError("503"))
        slack = FakeSlack()
        await TicketProcessor(zendesk, FakeTriage(), slack).notify_resolution(SOLVED)
        assert slack.payloads == []

    async def test_a_failed_comment_fetch_does_not_escape(self):
        zendesk = FakeZendeskWithComments(
            ticket=solved_ticket(), comments_error=ZendeskUnavailableError("503")
        )
        slack = FakeSlack()
        await TicketProcessor(zendesk, FakeTriage(), slack).notify_resolution(SOLVED)
        assert slack.payloads == []

    async def test_notification_failures_never_touch_the_triage_path(self):
        """The two paths share no state, so a broken Slack leaves triage intact."""
        zendesk = FakeZendeskWithComments(ticket=a_ticket())
        triage = FakeTriage()
        slack = FakeSlack(error=RuntimeError("down"))
        processor = TicketProcessor(zendesk, triage, slack)

        await processor.notify_resolution(SOLVED)
        await processor.process(EVENT)

        assert len(triage.calls) == 1
        assert len(zendesk.applied) == 1
