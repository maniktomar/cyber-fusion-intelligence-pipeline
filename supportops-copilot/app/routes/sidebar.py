"""The endpoint the Zendesk sidebar app calls.

Read-only by design. The sidebar shows what triage decided; it never triggers a
triage, and it has no path that writes to a ticket. Keeping it read-only means
the app can be installed for every agent without widening what the integration
can do.

On authentication: the app sends a signed JWT that Zendesk mints for the
installing account, and the backend verifies it against the app's shared secret
before answering. That check is implemented in `app/sidebar/auth.py` and is the
part that most needs verifying against a live Zendesk instance -- see the README
"Known limitations".
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config import Settings
from app.dependencies import get_app_settings, get_zendesk_client
from app.sidebar.auth import extract_bearer, verify_sidebar_token
from app.sidebar.payload import SidebarPayload, SidebarState, build_sidebar_payload
from app.zendesk.client import ZendeskClient
from app.zendesk.errors import ZendeskAuthError, ZendeskError, ZendeskNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sidebar", tags=["sidebar"])


@router.get("/tickets/{ticket_id}/triage")
async def get_triage_state(
    ticket_id: int,
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_app_settings),
    zendesk: ZendeskClient = Depends(get_zendesk_client),
) -> dict:
    """Everything the sidebar needs for one ticket, in one round trip."""
    token_result = verify_sidebar_token(
        extract_bearer(authorization),
        secret=settings.zendesk_app_secret,
        expected_issuer=settings.zendesk_app_issuer or None,
    )
    if not token_result:
        logger.warning("Sidebar request rejected: %s", token_result.failure.value)
        # One opaque 401 for every reason, as with the webhook.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sidebar request could not be authenticated.",
        )

    if ticket_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ticket id."
        )

    try:
        ticket = await zendesk.get_ticket(ticket_id)
        comments = await zendesk.get_comments(ticket_id)
    except ZendeskNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found."
        ) from exc
    except ZendeskAuthError as exc:
        # 502, not 401: the sidebar's own caller is authenticated fine. It is
        # *our* Zendesk credential that is broken, and saying 401 would send an
        # agent hunting for a problem with their own session.
        logger.error("Sidebar: Zendesk rejected our token for ticket %s.", ticket_id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The copilot's Zendesk connection needs re-authorising.",
        ) from exc
    except ZendeskError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Zendesk.",
        ) from exc

    try:
        payload = build_sidebar_payload(ticket, comments)
    except Exception:
        # A malformed note must not blank the sidebar with a 500. Degrade to
        # "cannot read this" and let the agent open the note themselves.
        logger.exception("Sidebar: could not build payload for ticket %s.", ticket_id)
        payload = SidebarPayload(
            state=SidebarState.UNREADABLE,
            ticket_id=ticket_id,
            fallback_detail="Could not read this ticket's triage note.",
        )

    return payload.to_dict()
