"""Typed errors for the LLM layer.

The triage service branches on these, so they carve the failure space along the
lines the fallback logic actually cares about: "the model gave us something we
could not use" vs "we could not reach the model at all" vs "the model declined".
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class for every failure raised by the LLM client."""


class LLMUnavailableError(LLMError):
    """The model could not be reached, or kept failing after retries."""


class LLMMalformedResponseError(LLMError):
    """The model responded, but not in a shape we can trust.

    Covers schema-validation failures and truncated output. Notably *not* an
    availability problem: retrying the same prompt may well produce the same
    garbage, so the fallback path treats this differently.
    """


class LLMRefusedError(LLMError):
    """The model declined the request on safety grounds.

    Arrives as a normal HTTP 200 with stop_reason == "refusal", so it has to be
    checked explicitly rather than caught.
    """

    def __init__(self, category: str | None, explanation: str | None) -> None:
        self.category = category
        self.explanation = explanation
        super().__init__(f"Model refused the request (category={category})")


class CircuitOpenError(LLMError):
    """The circuit breaker is open; we are deliberately not calling the model."""

    def __init__(self, retry_after_seconds: float) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            f"LLM circuit breaker is open; retrying in {retry_after_seconds:.0f}s"
        )
