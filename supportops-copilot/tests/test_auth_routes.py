"""Endpoint-level tests driving the real FastAPI app with Zendesk stubbed out."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.auth.errors import TokenExchangeError
from app.auth.token_store import TokenRecord
from app.dependencies import (
    get_app_settings,
    get_oauth_client,
    get_state_store,
    get_token_store,
)
from app.main import create_app

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class StubOAuthClient:
    """Stands in for ZendeskOAuthClient so route tests never touch the network."""

    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.exchanged_codes: list[str] = []
        self.raise_on_exchange: Exception | None = None
        self.record = TokenRecord(
            access_token="zd_access",
            scope="read write",
            refresh_token="zd_refresh",
            expires_at=NOW + timedelta(hours=1),
            obtained_at=NOW,
        )

    def build_authorize_url(self, state: str) -> str:
        return (
            "https://acme-sandbox.zendesk.com/oauth/authorizations/new"
            f"?response_type=code&client_id=supportops_copilot&state={state}"
        )

    async def exchange_code(self, code: str, *, now=None) -> TokenRecord:
        if self.raise_on_exchange:
            raise self.raise_on_exchange
        self.exchanged_codes.append(code)
        self.store.save(self.record)
        return self.record


@pytest.fixture
def stub_oauth(settings, token_store) -> StubOAuthClient:
    return StubOAuthClient(settings, token_store)


@pytest.fixture
def client(settings, token_store, state_store, stub_oauth) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_app_settings] = lambda: settings
    app.dependency_overrides[get_token_store] = lambda: token_store
    app.dependency_overrides[get_state_store] = lambda: state_store
    app.dependency_overrides[get_oauth_client] = lambda: stub_oauth
    with TestClient(app) as c:
        yield c


def test_healthz_is_up(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


class TestLogin:
    def test_redirects_to_zendesk_consent(self, client):
        response = client.get("/auth/zendesk/login", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"].startswith(
            "https://acme-sandbox.zendesk.com/oauth/authorizations/new"
        )

    def test_issues_a_fresh_state_on_every_login(self, client, state_store):
        first = _state_from(client.get("/auth/zendesk/login", follow_redirects=False))
        second = _state_from(client.get("/auth/zendesk/login", follow_redirects=False))
        assert first != second
        assert len(state_store) == 2


def _state_from(response) -> str:
    return parse_qs(urlparse(response.headers["location"]).query)["state"][0]


class TestCallback:
    def test_valid_callback_stores_a_token(self, client, stub_oauth, token_store):
        state = _state_from(client.get("/auth/zendesk/login", follow_redirects=False))
        response = client.get(f"/auth/zendesk/callback?code=abc&state={state}")

        assert response.status_code == 200
        assert response.json()["connected"] is True
        assert stub_oauth.exchanged_codes == ["abc"]
        assert token_store.load() is not None

    def test_response_body_never_contains_the_token(self, client):
        state = _state_from(client.get("/auth/zendesk/login", follow_redirects=False))
        response = client.get(f"/auth/zendesk/callback?code=abc&state={state}")
        assert "zd_access" not in response.text
        assert "zd_refresh" not in response.text

    def test_forged_state_is_rejected_before_the_code_is_used(self, client, stub_oauth):
        client.get("/auth/zendesk/login", follow_redirects=False)
        response = client.get("/auth/zendesk/callback?code=abc&state=forged")

        assert response.status_code == 400
        assert stub_oauth.exchanged_codes == []

    def test_missing_state_is_rejected(self, client, stub_oauth):
        response = client.get("/auth/zendesk/callback?code=abc")
        assert response.status_code == 400
        assert stub_oauth.exchanged_codes == []

    def test_replayed_callback_is_rejected(self, client, stub_oauth):
        state = _state_from(client.get("/auth/zendesk/login", follow_redirects=False))
        assert client.get(f"/auth/zendesk/callback?code=abc&state={state}").status_code == 200

        replay = client.get(f"/auth/zendesk/callback?code=abc&state={state}")
        assert replay.status_code == 400
        assert stub_oauth.exchanged_codes == ["abc"]

    def test_user_denied_consent_is_surfaced_as_400(self, client, stub_oauth):
        state = _state_from(client.get("/auth/zendesk/login", follow_redirects=False))
        response = client.get(
            f"/auth/zendesk/callback?error=access_denied"
            f"&error_description=User+said+no&state={state}"
        )
        assert response.status_code == 400
        assert response.json()["detail"]["error"] == "access_denied"
        assert stub_oauth.exchanged_codes == []

    def test_callback_without_a_code_is_rejected(self, client):
        state = _state_from(client.get("/auth/zendesk/login", follow_redirects=False))
        response = client.get(f"/auth/zendesk/callback?state={state}")
        assert response.status_code == 400
        assert "no authorization code" in response.json()["detail"]

    def test_zendesk_rejecting_the_exchange_returns_502_not_500(self, client, stub_oauth):
        stub_oauth.raise_on_exchange = TokenExchangeError(400, '{"error":"invalid_grant"}')
        state = _state_from(client.get("/auth/zendesk/login", follow_redirects=False))
        response = client.get(f"/auth/zendesk/callback?code=abc&state={state}")

        assert response.status_code == 502
        # The upstream body may contain sensitive detail; it must not be echoed.
        assert "invalid_grant" not in response.text


class TestStatus:
    def test_reports_disconnected_before_authorization(self, client):
        body = client.get("/auth/zendesk/status").json()
        assert body["connected"] is False

    def test_reports_connected_details_without_the_token(self, client, token_store):
        token_store.save(
            TokenRecord(
                access_token="zd_access",
                scope="read write",
                refresh_token="zd_refresh",
                expires_at=NOW + timedelta(days=365),
            )
        )
        body = client.get("/auth/zendesk/status").json()

        assert body["connected"] is True
        assert body["scope"] == "read write"
        assert body["refreshable"] is True
        assert body["stale"] is False
        assert "zd_access" not in client.get("/auth/zendesk/status").text

    def test_reports_a_non_expiring_token_as_never_stale(self, client, token_store):
        token_store.save(TokenRecord(access_token="forever", expires_at=None))
        body = client.get("/auth/zendesk/status").json()
        assert body["expires"] is False
        assert body["stale"] is False

    def test_corrupt_store_surfaces_as_an_explicit_error(self, client, token_store):
        token_store.save(TokenRecord(access_token="a"))
        token_store.path.write_bytes(b"not-a-fernet-token")
        response = client.get("/auth/zendesk/status")
        assert response.status_code == 500
