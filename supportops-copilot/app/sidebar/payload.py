"""What the agent-facing sidebar shows for a ticket.

Reconstructed from the ticket's own comments rather than a local database. The
machine-readable footer on each internal note carries the full decision, so
Zendesk stays the single source of truth and there is no second store to keep
in sync -- see `NOTE_DATA_MARKER` in `app.triage.decision`.

Everything here is defensive: an agent can edit or delete a note, a ticket can
be triaged twice, and an older note may carry a schema version this code does
not know. None of those may break the sidebar; all of them degrade to "nothing
to show yet".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum

from app.triage.decision import NOTE_SCHEMA_VERSION, decode_note_data
from app.zendesk.models import ZendeskComment, ZendeskTicket


class SidebarState(StrEnum):
    TRIAGED = "triaged"
    NEEDS_MANUAL_TRIAGE = "needs_manual_triage"
    NOT_TRIAGED = "not_triaged"
    UNREADABLE = "unreadable"


@dataclass
class SidebarPayload:
    """The complete sidebar view. Serialised straight to JSON for the app."""

    state: SidebarState
    ticket_id: int
    category: str | None = None
    urgency: str | None = None
    sentiment: str | None = None
    classification_confidence: float | None = None
    draft_confidence: float | None = None
    reasoning: str | None = None
    draft: str | None = None
    grounded_in: list[str] = field(default_factory=list)
    fallback_reason: str | None = None
    fallback_detail: str | None = None
    history_summary: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _latest_triage_note(comments: list[ZendeskComment]) -> dict | None:
    """The most recent decodable footer from a private comment.

    Newest-first so re-triaging a ticket shows the current decision, not the
    first one. Private-only because a footer in a public comment would mean an
    agent pasted our internal note to the customer -- worth not compounding.
    """
    for comment in reversed(comments):
        if comment.public:
            continue
        data = decode_note_data(comment.body)
        if data is not None:
            return data
    return None


def _history_summary(ticket: ZendeskTicket, comments: list[ZendeskComment]) -> str:
    public = sum(1 for c in comments if c.public)
    status = ticket.status or "unknown"
    if public == 0:
        return f"Status {status}; no public replies yet."
    plural = "reply" if public == 1 else "replies"
    return f"Status {status}; {public} public {plural} so far."


def build_sidebar_payload(
    ticket: ZendeskTicket, comments: list[ZendeskComment]
) -> SidebarPayload:
    history = _history_summary(ticket, comments)
    data = _latest_triage_note(comments)

    if data is None:
        return SidebarPayload(
            state=SidebarState.NOT_TRIAGED,
            ticket_id=ticket.id,
            history_summary=history,
        )

    version = data.get("v")
    if version != NOTE_SCHEMA_VERSION:
        # A note written by a newer build. Showing half-understood fields is
        # worse than showing none, so say so plainly and let the agent read the
        # note itself.
        return SidebarPayload(
            state=SidebarState.UNREADABLE,
            ticket_id=ticket.id,
            fallback_detail=(
                f"This ticket was triaged by a different version of the copilot "
                f"(note format v{version}, this app reads v{NOTE_SCHEMA_VERSION}). "
                "Open the internal note to read it."
            ),
            history_summary=history,
        )

    if data.get("action") == "flag_only":
        return SidebarPayload(
            state=SidebarState.NEEDS_MANUAL_TRIAGE,
            ticket_id=ticket.id,
            fallback_reason=data.get("reason"),
            fallback_detail=data.get("detail"),
            history_summary=history,
        )

    return SidebarPayload(
        state=SidebarState.TRIAGED,
        ticket_id=ticket.id,
        category=data.get("category"),
        urgency=data.get("urgency"),
        sentiment=data.get("sentiment"),
        classification_confidence=data.get("classification_confidence"),
        draft_confidence=data.get("draft_confidence"),
        reasoning=data.get("reasoning"),
        draft=data.get("draft"),
        grounded_in=list(data.get("grounded_in") or []),
        history_summary=history,
    )
