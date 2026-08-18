"""Parsing Zendesk webhook event payloads.

Treated as untrusted input even after the signature check passes: a valid
signature proves the payload came from our Zendesk account, not that it has the
shape we expect.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    TICKET_CREATED = "ticket.created"
    TICKET_UPDATED = "ticket.updated"
    TICKET_SOLVED = "ticket.solved"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, raw: str | None) -> EventType:
        if not raw:
            return cls.UNKNOWN
        # Zendesk sends dotted types like "zen:event-type:ticket.created".
        tail = raw.rsplit(":", 1)[-1]
        try:
            return cls(tail)
        except ValueError:
            return cls.UNKNOWN


class MalformedEventError(ValueError):
    """The payload was signed but is not a shape we can act on."""


@dataclass(frozen=True)
class WebhookEvent:
    type: EventType
    ticket_id: int
    raw_type: str

    @property
    def should_triage(self) -> bool:
        return self.type in (EventType.TICKET_CREATED, EventType.TICKET_UPDATED)

    @property
    def is_resolution(self) -> bool:
        return self.type is EventType.TICKET_SOLVED


def parse_event(payload: dict[str, Any]) -> WebhookEvent:
    """Pull the event type and ticket id out of a webhook body."""
    if not isinstance(payload, dict):
        raise MalformedEventError("webhook body was not a JSON object")

    raw_type = payload.get("type") or payload.get("event_type")
    detail = payload.get("detail") or payload.get("ticket") or {}
    if not isinstance(detail, dict):
        raise MalformedEventError("webhook 'detail' was not an object")

    raw_id = detail.get("id") or payload.get("ticket_id")
    if raw_id is None:
        raise MalformedEventError("webhook payload carried no ticket id")
    try:
        ticket_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise MalformedEventError(f"ticket id {raw_id!r} is not an integer") from exc

    if ticket_id <= 0:
        raise MalformedEventError(f"ticket id {ticket_id} is not positive")

    return WebhookEvent(
        type=EventType.parse(raw_type),
        ticket_id=ticket_id,
        raw_type=str(raw_type or ""),
    )
