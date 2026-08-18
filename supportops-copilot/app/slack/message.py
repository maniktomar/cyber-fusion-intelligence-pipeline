"""Slack Block Kit payload for a resolved ticket."""

from __future__ import annotations

from app.notifications.resolution import DraftUsage, ResolutionSummary

_USAGE_LABEL = {
    DraftUsage.USED: ":white_check_mark: AI draft used",
    DraftUsage.NOT_USED: ":pencil2: agent wrote their own reply",
    DraftUsage.NO_DRAFT_OFFERED: ":warning: no AI draft (flagged for manual triage)",
    DraftUsage.UNKNOWN: ":grey_question: no public reply to compare",
}


def _truncate(text: str, limit: int = 150) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_resolution_message(
    summary: ResolutionSummary, *, ticket_url: str | None = None
) -> dict:
    subject = _truncate(summary.subject) or "(no subject)"
    heading = (
        f"*<{ticket_url}|Ticket #{summary.ticket_id}>* resolved"
        if ticket_url
        else f"*Ticket #{summary.ticket_id}* resolved"
    )

    fields = [
        {"type": "mrkdwn", "text": f"*Category*\n{summary.category or 'not classified'}"},
        {"type": "mrkdwn", "text": f"*Resolution time*\n{summary.resolution_time_human}"},
        {"type": "mrkdwn", "text": f"*AI draft*\n{_USAGE_LABEL[summary.draft_usage]}"},
    ]
    if summary.similarity is not None:
        fields.append(
            {"type": "mrkdwn", "text": f"*Reply similarity*\n{summary.similarity:.0%}"}
        )

    return {
        # `text` is the notification fallback: without it, mobile push and
        # screen readers show an empty message even though blocks render fine.
        "text": f"Ticket #{summary.ticket_id} resolved - {subject}",
        "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"{heading}\n{subject}"}},
            {"type": "section", "fields": fields},
        ],
    }
