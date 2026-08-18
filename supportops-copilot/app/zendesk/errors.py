"""Typed errors for the Zendesk API client.

Split along the lines the caller acts on: a 401 means re-authorise, a 404 means
the ticket is gone and retrying is pointless, a 429 means back off for a stated
number of seconds, and a 5xx means try again later.
"""

from __future__ import annotations


class ZendeskError(Exception):
    """Base class for every Zendesk API failure."""


class ZendeskAuthError(ZendeskError):
    """401/403 - the token is invalid, revoked, or lacks the required scope."""


class ZendeskNotFoundError(ZendeskError):
    """404 - the ticket does not exist. Retrying will not help."""


class ZendeskRateLimitError(ZendeskError):
    """429 - too many requests. `retry_after` is Zendesk's own stated delay."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"Zendesk rate limited the request; retry in {retry_after:.0f}s")


class ZendeskUnavailableError(ZendeskError):
    """5xx or a transport failure. Worth retrying."""


class ZendeskRequestError(ZendeskError):
    """4xx we did not expect - usually a malformed request on our side."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Zendesk returned {status_code}: {body[:300]}")
