"""Slack must be unable to break ticket processing. That is the requirement."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import respx

from app.notifications.resolution import DraftUsage, ResolutionSummary
from app.slack.client import SlackNotifier
from app.slack.message import build_resolution_message

WEBHOOK = "https://hooks.slack.com/services/T000/B000/xxxx"


def a_summary(**overrides) -> ResolutionSummary:
    payload = {
        "ticket_id": 4242,
        "subject": "Charged twice this month",
        "category": "billing",
        "resolution_time": timedelta(hours=3, minutes=30),
        "draft_usage": DraftUsage.USED,
        "similarity": 0.94,
    }
    payload.update(overrides)
    return ResolutionSummary(**payload)


class TestMessageBuilding:
    def test_includes_the_four_required_fields(self):
        """The brief asks for ticket ID, category, resolution time, and whether
        the AI draft was used."""
        payload = build_resolution_message(a_summary())
        rendered = str(payload)

        assert "4242" in rendered
        assert "billing" in rendered
        assert "3h 30m" in rendered
        assert "AI draft used" in rendered

    def test_has_a_text_fallback(self):
        """Without it, mobile push and screen readers show an empty message."""
        payload = build_resolution_message(a_summary())
        assert payload["text"].startswith("Ticket #4242 resolved")

    def test_links_the_ticket_when_a_url_is_given(self):
        payload = build_resolution_message(
            a_summary(), ticket_url="https://acme.zendesk.com/agent/tickets/4242"
        )
        assert "acme.zendesk.com/agent/tickets/4242" in str(payload)

    def test_works_without_a_url(self):
        assert "Ticket #4242" in str(build_resolution_message(a_summary()))

    def test_a_long_subject_is_truncated(self):
        payload = build_resolution_message(a_summary(subject="word " * 100))
        assert "…" in str(payload)

    def test_an_empty_subject_does_not_render_blank(self):
        assert "(no subject)" in str(build_resolution_message(a_summary(subject="")))

    def test_an_unclassified_ticket_says_so(self):
        payload = build_resolution_message(a_summary(category=None))
        assert "not classified" in str(payload)

    @pytest.mark.parametrize("usage", list(DraftUsage))
    def test_every_usage_state_renders(self, usage):
        payload = build_resolution_message(a_summary(draft_usage=usage, similarity=None))
        assert payload["blocks"]

    def test_similarity_is_omitted_when_unknown(self):
        payload = build_resolution_message(a_summary(similarity=None))
        assert "similarity" not in str(payload).lower()


class TestNotifierDecoupling:
    @respx.mock
    async def test_a_successful_post_returns_true(self):
        respx.post(WEBHOOK).mock(return_value=httpx.Response(200, text="ok"))
        assert await SlackNotifier(WEBHOOK).notify({"text": "hi"}) is True

    @respx.mock
    async def test_a_slack_error_returns_false_rather_than_raising(self):
        respx.post(WEBHOOK).mock(return_value=httpx.Response(500, text="oh no"))
        assert await SlackNotifier(WEBHOOK).notify({"text": "hi"}) is False

    @respx.mock
    async def test_a_rejected_payload_returns_false(self):
        respx.post(WEBHOOK).mock(return_value=httpx.Response(400, text="invalid_blocks"))
        assert await SlackNotifier(WEBHOOK).notify({"text": "hi"}) is False

    @respx.mock
    async def test_a_network_failure_returns_false(self):
        respx.post(WEBHOOK).mock(side_effect=httpx.ConnectError("dns"))
        assert await SlackNotifier(WEBHOOK).notify({"text": "hi"}) is False

    @respx.mock
    async def test_a_timeout_returns_false(self):
        respx.post(WEBHOOK).mock(side_effect=httpx.ReadTimeout("slow"))
        assert await SlackNotifier(WEBHOOK).notify({"text": "hi"}) is False

    @respx.mock
    async def test_an_unexpected_exception_returns_false(self):
        """The contract is that this cannot break the caller, and a contract
        with exceptions in it is not one."""
        respx.post(WEBHOOK).mock(side_effect=RuntimeError("something bizarre"))
        assert await SlackNotifier(WEBHOOK).notify({"text": "hi"}) is False

    @respx.mock
    async def test_a_failure_is_not_retried(self):
        """A stale notification arriving hours late is worse than none."""
        route = respx.post(WEBHOOK).mock(return_value=httpx.Response(500))
        await SlackNotifier(WEBHOOK).notify({"text": "hi"})
        assert route.call_count == 1

    async def test_an_unconfigured_notifier_is_a_no_op_not_an_error(self):
        notifier = SlackNotifier("")
        assert notifier.configured is False
        assert await notifier.notify({"text": "hi"}) is False

    @respx.mock
    async def test_the_payload_is_sent_as_json(self):
        route = respx.post(WEBHOOK).mock(return_value=httpx.Response(200))
        await SlackNotifier(WEBHOOK).notify(build_resolution_message(a_summary()))

        import json

        sent = json.loads(route.calls.last.request.content)
        assert "blocks" in sent and "text" in sent
