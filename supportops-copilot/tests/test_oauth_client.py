"""Token exchange and refresh against a mocked Zendesk token endpoint."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from app.auth.errors import (
    NoStoredTokenError,
    ReauthorizationRequiredError,
    TokenExchangeError,
)
from app.auth.oauth import ZendeskOAuthClient
from app.auth.token_store import TokenRecord

TOKEN_URL = "https://acme-sandbox.zendesk.com/api/v2/oauth/tokens"
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


class TestAuthorizeUrl:
    def test_contains_every_required_oauth_parameter(self, oauth_client):
        url = oauth_client.build_authorize_url("state-123")
        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        assert parsed.netloc == "acme-sandbox.zendesk.com"
        assert parsed.path == "/oauth/authorizations/new"
        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["supportops_copilot"]
        assert params["scope"] == ["read write"]
        assert params["state"] == ["state-123"]
        assert params["redirect_uri"] == ["http://localhost:8000/auth/zendesk/callback"]

    def test_never_leaks_the_client_secret(self, oauth_client):
        assert "s3cret" not in oauth_client.build_authorize_url("state-123")

    def test_scope_separator_is_percent_encoded_not_a_plus(self, oauth_client):
        """RFC 6749 scope is space-delimited; "+" is not universally decoded."""
        assert "scope=read%20write" in oauth_client.build_authorize_url("s")


class TestExchangeCode:
    @respx.mock
    async def test_successful_exchange_persists_an_encrypted_token(self, oauth_client):
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "access_token": "zd_access_1",
                    "token_type": "bearer",
                    "scope": "read write",
                },
            )
        )
        record = await oauth_client.exchange_code("auth-code-1", now=NOW)

        assert record.access_token == "zd_access_1"
        assert oauth_client.store.load() == record
        assert b"zd_access_1" not in oauth_client.store.path.read_bytes()

    @respx.mock
    async def test_sends_the_authorization_code_grant_payload(self, oauth_client):
        route = respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "a"})
        )
        await oauth_client.exchange_code("auth-code-1", now=NOW)

        body = json.loads(route.calls.last.request.content)
        assert body["grant_type"] == "authorization_code"
        assert body["code"] == "auth-code-1"
        assert body["client_id"] == "supportops_copilot"
        assert body["client_secret"] == "s3cret"
        assert body["redirect_uri"] == "http://localhost:8000/auth/zendesk/callback"
        assert "expires_in" not in body

    @respx.mock
    async def test_requests_an_expiring_token_when_configured(self, settings, token_store):
        settings.zendesk_token_expires_in = 3600
        client = ZendeskOAuthClient(settings, token_store)
        route = respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "a", "expires_in": 3600, "refresh_token": "r1"},
            )
        )
        record = await client.exchange_code("code", now=NOW)

        assert json.loads(route.calls.last.request.content)["expires_in"] == 3600
        assert record.expires_at == NOW + timedelta(hours=1)
        assert record.refresh_token == "r1"

    @respx.mock
    async def test_http_error_raises_typed_error_and_stores_nothing(self, oauth_client):
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(400, json={"error": "invalid_grant"})
        )
        with pytest.raises(TokenExchangeError) as exc:
            await oauth_client.exchange_code("stale-code")

        assert exc.value.status_code == 400
        assert "invalid_grant" in exc.value.body
        assert oauth_client.store.load() is None

    @respx.mock
    async def test_non_json_response_raises_rather_than_crashing(self, oauth_client):
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, text="<html>maintenance</html>")
        )
        with pytest.raises(TokenExchangeError, match="non-JSON"):
            await oauth_client.exchange_code("code")

    @respx.mock
    async def test_json_without_access_token_raises(self, oauth_client):
        respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        with pytest.raises(TokenExchangeError, match="missing access_token"):
            await oauth_client.exchange_code("code")

    @respx.mock
    async def test_network_failure_raises_typed_error(self, oauth_client):
        respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("dns failure"))
        with pytest.raises(TokenExchangeError, match="transport error"):
            await oauth_client.exchange_code("code")


class TestRefresh:
    @respx.mock
    async def test_refresh_replaces_the_stored_token(self, oauth_client):
        old = TokenRecord(
            access_token="old", refresh_token="r1", expires_at=NOW, obtained_at=NOW
        )
        oauth_client.store.save(old)
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200,
                json={"access_token": "new", "refresh_token": "r2", "expires_in": 3600},
            )
        )
        renewed = await oauth_client.refresh(old, now=NOW)

        assert renewed.access_token == "new"
        assert renewed.refresh_token == "r2"
        assert oauth_client.store.load().access_token == "new"

    @respx.mock
    async def test_sends_the_refresh_token_grant_payload(self, oauth_client):
        old = TokenRecord(access_token="old", refresh_token="r1", expires_at=NOW)
        route = respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "new"})
        )
        await oauth_client.refresh(old, now=NOW)

        body = json.loads(route.calls.last.request.content)
        assert body["grant_type"] == "refresh_token"
        assert body["refresh_token"] == "r1"
        assert "code" not in body

    @respx.mock
    async def test_non_rotating_response_keeps_the_existing_refresh_token(self, oauth_client):
        old = TokenRecord(access_token="old", refresh_token="r1", expires_at=NOW)
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(200, json={"access_token": "new", "expires_in": 3600})
        )
        renewed = await oauth_client.refresh(old, now=NOW)
        assert renewed.refresh_token == "r1"

    async def test_refresh_without_a_refresh_token_demands_reauthorization(self, oauth_client):
        record = TokenRecord(access_token="old", refresh_token=None, expires_at=NOW)
        with pytest.raises(ReauthorizationRequiredError):
            await oauth_client.refresh(record)

    @respx.mock
    async def test_rejected_refresh_leaves_the_old_token_in_place(self, oauth_client):
        old = TokenRecord(access_token="old", refresh_token="r1", expires_at=NOW)
        oauth_client.store.save(old)
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(401, json={"error": "invalid_grant"})
        )
        with pytest.raises(TokenExchangeError):
            await oauth_client.refresh(old, now=NOW)
        assert oauth_client.store.load().access_token == "old"


class TestGetValidAccessToken:
    async def test_raises_when_nothing_has_been_authorized(self, oauth_client):
        with pytest.raises(NoStoredTokenError):
            await oauth_client.get_valid_access_token()

    async def test_non_expiring_token_is_returned_untouched(self, oauth_client):
        oauth_client.store.save(TokenRecord(access_token="forever", expires_at=None))
        assert await oauth_client.get_valid_access_token(now=NOW) == "forever"

    async def test_fresh_token_is_returned_without_a_network_call(self, oauth_client):
        oauth_client.store.save(
            TokenRecord(
                access_token="fresh",
                refresh_token="r1",
                expires_at=NOW + timedelta(hours=5),
            )
        )
        assert await oauth_client.get_valid_access_token(now=NOW) == "fresh"

    @respx.mock
    async def test_token_inside_the_leeway_window_is_refreshed(self, oauth_client):
        oauth_client.store.save(
            TokenRecord(
                access_token="expiring",
                refresh_token="r1",
                expires_at=NOW + timedelta(seconds=60),
            )
        )
        respx.post(TOKEN_URL).mock(
            return_value=httpx.Response(
                200, json={"access_token": "renewed", "expires_in": 3600}
            )
        )
        assert await oauth_client.get_valid_access_token(now=NOW) == "renewed"

    async def test_expired_token_without_refresh_demands_reauthorization(self, oauth_client):
        oauth_client.store.save(
            TokenRecord(
                access_token="dead",
                refresh_token=None,
                expires_at=NOW - timedelta(hours=1),
            )
        )
        with pytest.raises(ReauthorizationRequiredError):
            await oauth_client.get_valid_access_token(now=NOW)
