"""Runs a triage end to end for one webhook event.

This is the only place that touches all three subsystems, and it runs in a
background task after the webhook has already been acknowledged. That means
nothing here can surface an error to a caller -- so it swallows nothing
silently, and logs every exit.
"""

from __future__ import annotations

import logging

from app.notifications.resolution import build_summary
from app.slack.client import SlackNotifier
from app.slack.message import build_resolution_message
from app.triage.decision import FallbackReason, flag_only
from app.triage.service import Ticket, TriageService
from app.webhooks.events import WebhookEvent
from app.zendesk.client import ZendeskClient
from app.zendesk.errors import ZendeskError, ZendeskNotFoundError

logger = logging.getLogger(__name__)


class TicketProcessor:
    def __init__(
        self,
        zendesk: ZendeskClient,
        triage: TriageService,
        slack: SlackNotifier | None = None,
        *,
        ticket_url_template: str | None = None,
    ) -> None:
        self.zendesk = zendesk
        self.triage = triage
        self.slack = slack
        self.ticket_url_template = ticket_url_template

    async def process(self, event: WebhookEvent) -> None:
        """Fetch, triage, and write back. Never raises."""
        ticket_id = event.ticket_id
        try:
            ticket = await self.zendesk.get_ticket(ticket_id)
        except ZendeskNotFoundError:
            logger.warning("Ticket %s no longer exists; nothing to triage.", ticket_id)
            return
        except ZendeskError:
            logger.exception("Ticket %s: could not be fetched; giving up.", ticket_id)
            return
        except Exception:
            # Backstop. A background task that raises loses the ticket with
            # nothing but a traceback, which is the exact failure mode this
            # project exists to prevent.
            logger.exception(
                "Ticket %s: unexpected error fetching the ticket; giving up.", ticket_id
            )
            return

        if ticket.already_triaged:
            # Our own write fires ticket.updated. Without this the service
            # re-triages every ticket it touches, forever.
            logger.info("Ticket %s already carries an ai-* tag; skipping.", ticket_id)
            return

        try:
            decision = self.triage.triage(
                Ticket(id=ticket.id, subject=ticket.subject, body=ticket.description)
            )
        except Exception as exc:
            # TriageService is written to swallow its own failures, so reaching
            # here means a bug in our code rather than a model or network
            # problem. Belt and braces: a bug must still leave the ticket
            # flagged rather than silently unprocessed.
            logger.exception("Ticket %s: triage itself raised.", ticket_id)
            decision = flag_only(
                FallbackReason.LLM_UNAVAILABLE,
                f"internal error during triage: {type(exc).__name__}",
            )

        try:
            await self.zendesk.apply(ticket_id, decision)
        except Exception:
            # The triage itself succeeded; we just could not record it. Log with
            # a traceback -- this is the one failure mode where the ticket is
            # genuinely left untouched and nobody is told, so it must be loud in
            # the only place left to be loud.
            logger.exception(
                "Ticket %s: triage produced '%s' but writing it back failed.",
                ticket_id,
                decision.action.value,
            )

    async def flag_unprocessable(self, ticket_id: int, detail: str) -> None:
        """Mark a ticket we could not even attempt, so it is not lost."""
        decision = flag_only(FallbackReason.LLM_UNAVAILABLE, detail)
        try:
            await self.zendesk.apply(ticket_id, decision)
        except Exception:
            logger.exception("Ticket %s: could not flag as unprocessable.", ticket_id)

    async def notify_resolution(self, event: WebhookEvent) -> None:
        """Post a resolution summary to Slack. Never raises, never blocks triage.

        Runs as its own background task, so a Slack outage cannot delay or fail
        anything on the ticket-processing path -- there is no shared state
        between this method and `process`.
        """
        ticket_id = event.ticket_id
        if self.slack is None or not self.slack.configured:
            logger.debug("Slack not configured; skipping ticket %s summary.", ticket_id)
            return

        try:
            ticket = await self.zendesk.get_ticket(ticket_id)
            comments = await self.zendesk.get_comments(ticket_id)
        except Exception:
            logger.warning(
                "Ticket %s: could not gather the resolution summary.",
                ticket_id,
                exc_info=True,
            )
            return

        try:
            summary = build_summary(ticket, comments)
            url = (
                self.ticket_url_template.format(ticket_id=ticket_id)
                if self.ticket_url_template
                else None
            )
            payload = build_resolution_message(summary, ticket_url=url)
        except Exception:
            logger.warning(
                "Ticket %s: could not build the resolution summary.",
                ticket_id,
                exc_info=True,
            )
            return

        try:
            sent = await self.slack.notify(payload)
        except Exception:
            # SlackNotifier is written never to raise, but relying on another
            # component's promise is what produced the OAuth-seam bug in the
            # engineering journal. Enforce it at the boundary instead.
            logger.warning(
                "Ticket %s: Slack notifier raised unexpectedly.",
                ticket_id,
                exc_info=True,
            )
            return

        logger.info(
            "Ticket %s resolution summary %s (draft: %s)",
            ticket_id,
            "posted to Slack" if sent else "not posted",
            summary.draft_usage.value,
        )
