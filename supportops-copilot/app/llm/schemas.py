"""Structured output schemas for the triage LLM calls.

These are the contract with the model. Using JSON-schema-constrained output
instead of parsing free text means "the model rambled" becomes a validation
error the fallback layer can act on, rather than a regex that quietly extracts
the wrong category.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Category(StrEnum):
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT_ACCESS = "account_access"
    FEATURE_REQUEST = "feature_request"
    SHIPPING = "shipping"
    OTHER = "other"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class Sentiment(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    FRUSTRATED = "frustrated"
    ANGRY = "angry"


class Classification(BaseModel):
    """The model's read of a ticket, with its own confidence in that read."""

    category: Category
    urgency: Urgency
    sentiment: Sentiment
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How confident you are in this classification. Be honest and "
            "well-calibrated: a ticket that is genuinely ambiguous should score "
            "below 0.7, and one that could plausibly belong to two categories "
            "should score below 0.5. Overconfidence is worse than abstaining."
        ),
    )
    reasoning: str = Field(
        max_length=600,
        description="One or two sentences justifying the classification.",
    )


class Draft(BaseModel):
    """A suggested reply, grounded in the knowledge base."""

    body: str = Field(
        description=(
            "The suggested reply to the customer. Plain text. Do not invent "
            "policy, refund amounts, dates, or account details that are not "
            "present in the ticket or the knowledge base."
        )
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How confident you are that this draft is correct and safe to send "
            "after a human skim. Score below 0.7 if the knowledge base did not "
            "actually cover this issue."
        ),
    )
    grounded_in: list[str] = Field(
        default_factory=list,
        description=(
            "IDs of the knowledge base articles this draft relies on. Empty if "
            "the draft is not grounded in any retrieved article."
        ),
    )
