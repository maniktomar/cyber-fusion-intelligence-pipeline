"""The Zendesk webhook endpoint.

Two things shape this handler:

1. **Verify before parsing.** The signature is checked against the raw bytes
   before the JSON is touched, so a forged payload never reaches any parsing
   code, let alone the triage engine.

2. **Acknowledge fast, work later.** Zendesk gives a webhook roughly ten
   seconds before it records a delivery failure and retries. Triage makes two
   LLM calls and can take considerably longer than that. Doing the work inline
   would mean Zendesk retrying a ticket we are still processing -- duplicate
   triage on every slow ticket. So the handler validates, queues, and returns
   202 immediately.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status

from app.config import Settings
from app.dependencies import get_app_settings, get_ticket_processor
from app.webhooks.events import MalformedEventError, parse_event
from app.webhooks.processor import TicketProcessor
from app.webhooks.signature import verify_webhook

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/zendesk", status_code=status.HTTP_202_ACCEPTED)
async def zendesk_webhook(
    request: Request,
    background: BackgroundTasks,
    x_zendesk_webhook_signature: str | None = Header(default=None),
    x_zendesk_webhook_signature_timestamp: str | None = Header(default=None),
    settings: Settings = Depends(get_app_settings),
    processor: TicketProcessor = Depends(get_ticket_processor),
) -> dict:
    raw_body = await request.body()

    result = verify_webhook(
        secret=settings.zendesk_webhook_secret,
        body=raw_body,
        signature=x_zendesk_webhook_signature,
        timestamp=x_zendesk_webhook_signature_timestamp,
        tolerance_seconds=settings.webhook_tolerance_seconds,
    )
    if not result:
        logger.warning("Rejected webhook: %s", result.failure.value)
        # A single opaque 401 for every rejection reason. Telling an attacker
        # whether the signature or the timestamp was wrong is free information.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook signature verification failed.",
        )

    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook body was not valid JSON.",
        ) from exc

    try:
        event = parse_event(payload)
    except MalformedEventError as exc:
        logger.warning("Signed webhook had an unusable payload: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if event.is_resolution:
        # Queued as its own task, separate from triage: a Slack outage must not
        # touch anything on the ticket-processing path.
        background.add_task(processor.notify_resolution, event)
        return {"accepted": True, "queued": True, "event": event.type.value}

    if not event.should_triage:
        # Acknowledging keeps Zendesk from recording a delivery failure and
        # retrying an event we were never going to act on.
        logger.info(
            "Webhook %s for ticket %s acknowledged; no action.",
            event.raw_type or event.type.value,
            event.ticket_id,
        )
        return {"accepted": True, "queued": False, "event": event.type.value}

    background.add_task(processor.process, event)
    return {"accepted": True, "queued": True, "ticket_id": event.ticket_id}
