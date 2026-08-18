"""Zendesk OAuth2 authorization-code client.

Implements the full flow: build the consent URL, exchange the returned code for
an access token, persist it encrypted, and renew it before expiry when Zendesk
issued a refresh token.

A note on refresh, because it shapes this module: Zendesk's default OAuth access
token does not expire and no refresh token is issued. An expiring token is
opt-in — you pass `expires_in` on the exchange and get a `refresh_token` back.
This client handles both cases explicitly rather than assuming either:

  * non-expiring token  -> `is_stale()` is always False, no refresh is attempted
  * expiring + refresh  -> renewed automatically inside the leeway window
  * expiring, no refresh-> `ReauthorizationRequiredError`, a human must re-consent

The third case is the one that silently breaks integrations in production, so it
raises loudly instead of returning a token we know is about to be rejected.
"""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote, urlencode

import httpx

from app.auth.errors import (
    NoStoredTokenError,
    ReauthorizationRequiredError,
    TokenExchangeError,
)
from app.auth.token_store import EncryptedTokenStore, TokenRecord, utcnow
from app.config import Settings

logger = logging.getLogger(__name__)

# Zendesk's token endpoint is fast; a hung request should not pin a worker.
DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class ZendeskOAuthClient:
    def __init__(
        self,
        settings: Settings,
        store: EncryptedTokenStore,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._http_client = http_client

    def build_authorize_url(self, state: str) -> str:
        """The URL to send the admin's browser to for consent."""
        params = {
            "response_type": "code",
            "redirect_uri": self.settings.zendesk_redirect_uri,
            "client_id": self.settings.zendesk_client_id,
            "scope": self.settings.zendesk_scopes,
            "state": state,
        }
        # quote_via=quote so the space in "read write" encodes as %20 rather than
        # "+". Both are legal form encoding, but RFC 6749 defines scope as a
        # space-delimited list and not every provider decodes "+" back to a space.
        query = urlencode(params, quote_via=quote)
        return f"{self.settings.authorize_url}?{query}"

    async def exchange_code(self, code: str, *, now: datetime | None = None) -> TokenRecord:
        """Trade an authorization code for an access token and persist it."""
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.settings.zendesk_client_id,
            "client_secret": self.settings.zendesk_client_secret,
            "redirect_uri": self.settings.zendesk_redirect_uri,
            "scope": self.settings.zendesk_scopes,
        }
        if self.settings.zendesk_token_expires_in:
            payload["expires_in"] = self.settings.zendesk_token_expires_in

        record = await self._post_token_request(payload, now=now)
        self.store.save(record)
        logger.info(
            "Zendesk authorization complete: scope=%s expires_at=%s",
            record.scope,
            record.expires_at,
        )
        return record

    async def refresh(self, record: TokenRecord, *, now: datetime | None = None) -> TokenRecord:
        """Renew an expiring token. Requires a refresh token on the record."""
        if not record.refresh_token:
            raise ReauthorizationRequiredError(
                "Stored token has no refresh_token; re-run /auth/zendesk/login."
            )
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": record.refresh_token,
            "client_id": self.settings.zendesk_client_id,
            "client_secret": self.settings.zendesk_client_secret,
        }
        renewed = await self._post_token_request(payload, now=now)

        # Zendesk may omit refresh_token on renewal; keep the one we already hold
        # so a single non-rotating response does not strand us without a way back.
        if renewed.refresh_token is None:
            renewed = TokenRecord(
                access_token=renewed.access_token,
                token_type=renewed.token_type,
                scope=renewed.scope or record.scope,
                refresh_token=record.refresh_token,
                expires_at=renewed.expires_at,
                obtained_at=renewed.obtained_at,
            )
        self.store.save(renewed)
        logger.info("Zendesk token refreshed; expires_at=%s", renewed.expires_at)
        return renewed

    async def get_valid_access_token(self, *, now: datetime | None = None) -> str:
        """Return a usable access token, refreshing first if it is near expiry."""
        record = self.store.load()
        if record is None:
            raise NoStoredTokenError(
                "No Zendesk token stored. Visit /auth/zendesk/login to authorize."
            )
        now = now or utcnow()
        if not record.is_stale(
            leeway_seconds=self.settings.token_refresh_leeway_seconds, now=now
        ):
            return record.access_token

        if not record.refresh_token:
            raise ReauthorizationRequiredError(
                f"Access token expires at {record.expires_at} and no refresh token "
                "is available. Re-run /auth/zendesk/login."
            )
        renewed = await self.refresh(record, now=now)
        return renewed.access_token

    async def _post_token_request(
        self, payload: dict, *, now: datetime | None = None
    ) -> TokenRecord:
        client = self._http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
        try:
            response = await client.post(
                self.settings.token_url,
                json=payload,
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise TokenExchangeError(0, f"transport error: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise TokenExchangeError(response.status_code, response.text)
        try:
            body = response.json()
        except ValueError as exc:
            raise TokenExchangeError(
                response.status_code, f"non-JSON body: {response.text[:200]}"
            ) from exc
        if "access_token" not in body:
            raise TokenExchangeError(
                response.status_code, f"response missing access_token: {body}"
            )
        return TokenRecord.from_token_response(body, now=now)
