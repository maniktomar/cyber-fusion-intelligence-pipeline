"""Anthropic client wrapper: structured output, typed failures, circuit breaker.

Design notes worth knowing before changing this file:

* We call `messages.create` with `output_config.format` and validate the JSON
  ourselves, rather than `messages.parse`. Two reasons: `output_config` is the
  only place `effort` and `format` can be set together, and doing our own
  validation makes the malformed-response path an explicit, testable branch
  instead of an SDK internal.
* Transport retries (429, 5xx, connection resets) are the SDK's job -- it does
  exponential backoff already. Re-implementing that on top would multiply the
  attempt count. We configure `max_retries` and add exactly one retry of our
  own, for schema-validation failures, which the SDK cannot know about.
* Thinking is on by default on Claude Opus 5 and counts against `max_tokens`,
  so the budgets here are deliberately generous. A tight budget produces a
  truncated response, which surfaces as a malformed-response fallback.
"""

from __future__ import annotations

import json
import logging
from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.circuit import CircuitBreaker
from app.llm.errors import (
    CircuitOpenError,
    LLMMalformedResponseError,
    LLMRefusedError,
    LLMUnavailableError,
)
from app.llm.schema_tools import strict_schema

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = "claude-opus-5"


class StructuredLLMClient:
    """Asks Claude for one schema-shaped answer, or raises a typed failure."""

    def __init__(
        self,
        client: anthropic.Anthropic | None = None,
        *,
        model: str = DEFAULT_MODEL,
        breaker: CircuitBreaker | None = None,
        schema_retries: int = 1,
    ) -> None:
        self._client = client or anthropic.Anthropic(max_retries=2)
        self.model = model
        self.breaker = breaker or CircuitBreaker()
        self.schema_retries = schema_retries

    def complete(
        self,
        *,
        schema: type[T],
        system: str,
        user: str,
        max_tokens: int = 8192,
        effort: str = "low",
    ) -> T:
        """Return a validated `schema` instance, or raise an `LLMError`."""
        if not self.breaker.allows_request():
            raise CircuitOpenError(self.breaker.retry_after())

        try:
            result = self._complete_with_schema_retry(
                schema=schema,
                system=system,
                user=user,
                max_tokens=max_tokens,
                effort=effort,
            )
        except LLMMalformedResponseError:
            # A bad shape is not an outage: the model answered. Counting it as a
            # circuit failure would trip the breaker on one badly-worded prompt
            # and take the whole integration offline.
            raise
        except Exception:
            self.breaker.record_failure()
            raise

        self.breaker.record_success()
        return result

    def _complete_with_schema_retry(
        self, *, schema: type[T], system: str, user: str, max_tokens: int, effort: str
    ) -> T:
        last_error: LLMMalformedResponseError | None = None
        for attempt in range(self.schema_retries + 1):
            try:
                return self._complete_once(
                    schema=schema,
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    effort=effort,
                )
            except LLMMalformedResponseError as exc:
                last_error = exc
                logger.warning(
                    "Malformed model output (attempt %d/%d): %s",
                    attempt + 1,
                    self.schema_retries + 1,
                    exc,
                )
        assert last_error is not None
        raise last_error

    def _complete_once(
        self, *, schema: type[T], system: str, user: str, max_tokens: int, effort: str
    ) -> T:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": effort,
                    "format": {
                        "type": "json_schema",
                        "schema": strict_schema(schema),
                    },
                },
            )
        except anthropic.APIStatusError as exc:
            raise LLMUnavailableError(
                f"Anthropic API returned {exc.status_code}: {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailableError(f"Could not reach the Anthropic API: {exc}") from exc

        # A refusal is an HTTP 200 with an empty or partial content list, so it
        # has to be checked before anything reads content[0].
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMRefusedError(
                getattr(details, "category", None),
                getattr(details, "explanation", None),
            )

        if response.stop_reason == "max_tokens":
            raise LLMMalformedResponseError(
                f"Response hit the {max_tokens}-token cap and is truncated."
            )

        text = next(
            (block.text for block in response.content if block.type == "text"), None
        )
        if not text:
            raise LLMMalformedResponseError(
                f"Response contained no text block (stop_reason={response.stop_reason})."
            )

        try:
            return schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMMalformedResponseError(
                f"Output did not match {schema.__name__}: {exc.error_count()} error(s)"
            ) from exc
        except json.JSONDecodeError as exc:
            raise LLMMalformedResponseError(f"Output was not valid JSON: {exc}") from exc
