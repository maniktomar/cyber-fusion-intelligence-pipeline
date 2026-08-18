"""The confidence gate: the part of this system that must never fail silently.

Everything upstream is best-effort. This module turns whatever happened into
exactly one of two outcomes, and there is no third path where nothing happens
and nothing is recorded:

  APPLY      - tag the ticket and attach the draft as an internal note
  FLAG_ONLY  - tag the ticket `ai-needs-manual-triage` and change nothing else

A draft is never sent to a customer. The best case is a note an agent reads.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from app.llm.schemas import Classification, Draft

logger = logging.getLogger(__name__)

MANUAL_TRIAGE_TAG = "ai-needs-manual-triage"
AI_TRIAGED_TAG = "ai-triaged"

# Marker for the machine-readable footer appended to every internal note.
#
# The alternative was a second datastore keyed by ticket id. This avoids it:
# Zendesk stays the single source of truth, there is nothing to keep in sync,
# and a ticket exported or migrated carries its own triage history. The cost is
# one line of JSON an agent can see at the bottom of the note -- a fair trade
# for not owning a database.
NOTE_DATA_MARKER = "[supportops-data]"
NOTE_SCHEMA_VERSION = 1


class Action(StrEnum):
    APPLY = "apply"
    FLAG_ONLY = "flag_only"


class FallbackReason(StrEnum):
    """Why we declined to act. Recorded on the ticket so it is never a mystery."""

    EMPTY_TICKET = "empty_ticket"
    LOW_CONFIDENCE_CLASSIFICATION = "low_confidence_classification"
    LOW_CONFIDENCE_DRAFT = "low_confidence_draft"
    LLM_UNAVAILABLE = "llm_unavailable"
    CIRCUIT_OPEN = "circuit_open"
    MALFORMED_RESPONSE = "malformed_response"
    MODEL_REFUSED = "model_refused"
    DRAFT_REJECTED = "draft_rejected"
    UNGROUNDED_DRAFT = "ungrounded_draft"


_HUMAN_READABLE: dict[FallbackReason, str] = {
    FallbackReason.EMPTY_TICKET: "the ticket had no usable text to read",
    FallbackReason.LOW_CONFIDENCE_CLASSIFICATION: (
        "the model was not confident enough about the category/urgency"
    ),
    FallbackReason.LOW_CONFIDENCE_DRAFT: (
        "the model was not confident enough in its suggested reply"
    ),
    FallbackReason.LLM_UNAVAILABLE: "the AI service could not be reached",
    FallbackReason.CIRCUIT_OPEN: (
        "the AI service is failing repeatedly and calls are paused"
    ),
    FallbackReason.MALFORMED_RESPONSE: "the AI returned a response we could not parse",
    FallbackReason.MODEL_REFUSED: "the AI declined to process this ticket",
    FallbackReason.DRAFT_REJECTED: "the suggested reply failed a safety check",
    FallbackReason.UNGROUNDED_DRAFT: (
        "the suggested reply was not grounded in any knowledge base article"
    ),
}


@dataclass(frozen=True)
class TriageDecision:
    """The single, always-produced output of triage."""

    action: Action
    tags: list[str]
    internal_note: str
    reason: FallbackReason | None = None
    detail: str | None = None
    classification: Classification | None = None
    draft: Draft | None = None

    @property
    def is_fallback(self) -> bool:
        return self.action is Action.FLAG_ONLY


@dataclass(frozen=True)
class Thresholds:
    """Confidence floors. See README "Design Decisions" for how these were set."""

    classification: float = 0.75
    draft: float = 0.70
    require_grounding: bool = True


# Text that must never reach an agent as a "ready" draft. Unresolved template
# placeholders are the giveaway that the model produced a shape, not an answer.
_PLACEHOLDER_PATTERNS = (
    re.compile(r"\[[A-Z_ ]{2,}\]"),
    re.compile(r"\{\{.*?\}\}"),
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bXXX+\b"),
    re.compile(r"\binsert .{0,20}here\b", re.IGNORECASE),
)

# Commitments a support agent is not authorised to make on the company's behalf.
# A draft containing one of these is not "slightly wrong" -- it is a liability,
# so it fails closed rather than being softened.
_UNAUTHORISED_COMMITMENTS = (
    re.compile(r"\bwe guarantee\b", re.IGNORECASE),
    re.compile(r"\b(full|complete) refund (has been|is being) (issued|processed)\b", re.IGNORECASE),
    re.compile(r"\bi have (issued|processed) (a|your) refund\b", re.IGNORECASE),
    re.compile(r"\bthis will (definitely|certainly) be (fixed|resolved) by\b", re.IGNORECASE),
    re.compile(r"\bwe will never\b", re.IGNORECASE),
)

MIN_DRAFT_CHARS = 40
MAX_DRAFT_CHARS = 4000


@dataclass
class DraftRejection:
    reason: FallbackReason
    detail: str


def validate_draft(draft: Draft) -> DraftRejection | None:
    """Content checks a draft must pass before an agent ever sees it."""
    body = draft.body.strip()

    if len(body) < MIN_DRAFT_CHARS:
        return DraftRejection(
            FallbackReason.DRAFT_REJECTED,
            f"draft is only {len(body)} characters; too short to be a real reply",
        )
    if len(body) > MAX_DRAFT_CHARS:
        return DraftRejection(
            FallbackReason.DRAFT_REJECTED,
            f"draft is {len(body)} characters; over the {MAX_DRAFT_CHARS} limit",
        )
    # The matched text is logged for engineers but deliberately kept out of the
    # rejection detail: that detail is rendered into an internal note on the
    # ticket, and quoting a fragment of a draft we just judged unsafe puts that
    # fragment in front of an agent who may skim it as a suggestion.
    for pattern in _PLACEHOLDER_PATTERNS:
        match = pattern.search(body)
        if match:
            logger.warning("Draft rejected: placeholder %r", match.group(0))
            return DraftRejection(
                FallbackReason.DRAFT_REJECTED,
                "the draft contained an unresolved template placeholder",
            )
    for pattern in _UNAUTHORISED_COMMITMENTS:
        match = pattern.search(body)
        if match:
            logger.warning("Draft rejected: unauthorised commitment %r", match.group(0))
            return DraftRejection(
                FallbackReason.DRAFT_REJECTED,
                "the draft made a commitment support is not authorised to make",
            )
    return None


def flag_only(reason: FallbackReason, detail: str | None = None) -> TriageDecision:
    """Build the fallback decision. Loud on the ticket, never silent."""
    explanation = _HUMAN_READABLE[reason]
    note_lines = [
        "AI: could not process, needs manual triage.",
        f"Reason: {explanation}.",
    ]
    if detail:
        note_lines.append(f"Detail: {detail}")
    note_lines.append(
        "No classification or draft has been applied. This ticket is untouched "
        "apart from this note and its tag."
    )
    note = "\n".join(note_lines) + "\n\n" + _encode_note_data(
        {"action": Action.FLAG_ONLY.value, "reason": reason.value, "detail": detail}
    )
    return TriageDecision(
        action=Action.FLAG_ONLY,
        tags=[MANUAL_TRIAGE_TAG],
        internal_note=note,
        reason=reason,
        detail=detail,
    )


def apply_decision(classification: Classification, draft: Draft) -> TriageDecision:
    """Build the success decision: tags plus a draft staged for agent review."""
    tags = [
        AI_TRIAGED_TAG,
        f"ai-category-{classification.category.value}",
        f"ai-urgency-{classification.urgency.value}",
        f"ai-sentiment-{classification.sentiment.value}",
    ]
    grounding = ", ".join(draft.grounded_in) if draft.grounded_in else "none"
    note = "\n".join(
        [
            "AI triage (review before sending -- nothing has been sent to the customer).",
            f"Category: {classification.category.value} | "
            f"Urgency: {classification.urgency.value} | "
            f"Sentiment: {classification.sentiment.value}",
            f"Classification confidence: {classification.confidence:.2f} | "
            f"Draft confidence: {draft.confidence:.2f}",
            f"Grounded in: {grounding}",
            f"Rationale: {classification.reasoning}",
            "",
            "--- Suggested reply ---",
            draft.body.strip(),
        ]
    )
    note += "\n\n" + _encode_note_data(
        {
            "action": Action.APPLY.value,
            "category": classification.category.value,
            "urgency": classification.urgency.value,
            "sentiment": classification.sentiment.value,
            "classification_confidence": classification.confidence,
            "draft_confidence": draft.confidence,
            "grounded_in": draft.grounded_in,
            "reasoning": classification.reasoning,
            "draft": draft.body.strip(),
        }
    )
    return TriageDecision(
        action=Action.APPLY,
        tags=tags,
        internal_note=note,
        classification=classification,
        draft=draft,
    )


def decide(
    classification: Classification,
    draft: Draft,
    thresholds: Thresholds,
) -> TriageDecision:
    """Gate a successful model round-trip against the confidence thresholds.

    Checks run cheapest-and-most-decisive first, and the first failure wins --
    reporting one clear reason beats reporting a list an agent has to read.
    """
    if classification.confidence < thresholds.classification:
        return flag_only(
            FallbackReason.LOW_CONFIDENCE_CLASSIFICATION,
            f"classification confidence {classification.confidence:.2f} "
            f"is below the {thresholds.classification:.2f} threshold",
        )

    if draft.confidence < thresholds.draft:
        return flag_only(
            FallbackReason.LOW_CONFIDENCE_DRAFT,
            f"draft confidence {draft.confidence:.2f} "
            f"is below the {thresholds.draft:.2f} threshold",
        )

    if thresholds.require_grounding and not draft.grounded_in:
        return flag_only(
            FallbackReason.UNGROUNDED_DRAFT,
            "the model cited no knowledge base article for this reply",
        )

    rejection = validate_draft(draft)
    if rejection is not None:
        return flag_only(rejection.reason, rejection.detail)

    return apply_decision(classification, draft)


def _encode_note_data(payload: dict) -> str:
    """One-line JSON footer so the sidebar can read a decision back exactly.

    Sorted keys on a single line: the sidebar finds it by scanning for the
    marker, and a stable key order keeps note diffs readable when the same
    ticket is triaged more than once.
    """
    body = json.dumps(
        {"v": NOTE_SCHEMA_VERSION, **payload}, sort_keys=True, separators=(",", ":")
    )
    return f"{NOTE_DATA_MARKER} {body}"


def decode_note_data(note: str) -> dict | None:
    """Read the footer back. None if absent or unparseable.

    Tolerant by design: agents edit notes, and a corrupted footer must degrade
    the sidebar to "no data" rather than break it.
    """
    index = note.rfind(NOTE_DATA_MARKER)
    if index == -1:
        return None
    remainder = note[index + len(NOTE_DATA_MARKER) :].strip().splitlines()
    if not remainder:
        return None
    try:
        payload = json.loads(remainder[0])
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
