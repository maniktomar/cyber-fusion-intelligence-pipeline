"""OAuth2 authorization-code endpoints for connecting a Zendesk account."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.auth.errors import TokenExchangeError
from app.auth.oauth import ZendeskOAuthClient
from app.auth.state import OAuthStateStore
from app.auth.token_store import CorruptTokenStoreError, EncryptedTokenStore, utcnow
from app.config import Settings
from app.dependencies import (
    get_app_settings,
    get_oauth_client,
    get_state_store,
    get_token_store,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/zendesk", tags=["auth"])


@router.get("/login")
async def login(
    oauth: ZendeskOAuthClient = Depends(get_oauth_client),
    states: OAuthStateStore = Depends(get_state_store),
) -> RedirectResponse:
    """Kick off consent: issue a CSRF state and redirect to Zendesk."""
    state = states.issue()
    # 307 keeps this a plain GET redirect and stops browsers from caching it.
    return RedirectResponse(oauth.build_authorize_url(state), status_code=307)


@router.get("/callback")
async def callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
    oauth: ZendeskOAuthClient = Depends(get_oauth_client),
    states: OAuthStateStore = Depends(get_state_store),
) -> dict:
    """Handle Zendesk's redirect back, exchanging the code for a token.

    The state check runs before anything else and before the code is used, so a
    forged callback never reaches the token endpoint.
    """
    if error:
        logger.warning("Zendesk denied authorization: %s (%s)", error, error_description)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": error, "error_description": error_description},
        )

    if not states.consume(state or ""):
        logger.warning("Rejected OAuth callback with unknown/expired/replayed state")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired OAuth state. Restart at /auth/zendesk/login.",
        )

    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Zendesk callback carried no authorization code.",
        )

    try:
        record = await oauth.exchange_code(code)
    except TokenExchangeError as exc:
        logger.error("Token exchange failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Zendesk rejected the token exchange ({exc.status_code}).",
        ) from exc

    # The token itself is never returned to the browser.
    return {
        "connected": True,
        "scope": record.scope,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "refreshable": record.refresh_token is not None,
    }


@router.get("/status")
async def auth_status(
    store: EncryptedTokenStore = Depends(get_token_store),
    settings: Settings = Depends(get_app_settings),
) -> dict:
    """Report whether a usable Zendesk token exists, without revealing it."""
    try:
        record = store.load()
    except CorruptTokenStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    if record is None:
        return {"connected": False, "reason": "no token stored"}

    return {
        "connected": True,
        "scope": record.scope,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "expires": record.expires,
        "refreshable": record.refresh_token is not None,
        "stale": record.is_stale(
            leeway_seconds=settings.token_refresh_leeway_seconds, now=utcnow()
        ),
    }
