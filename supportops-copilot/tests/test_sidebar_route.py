"""The sidebar endpoint, through the real app."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_app_settings, get_zendesk_client
from app.llm.schemas import Category, Classification, Draft, Sentiment, Urgency
from app.main import create_app
from app.triage.decision import FallbackReason, apply_decision, flag_only
from app.zendesk.errors import (
    ZendeskAuthError,
    ZendeskNotFoundError,
    ZendeskUnavailableError,
)
from app.zendesk.models import ZendeskComment, ZendeskTicket

SECRET = "sidebar_route_secret_" + "z" * 43


def a_note() -> str:
    return apply_decision(
        Classification(
            category=Category.BILLING,
            urgency=Urgency.HIGH,
            sentiment=Sentiment.FRUSTRATED,
            confidence=0.92,
            reasoning="Duplicate charge.",
        ),
        Draft(
            body="Thanks for flagging this duplicate charge; we are reversing it.",
            confidence=0.85,
            grounded_in=["kb-001"],
        ),
    ).internal_note


def token(secret: str = SECRET, **claims) -> str:
    payload = {"exp": datetime.now(UTC) + timedelta(minutes=2), "sub": "agent"}
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm="HS256")


class FakeZendesk:
    def __init__(self, comments=None, ticket_error=None, comments_error=None):
        self._comments = comments if comments is not None else [
            ZendeskComment.from_api({"id": 1, "body": a_note(), "public": False})
        ]
        self._ticket_error = ticket_error
        self._comments_error = comments_error

    async def get_ticket(self, ticket_id: int):
        if self._ticket_error:
            raise self._ticket_error
        return ZendeskTicket.from_api(
            {"id": ticket_id, "subject": "Charged twice", "status": "open"}
        )

    async def get_comments(self, ticket_id: int):
        if self._comments_error:
            raise self._comments_error
        return self._comments


@pytest.fixture
def zendesk() -> FakeZendesk:
    return FakeZendesk()


@pytest.fixture
def client(settings, zendesk) -> TestClient:
    settings.zendesk_app_secret = SECRET
    settings.zendesk_app_issuer = ""
    app = create_app()
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_zendesk_client] = lambda: zendesk
    with TestClient(app) as c:
        yield c


def get(client, ticket_id: int = 4242, auth: str | None = None):
    headers = {"Authorization": auth if auth is not None else f"Bearer {token()}"}
    return client.get(f"/api/sidebar/tickets/{ticket_id}/triage", headers=headers)


class TestAuthenticated:
    def test_returns_the_triage_state(self, client):
        response = get(client)

        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "triaged"
        assert body["category"] == "billing"
        assert body["draft_confidence"] == 0.85

    def test_includes_the_draft(self, client):
        assert "duplicate charge" in get(client).json()["draft"]

    def test_a_flagged_ticket_reports_the_reason(self, settings, client, zendesk):
        zendesk._comments = [
            ZendeskComment.from_api(
                {
                    "id": 1,
                    "body": flag_only(FallbackReason.MODEL_REFUSED, "cyber").internal_note,
                    "public": False,
                }
            )
        ]
        body = get(client).json()

        assert body["state"] == "needs_manual_triage"
        assert body["fallback_reason"] == "model_refused"
        assert body["draft"] is None

    def test_an_untriaged_ticket_reports_not_triaged(self, client, zendesk):
        zendesk._comments = []
        assert get(client).json()["state"] == "not_triaged"


class TestUnauthenticated:
    def test_no_header_is_rejected(self, client):
        response = client.get("/api/sidebar/tickets/4242/triage")
        assert response.status_code == 401

    def test_a_token_signed_with_the_wrong_secret_is_rejected(self, client):
        wrong = token(secret="not_the_secret_" + "q" * 49)
        assert get(client, auth=f"Bearer {wrong}").status_code == 401

    def test_an_expired_token_is_rejected(self, client):
        expired = token(exp=datetime.now(UTC) - timedelta(hours=1))
        assert get(client, auth=f"Bearer {expired}").status_code == 401

    def test_a_non_bearer_header_is_rejected(self, client):
        assert get(client, auth="Basic dXNlcjpwYXNz").status_code == 401

    def test_rejections_do_not_say_which_check_failed(self, client):
        details = {
            get(client, auth="Bearer garbage").json()["detail"],
            get(
                client, auth=f"Bearer {token(exp=datetime.now(UTC) - timedelta(hours=1))}"
            ).json()["detail"],
            get(client, auth="Basic x").json()["detail"],
        }
        assert len(details) == 1

    def test_an_unset_app_secret_rejects_everything(self, settings, zendesk):
        settings.zendesk_app_secret = ""
        app = create_app()
        app.dependency_overrides[get_app_settings] = lambda: settings
        app.dependency_overrides[get_zendesk_client] = lambda: zendesk
        with TestClient(app) as c:
            assert get(c).status_code == 401

    def test_zendesk_is_never_called_for_an_unauthenticated_request(self, client):
        """The auth check must precede any read of customer data."""
        called = []

        class Recording(FakeZendesk):
            async def get_ticket(self, ticket_id):
                called.append(ticket_id)
                return await super().get_ticket(ticket_id)

        client.app.dependency_overrides[get_zendesk_client] = lambda: Recording()
        get(client, auth="Bearer nonsense")
        assert called == []


class TestUpstreamFailures:
    def test_a_missing_ticket_is_a_404(self, settings, zendesk):
        zendesk._ticket_error = ZendeskNotFoundError("gone")
        app = create_app()
        settings.zendesk_app_secret = SECRET
        app.dependency_overrides[get_app_settings] = lambda: settings
        app.dependency_overrides[get_zendesk_client] = lambda: zendesk
        with TestClient(app) as c:
            assert get(c).status_code == 404

    def test_our_broken_zendesk_token_is_a_502_not_a_401(self, settings, zendesk):
        """A 401 would send the agent hunting for a problem with their own
        session, when it is our credential that is broken."""
        zendesk._ticket_error = ZendeskAuthError("token revoked")
        settings.zendesk_app_secret = SECRET
        app = create_app()
        app.dependency_overrides[get_app_settings] = lambda: settings
        app.dependency_overrides[get_zendesk_client] = lambda: zendesk
        with TestClient(app) as c:
            response = get(c)
            assert response.status_code == 502
            assert "re-authorising" in response.json()["detail"]

    def test_zendesk_being_down_is_a_502(self, settings, zendesk):
        zendesk._comments_error = ZendeskUnavailableError("503")
        settings.zendesk_app_secret = SECRET
        app = create_app()
        app.dependency_overrides[get_app_settings] = lambda: settings
        app.dependency_overrides[get_zendesk_client] = lambda: zendesk
        with TestClient(app) as c:
            assert get(c).status_code == 502


class TestInputValidation:
    @pytest.mark.parametrize("ticket_id", [0, -1])
    def test_a_non_positive_ticket_id_is_a_400(self, client, ticket_id):
        assert get(client, ticket_id=ticket_id).status_code == 400

    def test_a_non_numeric_ticket_id_is_a_422(self, client):
        response = client.get(
            "/api/sidebar/tickets/abc/triage",
            headers={"Authorization": f"Bearer {token()}"},
        )
        assert response.status_code == 422


class TestReadOnly:
    def test_the_sidebar_router_exposes_only_get(self):
        """The sidebar shows what triage decided; it must never write."""
        app = create_app()
        sidebar_routes = [
            r for r in app.routes if getattr(r, "path", "").startswith("/api/sidebar")
        ]
        assert sidebar_routes
        for route in sidebar_routes:
            assert set(route.methods) <= {"GET", "HEAD"}
