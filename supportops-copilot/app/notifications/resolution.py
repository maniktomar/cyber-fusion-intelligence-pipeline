"""Building the summary posted to Slack when a ticket is solved.

The interesting field is `draft_usage`. "Did an agent actually use what the AI
wrote?" is the only metric that says whether this tool earns its place, and it
is not something Zendesk records -- so it has to be inferred by comparing the
agent's public reply against the draft we staged in the internal note.

That inference is a heuristic and is labelled as one: `UNKNOWN` is a first-class
outcome rather than a coerced guess, because a metric that quietly reports
false confidence is worse than one that admits it does not know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.zendesk.models import ZendeskComment, ZendeskTicket

DRAFT_MARKER = "--- Suggested reply ---"

# Above this Jaccard overlap we call the draft used. Set high on purpose: two
# replies to the same billing question share a lot of vocabulary by nature, so
# a low bar would report "used" for an agent who ignored the draft entirely and
# happened to write about the same subject.
SIMILARITY_THRESHOLD = 0.6

_WORD_RE = re.compile(r"[a-z0-9']+")


class DraftUsage(StrEnum):
    USED = "used"
    NOT_USED = "not_used"
    NO_DRAFT_OFFERED = "no_draft_offered"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ResolutionSummary:
    ticket_id: int
    subject: str
    category: str | None
    resolution_time: timedelta | None
    draft_usage: DraftUsage
    similarity: float | None = None

    @property
    def resolution_time_human(self) -> str:
        if self.resolution_time is None:
            return "unknown"
        total = int(self.resolution_time.total_seconds())
        if total < 0:
            return "unknown"
        hours, remainder = divmod(total, 3600)
        minutes = remainder // 60
        if hours >= 24:
            days, hours = divmod(hours, 24)
            return f"{days}d {hours}h"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def similarity(a: str, b: str) -> float:
    """Jaccard overlap of the two word sets. 0.0 when either side is empty."""
    words_a, words_b = _words(a), _words(b)
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def extract_draft(comments: list[ZendeskComment]) -> str | None:
    """Pull the suggested reply out of the most recent AI internal note."""
    for comment in reversed(comments):
        if comment.public or DRAFT_MARKER not in comment.body:
            continue
        _, _, draft = comment.body.partition(DRAFT_MARKER)
        cleaned = draft.strip()
        return cleaned or None
    return None


def classify_draft_usage(
    comments: list[ZendeskComment], *, threshold: float = SIMILARITY_THRESHOLD
) -> tuple[DraftUsage, float | None]:
    """Compare the staged draft against the agent's public replies."""
    draft = extract_draft(comments)
    if draft is None:
        return DraftUsage.NO_DRAFT_OFFERED, None

    public_replies = [c.body for c in comments if c.public and c.body.strip()]
    if not public_replies:
        # A solved ticket with no public reply is a real thing (duplicates,
        # spam, resolved by phone). Reporting NOT_USED here would count them as
        # rejections of the draft and quietly deflate the metric.
        return DraftUsage.UNKNOWN, None

    best = max(similarity(draft, reply) for reply in public_replies)
    usage = DraftUsage.USED if best >= threshold else DraftUsage.NOT_USED
    return usage, round(best, 3)


def build_summary(
    ticket: ZendeskTicket,
    comments: list[ZendeskComment],
    *,
    solved_at: datetime | None = None,
) -> ResolutionSummary:
    usage, score = classify_draft_usage(comments)
    solved = solved_at or ticket.updated_at
    elapsed = (
        solved - ticket.created_at
        if solved is not None and ticket.created_at is not None
        else None
    )
    return ResolutionSummary(
        ticket_id=ticket.id,
        subject=ticket.subject,
        category=ticket.ai_category,
        resolution_time=elapsed,
        draft_usage=usage,
        similarity=score,
    )
