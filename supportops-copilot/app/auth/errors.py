"""Typed errors for the OAuth layer.

Callers need to distinguish "Zendesk said no" from "we have no token yet" from
"the token is dead and only a human can fix it", because those three cases have
different recovery paths.
"""

from __future__ import annotations


class ZendeskOAuthError(Exception):
    """Base class for every OAuth failure raised by this package."""


class TokenExchangeError(ZendeskOAuthError):
    """Zendesk rejected an authorization-code or refresh-token exchange."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Zendesk token endpoint returned {status_code}: {body[:500]}")


class NoStoredTokenError(ZendeskOAuthError):
    """Nothing has completed the authorization-code flow yet."""


class ReauthorizationRequiredError(ZendeskOAuthError):
    """The stored token cannot be renewed without a human re-running consent.

    Raised when an access token is at or near expiry and no refresh token is
    available to renew it.
    """


class InvalidStateError(ZendeskOAuthError):
    """The `state` returned to the callback was unknown, expired, or replayed."""
