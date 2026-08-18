"""Just enough of the Zendesk ticket shape to triage one."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def parse_zendesk_timestamp(raw: str | None) -> datetime | None:
    """Parse a Zendesk ISO-8601 timestamp into an aware UTC datetime.

    Zendesk sends UTC with a trailing `Z`, which `fromisoformat` did not accept
    before Python 3.11. Anything without a zone is *assumed* UTC rather than
    silently inheriting the host's local time -- a naive datetime here would
    make resolution times wrong by the server's offset, and wrong in a way that
    looks plausible.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class ZendeskComment:
    id: int
    body: str
    public: bool
    author_id: int | None
    created_at: datetime | None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ZendeskComment:
        return cls(
            id=int(payload.get("id") or 0),
            body=payload.get("body") or "",
            public=bool(payload.get("public", False)),
            author_id=payload.get("author_id"),
            created_at=parse_zendesk_timestamp(payload.get("created_at")),
        )


@dataclass(frozen=True)
class ZendeskTicket:
    id: int
    subject: str
    description: str
    status: str
    priority: str | None
    tags: list[str]
    requester_id: int | None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> ZendeskTicket:
        """Parse a ticket object from a `/api/v2/tickets/{id}.json` response.

        Tolerant on purpose: Zendesk omits null fields rather than sending them,
        and a missing `priority` on an unprioritised ticket must not be an error.
        """
        try:
            ticket_id = int(payload["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Ticket payload has no usable id: {payload!r}") from exc

        return cls(
            id=ticket_id,
            subject=payload.get("subject") or "",
            description=payload.get("description") or "",
            status=payload.get("status") or "new",
            priority=payload.get("priority"),
            tags=list(payload.get("tags") or []),
            requester_id=payload.get("requester_id"),
            created_at=parse_zendesk_timestamp(payload.get("created_at")),
            updated_at=parse_zendesk_timestamp(payload.get("updated_at")),
        )

    @property
    def already_triaged(self) -> bool:
        """True if a previous run already left its mark on this ticket.

        Guards against a webhook loop: our own update fires ticket.updated,
        which would otherwise re-triage the ticket we just triaged, forever.
        """
        return any(t.startswith("ai-") for t in self.tags)

    @property
    def ai_category(self) -> str | None:
        """The category our own triage assigned, read back off the tags."""
        prefix = "ai-category-"
        return next(
            (t[len(prefix) :] for t in self.tags if t.startswith(prefix)), None
        )

    @property
    def was_flagged_for_manual_triage(self) -> bool:
        return "ai-needs-manual-triage" in self.tags
