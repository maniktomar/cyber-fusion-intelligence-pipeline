"""A thin, typed wrapper around the handful of Zendesk endpoints we use.

Everything goes through `_request`, so retries, error mapping, and token
refresh live in exactly one place rather than being re-derived at each call
site. The public methods are deliberately boring.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

from app.auth.errors import ZendeskOAuthError
from app.auth.oauth import ZendeskOAuthClient
from app.triage.decision import TriageDecision
from app.zendesk.errors import (
    ZendeskAuthError,
    ZendeskNotFoundError,
    ZendeskRateLimitError,
    ZendeskRequestError,
    ZendeskUnavailableError,
)
from app.zendesk.models import ZendeskComment, ZendeskTicket

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)

# Zendesk's own rate limit is per-minute, so a long sleep is sometimes correct --
# but not inside a request handler. Anything longer than this gives up and lets
# the caller decide.
MAX_RETRY_SLEEP_SECONDS = 20.0


class ZendeskClient:
    def __init__(
        self,
        oauth: ZendeskOAuthClient,
        *,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
        sleep=asyncio.sleep,
    ) -> None:
        self.oauth = oauth
        self.base_url = oauth.settings.zendesk_base_url
        self._http_client = http_client
        self.max_retries = max_retries
        self._sleep = sleep

    async def get_ticket(self, ticket_id: int) -> ZendeskTicket:
        payload = await self._request("GET", f"/api/v2/tickets/{ticket_id}.json")
        return ZendeskTicket.from_api(payload["ticket"])

    async def get_comments(self, ticket_id: int) -> list[ZendeskComment]:
        """All comments on a ticket, oldest first, public and private."""
        payload = await self._request("GET", f"/api/v2/tickets/{ticket_id}/comments.json")
        return [ZendeskComment.from_api(c) for c in payload.get("comments", [])]

    async def add_tags(self, ticket_id: int, tags: list[str]) -> list[str]:
        """Additive: Zendesk's PUT /tags merges rather than replacing."""
        if not tags:
            return []
        payload = await self._request(
            "PUT", f"/api/v2/tickets/{ticket_id}/tags.json", json={"tags": tags}
        )
        return list(payload.get("tags", []))

    async def add_internal_note(self, ticket_id: int, body: str) -> None:
        """Add a private comment.

        `public: False` is the single most important field in this file. A true
        here would send the AI's draft straight to the customer, which is the
        one outcome the whole project is built to prevent.
        """
        await self._request(
            "PUT",
            f"/api/v2/tickets/{ticket_id}.json",
            json={"ticket": {"comment": {"body": body, "public": False}}},
        )

    async def apply(self, ticket_id: int, decision: TriageDecision) -> None:
        """Write a triage decision back to the ticket.

        Note ordering: the note goes on first, then the tags. If the second call
        fails we would rather have an explained ticket with no tags than a
        tagged ticket with no explanation -- a tag with nothing behind it is the
        silent failure this project exists to avoid.
        """
        await self.add_internal_note(ticket_id, decision.internal_note)
        await self.add_tags(ticket_id, decision.tags)
        logger.info(
            "Ticket %s updated: action=%s tags=%s",
            ticket_id,
            decision.action.value,
            ",".join(decision.tags),
        )

    async def _request(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        client = self._http_client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)

        try:
            return await self._request_with_retries(client, method, path, json)
        finally:
            if owns_client:
                await client.aclose()

    async def _request_with_retries(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        json: dict[str, Any] | None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                token = await self.oauth.get_valid_access_token()
            except ZendeskOAuthError as exc:
                # No token, or a token we cannot renew. Presenting this as an
                # OAuth error would leak a second error taxonomy to every
                # caller of this client; they all treat it the same way.
                raise ZendeskAuthError(f"Cannot authenticate to Zendesk: {exc}") from exc
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
            try:
                response = await client.request(method, url, json=json, headers=headers)
            except httpx.HTTPError as exc:
                last_error = ZendeskUnavailableError(f"transport error: {exc}")
                await self._backoff(attempt)
                continue

            if response.status_code < 300:
                return self._parse_json(response)

            error = self._error_for(response)
            if not self._is_retryable(error):
                raise error

            last_error = error
            delay = (
                error.retry_after
                if isinstance(error, ZendeskRateLimitError)
                else self._backoff_seconds(attempt)
            )
            if delay > MAX_RETRY_SLEEP_SECONDS:
                raise error
            logger.warning(
                "Zendesk %s %s failed (%s); retrying in %.1fs",
                method,
                path,
                type(error).__name__,
                delay,
            )
            await self._sleep(delay)

        assert last_error is not None
        raise last_error

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict[str, Any]:
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise ZendeskUnavailableError(
                f"Zendesk returned non-JSON body: {response.text[:200]}"
            ) from exc

    @staticmethod
    def _error_for(response: httpx.Response) -> Exception:
        status = response.status_code
        if status in (401, 403):
            return ZendeskAuthError(
                f"Zendesk rejected the token ({status}). Re-run /auth/zendesk/login."
            )
        if status == 404:
            return ZendeskNotFoundError(f"Zendesk resource not found: {response.url}")
        if status == 429:
            raw = response.headers.get("Retry-After", "60")
            try:
                retry_after = float(raw)
            except ValueError:
                retry_after = 60.0
            return ZendeskRateLimitError(retry_after)
        if status >= 500:
            return ZendeskUnavailableError(f"Zendesk returned {status}")
        return ZendeskRequestError(status, response.text)

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        return isinstance(error, ZendeskRateLimitError | ZendeskUnavailableError)

    @staticmethod
    def _backoff_seconds(attempt: int) -> float:
        # Full jitter: without it, a burst of tickets that all hit a 503 retries
        # in lockstep and re-creates the spike that caused it.
        return random.uniform(0, min(8.0, 0.5 * (2**attempt)))

    async def _backoff(self, attempt: int) -> None:
        await self._sleep(self._backoff_seconds(attempt))
