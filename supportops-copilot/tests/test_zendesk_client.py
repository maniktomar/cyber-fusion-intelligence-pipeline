"""Zendesk API client: happy path, error mapping, and retry behaviour."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.auth.oauth import ZendeskOAuthClient
from app.auth.token_store import TokenRecord
from app.llm.schemas import Category, Classification, Draft, Sentiment, Urgency
from app.triage.decision import FallbackReason, apply_decision, flag_only
from app.zendesk.client import ZendeskClient
from app.zendesk.errors import (
    ZendeskAuthError,
    ZendeskNotFoundError,
    ZendeskRateLimitError,
    ZendeskRequestError,
    ZendeskUnavailableError,
)
from app.zendesk.models import ZendeskTicket

BASE = "https://acme-sandbox.zendesk.com"
TICKET_URL = f"{BASE}/api/v2/tickets/4242.json"
TAGS_URL = f"{BASE}/api/v2/tickets/4242/tags.json"

TICKET_JSON = {
    "ticket": {
        "id": 4242,
        "subject": "Charged twice this month",
        "description": "My card shows two identical charges.",
        "status": "open",
        "priority": "normal",
        "tags": ["billing"],
        "requester_id": 77,
    }
}


@pytest.fixture
def slept() -> list[float]:
    return []


@pytest.fixture
def zendesk(settings, token_store, slept) -> ZendeskClient:
    token_store.save(TokenRecord(access_token="zd_token", expires_at=None))
    oauth = ZendeskOAuthClient(settings, token_store)

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    return ZendeskClient(oauth, max_retries=3, sleep=fake_sleep)


def a_decision():
    return apply_decision(
        Classification(
            category=Category.BILLING,
            urgency=Urgency.NORMAL,
            sentiment=Sentiment.NEUTRAL,
            confidence=0.9,
            reasoning="Duplicate charge.",
        ),
        Draft(
            body="Thanks for flagging this duplicate charge; we are reversing it now.",
            confidence=0.9,
            grounded_in=["kb-001"],
        ),
    )


class TestGetTicket:
    @respx.mock
    async def test_parses_a_ticket(self, zendesk):
        respx.get(TICKET_URL).mock(return_value=httpx.Response(200, json=TICKET_JSON))
        ticket = await zendesk.get_ticket(4242)

        assert ticket.id == 4242
        assert ticket.subject == "Charged twice this month"
        assert ticket.tags == ["billing"]

    @respx.mock
    async def test_sends_the_bearer_token(self, zendesk):
        route = respx.get(TICKET_URL).mock(
            return_value=httpx.Response(200, json=TICKET_JSON)
        )
        await zendesk.get_ticket(4242)
        assert route.calls.last.request.headers["authorization"] == "Bearer zd_token"

    @respx.mock
    async def test_missing_optional_fields_are_tolerated(self, zendesk):
        respx.get(TICKET_URL).mock(
            return_value=httpx.Response(200, json={"ticket": {"id": 4242}})
        )
        ticket = await zendesk.get_ticket(4242)

        assert ticket.subject == ""
        assert ticket.priority is None
        assert ticket.tags == []

    def test_a_ticket_with_no_id_is_rejected(self):
        with pytest.raises(ValueError, match="no usable id"):
            ZendeskTicket.from_api({"subject": "orphan"})


class TestErrorMapping:
    @respx.mock
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_failures(self, zendesk, status):
        respx.get(TICKET_URL).mock(return_value=httpx.Response(status, json={}))
        with pytest.raises(ZendeskAuthError):
            await zendesk.get_ticket(4242)

    @respx.mock
    async def test_not_found_is_not_retried(self, zendesk):
        route = respx.get(TICKET_URL).mock(return_value=httpx.Response(404, json={}))
        with pytest.raises(ZendeskNotFoundError):
            await zendesk.get_ticket(4242)
        assert route.call_count == 1

    @respx.mock
    async def test_unexpected_4xx_surfaces_the_status(self, zendesk):
        respx.get(TICKET_URL).mock(
            return_value=httpx.Response(422, text="unprocessable")
        )
        with pytest.raises(ZendeskRequestError) as exc:
            await zendesk.get_ticket(4242)
        assert exc.value.status_code == 422

    @respx.mock
    async def test_non_json_success_body_is_an_error_not_a_crash(self, zendesk):
        respx.get(TICKET_URL).mock(return_value=httpx.Response(200, text="<html>"))
        with pytest.raises(ZendeskUnavailableError, match="non-JSON"):
            await zendesk.get_ticket(4242)


class TestRetries:
    @respx.mock
    async def test_a_transient_500_is_retried_then_succeeds(self, zendesk, slept):
        respx.get(TICKET_URL).mock(
            side_effect=[
                httpx.Response(503, json={}),
                httpx.Response(200, json=TICKET_JSON),
            ]
        )
        ticket = await zendesk.get_ticket(4242)

        assert ticket.id == 4242
        assert len(slept) == 1

    @respx.mock
    async def test_retries_are_bounded(self, zendesk):
        route = respx.get(TICKET_URL).mock(return_value=httpx.Response(500, json={}))
        with pytest.raises(ZendeskUnavailableError):
            await zendesk.get_ticket(4242)
        assert route.call_count == 3

    @respx.mock
    async def test_a_transport_error_is_retried(self, zendesk):
        respx.get(TICKET_URL).mock(
            side_effect=[
                httpx.ConnectError("reset"),
                httpx.Response(200, json=TICKET_JSON),
            ]
        )
        assert (await zendesk.get_ticket(4242)).id == 4242

    @respx.mock
    async def test_rate_limit_waits_the_stated_delay(self, zendesk, slept):
        respx.get(TICKET_URL).mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "3"}, json={}),
                httpx.Response(200, json=TICKET_JSON),
            ]
        )
        await zendesk.get_ticket(4242)
        assert slept == [3.0]

    @respx.mock
    async def test_an_unparseable_retry_after_falls_back_to_a_default(self, zendesk):
        """60s exceeds the in-request sleep cap, so it gives up rather than block."""
        respx.get(TICKET_URL).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "soon"}, json={})
        )
        with pytest.raises(ZendeskRateLimitError) as exc:
            await zendesk.get_ticket(4242)
        assert exc.value.retry_after == 60.0

    @respx.mock
    async def test_a_long_retry_after_gives_up_rather_than_blocking(self, zendesk, slept):
        respx.get(TICKET_URL).mock(
            return_value=httpx.Response(429, headers={"Retry-After": "900"}, json={})
        )
        with pytest.raises(ZendeskRateLimitError):
            await zendesk.get_ticket(4242)
        assert slept == []

    @respx.mock
    async def test_backoff_is_jittered_not_fixed(self, zendesk, slept):
        """Lockstep retries across a burst recreate the spike that caused them."""
        respx.get(TICKET_URL).mock(return_value=httpx.Response(500, json={}))
        with pytest.raises(ZendeskUnavailableError):
            await zendesk.get_ticket(4242)
        assert all(0 <= s <= 8 for s in slept)


class TestWrites:
    @respx.mock
    async def test_internal_note_is_never_public(self, zendesk):
        """The single most consequential field in the client. A `true` here
        sends the AI's draft straight to the customer."""
        route = respx.put(TICKET_URL).mock(return_value=httpx.Response(200, json={}))
        await zendesk.add_internal_note(4242, "AI triage note")

        import json as jsonlib

        sent = jsonlib.loads(route.calls.last.request.content)
        assert sent["ticket"]["comment"]["public"] is False
        assert sent["ticket"]["comment"]["body"] == "AI triage note"

    @respx.mock
    async def test_tags_are_sent_to_the_tags_endpoint(self, zendesk):
        route = respx.put(TAGS_URL).mock(
            return_value=httpx.Response(200, json={"tags": ["billing", "ai-triaged"]})
        )
        result = await zendesk.add_tags(4242, ["ai-triaged"])

        import json as jsonlib

        assert jsonlib.loads(route.calls.last.request.content) == {"tags": ["ai-triaged"]}
        assert result == ["billing", "ai-triaged"]

    @respx.mock
    async def test_an_empty_tag_list_makes_no_request(self, zendesk):
        route = respx.put(TAGS_URL)
        assert await zendesk.add_tags(4242, []) == []
        assert route.call_count == 0

    @respx.mock
    async def test_apply_writes_the_note_before_the_tags(self, zendesk):
        """A tag with no explanation behind it is the silent failure we avoid."""
        order: list[str] = []
        respx.put(TICKET_URL).mock(
            side_effect=lambda r: order.append("note") or httpx.Response(200, json={})
        )
        respx.put(TAGS_URL).mock(
            side_effect=lambda r: order.append("tags") or httpx.Response(200, json={})
        )
        await zendesk.apply(4242, a_decision())
        assert order == ["note", "tags"]

    @respx.mock
    async def test_apply_sends_the_fallback_note_and_tag(self, zendesk):
        import json as jsonlib

        note_route = respx.put(TICKET_URL).mock(
            return_value=httpx.Response(200, json={})
        )
        tag_route = respx.put(TAGS_URL).mock(return_value=httpx.Response(200, json={}))
        decision = flag_only(FallbackReason.LLM_UNAVAILABLE, "503 from the model")

        await zendesk.apply(4242, decision)

        note = jsonlib.loads(note_route.calls.last.request.content)
        assert "needs manual triage" in note["ticket"]["comment"]["body"]
        assert jsonlib.loads(tag_route.calls.last.request.content) == {
            "tags": ["ai-needs-manual-triage"]
        }

    @respx.mock
    async def test_apply_propagates_a_write_failure(self, zendesk):
        respx.put(TICKET_URL).mock(return_value=httpx.Response(403, json={}))
        with pytest.raises(ZendeskAuthError):
            await zendesk.apply(4242, a_decision())


class TestLoopGuard:
    @pytest.mark.parametrize(
        ("tags", "expected"),
        [
            ([], False),
            (["billing", "urgent"], False),
            (["ai-triaged"], True),
            (["ai-needs-manual-triage"], True),
            (["billing", "ai-category-billing"], True),
            (["aircraft"], False),
        ],
    )
    def test_already_triaged_detects_our_own_marks(self, tags, expected):
        ticket = ZendeskTicket.from_api({"id": 1, "tags": tags})
        assert ticket.already_triaged is expected


class TestAuthErrorTranslation:
    """Regression: an OAuth-layer error once escaped the client's own error
    taxonomy and crashed a background task, losing the ticket entirely."""

    @respx.mock
    async def test_no_stored_token_surfaces_as_a_zendesk_auth_error(
        self, settings, token_store, slept
    ):
        from app.auth.oauth import ZendeskOAuthClient as OAuth

        token_store.clear()
        client = ZendeskClient(OAuth(settings, token_store), sleep=lambda s: None)

        with pytest.raises(ZendeskAuthError, match="Cannot authenticate"):
            await client.get_ticket(4242)

    @respx.mock
    async def test_an_unrenewable_token_surfaces_as_a_zendesk_auth_error(
        self, settings, token_store
    ):
        from datetime import UTC, datetime, timedelta

        from app.auth.oauth import ZendeskOAuthClient as OAuth

        token_store.save(
            TokenRecord(
                access_token="dead",
                refresh_token=None,
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        client = ZendeskClient(OAuth(settings, token_store), sleep=lambda s: None)

        with pytest.raises(ZendeskAuthError):
            await client.get_ticket(4242)

    @respx.mock
    async def test_the_auth_failure_is_not_retried(self, settings, token_store, slept):
        from app.auth.oauth import ZendeskOAuthClient as OAuth

        token_store.clear()
        route = respx.get(TICKET_URL)
        client = ZendeskClient(OAuth(settings, token_store), sleep=lambda s: None)

        with pytest.raises(ZendeskAuthError):
            await client.get_ticket(4242)
        assert route.call_count == 0
