"""LLM client behaviour against a faked Anthropic SDK surface.

We stub `messages.create` rather than the HTTP layer: the contract we care
about is how we interpret a Message object (stop_reason, content blocks), and
the SDK's own transport is not ours to test.
"""

from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app.circuit import CircuitBreaker, CircuitState
from app.llm.client import StructuredLLMClient
from app.llm.errors import (
    CircuitOpenError,
    LLMMalformedResponseError,
    LLMRefusedError,
    LLMUnavailableError,
)
from app.llm.schemas import Classification

VALID_JSON = (
    '{"category": "billing", "urgency": "normal", "sentiment": "neutral",'
    ' "confidence": 0.91, "reasoning": "Duplicate charge on one period."}'
)


def message(text: str | None = VALID_JSON, stop_reason: str = "end_turn", **extra):
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        stop_details=extra.get("stop_details"),
    )


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0) if self._responses else message()
        if isinstance(item, Exception):
            raise item
        return item


class FakeAnthropic:
    def __init__(self, responses=()):
        self.messages = FakeMessages(responses)


def build(responses=(), **kwargs) -> StructuredLLMClient:
    fake = FakeAnthropic(responses)
    return StructuredLLMClient(fake, **kwargs)


def api_status_error(status: int) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status, json={"error": {"message": "boom"}}, request=request)
    return anthropic.APIStatusError("boom", response=response, body=None)


class TestHappyPath:
    def test_valid_response_is_parsed_into_the_schema(self):
        client = build([message()])
        result = client.complete(schema=Classification, system="s", user="u")

        assert result.category.value == "billing"
        assert result.confidence == pytest.approx(0.91)

    def test_request_carries_the_schema_and_effort(self):
        client = build([message()])
        client.complete(schema=Classification, system="s", user="u", effort="medium")

        sent = client._client.messages.calls[0]
        assert sent["output_config"]["effort"] == "medium"
        assert sent["output_config"]["format"]["type"] == "json_schema"
        assert sent["output_config"]["format"]["schema"]["additionalProperties"] is False

    def test_schema_marks_every_property_required(self):
        client = build([message()])
        client.complete(schema=Classification, system="s", user="u")

        schema = client._client.messages.calls[0]["output_config"]["format"]["schema"]
        assert set(schema["required"]) == set(schema["properties"])

    def test_unsupported_json_schema_keywords_are_stripped(self):
        """`confidence` has ge/le and `reasoning` has max_length; both would 400."""
        client = build([message()])
        client.complete(schema=Classification, system="s", user="u")

        schema = client._client.messages.calls[0]["output_config"]["format"]["schema"]
        serialised = str(schema)
        for keyword in ("minimum", "maximum", "maxLength"):
            assert keyword not in serialised

    def test_pydantic_still_enforces_the_stripped_bounds(self):
        """Stripping them from the wire schema must not lose local validation."""
        out_of_range = VALID_JSON.replace('"confidence": 0.91', '"confidence": 4.2')
        client = build([message(out_of_range), message(out_of_range)])

        with pytest.raises(LLMMalformedResponseError):
            client.complete(schema=Classification, system="s", user="u")


class TestFailureModes:
    def test_refusal_raises_rather_than_reading_empty_content(self):
        refused = message(
            text=None,
            stop_reason="refusal",
            stop_details=SimpleNamespace(category="cyber", explanation="declined"),
        )
        client = build([refused])

        with pytest.raises(LLMRefusedError) as exc:
            client.complete(schema=Classification, system="s", user="u")
        assert exc.value.category == "cyber"

    def test_refusal_with_null_stop_details_still_raises_cleanly(self):
        client = build([message(text=None, stop_reason="refusal")])

        with pytest.raises(LLMRefusedError) as exc:
            client.complete(schema=Classification, system="s", user="u")
        assert exc.value.category is None

    def test_truncated_response_is_malformed_not_parsed(self):
        client = build([message('{"category": "bil', stop_reason="max_tokens")] * 2)

        with pytest.raises(LLMMalformedResponseError, match="truncated"):
            client.complete(schema=Classification, system="s", user="u")

    def test_response_with_no_text_block_is_malformed(self):
        client = build([message(text=None)] * 2)

        with pytest.raises(LLMMalformedResponseError, match="no text block"):
            client.complete(schema=Classification, system="s", user="u")

    def test_non_json_text_is_malformed(self):
        client = build([message("I'd say this is a billing issue.")] * 2)

        with pytest.raises(LLMMalformedResponseError):
            client.complete(schema=Classification, system="s", user="u")

    def test_json_missing_a_required_field_is_malformed(self):
        partial = '{"category": "billing", "urgency": "normal"}'
        client = build([message(partial)] * 2)

        with pytest.raises(LLMMalformedResponseError, match="did not match"):
            client.complete(schema=Classification, system="s", user="u")

    def test_invalid_enum_value_is_malformed(self):
        bad = VALID_JSON.replace('"billing"', '"refunds_and_stuff"')
        client = build([message(bad)] * 2)

        with pytest.raises(LLMMalformedResponseError):
            client.complete(schema=Classification, system="s", user="u")

    def test_api_status_error_becomes_unavailable(self):
        client = build([api_status_error(500)])

        with pytest.raises(LLMUnavailableError, match="500"):
            client.complete(schema=Classification, system="s", user="u")

    def test_connection_error_becomes_unavailable(self):
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        client = build([anthropic.APIConnectionError(request=request)])

        with pytest.raises(LLMUnavailableError, match="reach"):
            client.complete(schema=Classification, system="s", user="u")


class TestSchemaRetry:
    def test_one_bad_response_then_a_good_one_succeeds(self):
        client = build([message("not json"), message()], schema_retries=1)
        result = client.complete(schema=Classification, system="s", user="u")

        assert result.category.value == "billing"
        assert len(client._client.messages.calls) == 2

    def test_retries_are_bounded(self):
        client = build([message("not json")] * 5, schema_retries=1)

        with pytest.raises(LLMMalformedResponseError):
            client.complete(schema=Classification, system="s", user="u")
        assert len(client._client.messages.calls) == 2

    def test_retries_can_be_disabled(self):
        client = build([message("not json"), message()], schema_retries=0)

        with pytest.raises(LLMMalformedResponseError):
            client.complete(schema=Classification, system="s", user="u")
        assert len(client._client.messages.calls) == 1

    def test_transport_errors_are_not_retried_by_us(self):
        """The SDK already backs off on 5xx; retrying here would multiply attempts."""
        client = build([api_status_error(503)] * 3, schema_retries=1)

        with pytest.raises(LLMUnavailableError):
            client.complete(schema=Classification, system="s", user="u")
        assert len(client._client.messages.calls) == 1


class TestCircuitInteraction:
    def test_transport_failures_trip_the_breaker(self):
        breaker = CircuitBreaker(failure_threshold=2)
        client = build([api_status_error(500)] * 4, breaker=breaker)

        for _ in range(2):
            with pytest.raises(LLMUnavailableError):
                client.complete(schema=Classification, system="s", user="u")

        assert breaker.state() is CircuitState.OPEN

    def test_open_circuit_short_circuits_without_calling_the_api(self):
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure()
        client = build([message()], breaker=breaker)

        with pytest.raises(CircuitOpenError):
            client.complete(schema=Classification, system="s", user="u")
        assert client._client.messages.calls == []

    def test_malformed_output_does_not_trip_the_breaker(self):
        """A bad shape means the service is up. Tripping here would take the
        whole integration offline over one badly-worded prompt."""
        breaker = CircuitBreaker(failure_threshold=2)
        client = build([message("not json")] * 8, breaker=breaker, schema_retries=1)

        for _ in range(4):
            with pytest.raises(LLMMalformedResponseError):
                client.complete(schema=Classification, system="s", user="u")

        assert breaker.state() is CircuitState.CLOSED

    def test_a_refusal_does_trip_the_breaker(self):
        """A refusal is a completed call, but a stream of them means something
        systemic is wrong with what we're sending -- back off rather than spam."""
        breaker = CircuitBreaker(failure_threshold=2)
        refused = message(text=None, stop_reason="refusal")
        client = build([refused] * 4, breaker=breaker)

        for _ in range(2):
            with pytest.raises(LLMRefusedError):
                client.complete(schema=Classification, system="s", user="u")

        assert breaker.state() is CircuitState.OPEN

    def test_a_success_after_failures_keeps_the_circuit_closed(self):
        breaker = CircuitBreaker(failure_threshold=3)
        client = build([api_status_error(500), message()], breaker=breaker)

        with pytest.raises(LLMUnavailableError):
            client.complete(schema=Classification, system="s", user="u")
        client.complete(schema=Classification, system="s", user="u")

        assert breaker.state() is CircuitState.CLOSED
