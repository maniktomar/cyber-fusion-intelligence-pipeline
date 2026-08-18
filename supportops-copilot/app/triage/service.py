"""Orchestrates triage: classify, retrieve, draft, then gate.

The structure is deliberately linear and the error handling deliberately
exhaustive: every way this can go wrong converges on `flag_only`, so there is
no code path that returns None, raises past the caller, or leaves a ticket
neither triaged nor flagged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.llm.client import StructuredLLMClient
from app.llm.errors import (
    CircuitOpenError,
    LLMMalformedResponseError,
    LLMRefusedError,
    LLMUnavailableError,
)
from app.llm.schemas import Classification, Draft
from app.triage import prompts
from app.triage.decision import (
    FallbackReason,
    Thresholds,
    TriageDecision,
    decide,
    flag_only,
)
from app.triage.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

CLASSIFY_MAX_TOKENS = 8192
DRAFT_MAX_TOKENS = 16000


@dataclass(frozen=True)
class Ticket:
    """The subset of a Zendesk ticket triage actually reads."""

    id: int
    subject: str
    body: str

    @property
    def has_content(self) -> bool:
        return bool(self.body.strip() or self.subject.strip())


class TriageService:
    def __init__(
        self,
        llm: StructuredLLMClient,
        knowledge_base: KnowledgeBase,
        *,
        thresholds: Thresholds | None = None,
        retrieval_limit: int = 3,
    ) -> None:
        self.llm = llm
        self.kb = knowledge_base
        self.thresholds = thresholds or Thresholds()
        self.retrieval_limit = retrieval_limit

    def triage(self, ticket: Ticket) -> TriageDecision:
        """Always returns a decision. Never raises for an expected failure."""
        if not ticket.has_content:
            return flag_only(FallbackReason.EMPTY_TICKET)

        classification = self._classify(ticket)
        if isinstance(classification, TriageDecision):
            return classification

        draft = self._draft(ticket, classification)
        if isinstance(draft, TriageDecision):
            return draft

        decision = decide(classification, draft, self.thresholds)
        logger.info(
            "Ticket %s triaged: action=%s reason=%s",
            ticket.id,
            decision.action.value,
            decision.reason.value if decision.reason else "-",
        )
        return decision

    def _classify(self, ticket: Ticket) -> Classification | TriageDecision:
        try:
            return self.llm.complete(
                schema=Classification,
                system=prompts.CLASSIFY_SYSTEM,
                user=prompts.classify_user_prompt(ticket.subject, ticket.body),
                max_tokens=CLASSIFY_MAX_TOKENS,
                effort="low",
            )
        except Exception as exc:
            return self._fallback_for(exc, ticket, stage="classification")

    def _draft(
        self, ticket: Ticket, classification: Classification
    ) -> Draft | TriageDecision:
        query = f"{ticket.subject} {ticket.body}"
        hits = self.kb.search(query, limit=self.retrieval_limit)
        articles_block = "\n\n".join(hit.article.as_prompt_block() for hit in hits)

        try:
            draft = self.llm.complete(
                schema=Draft,
                system=prompts.DRAFT_SYSTEM,
                user=prompts.draft_user_prompt(
                    ticket.subject,
                    ticket.body,
                    classification.category.value,
                    articles_block,
                ),
                max_tokens=DRAFT_MAX_TOKENS,
                effort="medium",
            )
        except Exception as exc:
            return self._fallback_for(exc, ticket, stage="draft")

        # A citation the retriever never surfaced means the model invented an ID.
        # Drop it rather than trusting it; the grounding check then applies.
        retrieved_ids = {hit.article.id for hit in hits}
        verified = [aid for aid in draft.grounded_in if aid in retrieved_ids]
        if verified != draft.grounded_in:
            logger.warning(
                "Ticket %s: dropped unverifiable citations %s",
                ticket.id,
                sorted(set(draft.grounded_in) - retrieved_ids),
            )
            draft = draft.model_copy(update={"grounded_in": verified})
        return draft

    def _fallback_for(
        self, exc: Exception, ticket: Ticket, *, stage: str
    ) -> TriageDecision:
        """Map any exception onto a flag-only decision with an honest reason."""
        if isinstance(exc, CircuitOpenError):
            reason, detail = FallbackReason.CIRCUIT_OPEN, str(exc)
        elif isinstance(exc, LLMRefusedError):
            reason = FallbackReason.MODEL_REFUSED
            detail = f"refusal category: {exc.category or 'unspecified'}"
        elif isinstance(exc, LLMMalformedResponseError):
            reason, detail = FallbackReason.MALFORMED_RESPONSE, str(exc)
        elif isinstance(exc, LLMUnavailableError):
            reason, detail = FallbackReason.LLM_UNAVAILABLE, str(exc)
        else:
            # An unexpected exception is still a ticket that needs a human. It
            # gets flagged like any other failure, and logged with a traceback
            # so the bug is visible rather than absorbed.
            logger.exception(
                "Ticket %s: unexpected error during %s", ticket.id, stage
            )
            reason = FallbackReason.LLM_UNAVAILABLE
            detail = f"unexpected {type(exc).__name__} during {stage}"

        logger.warning(
            "Ticket %s: falling back at %s stage (%s)", ticket.id, stage, reason.value
        )
        return flag_only(reason, detail)
