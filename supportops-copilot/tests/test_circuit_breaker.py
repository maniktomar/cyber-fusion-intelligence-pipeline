"""The breaker exists so a model outage fails fast instead of slowly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.circuit import CircuitBreaker, CircuitState

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def test_starts_closed_and_allows_requests():
    breaker = CircuitBreaker()
    assert breaker.state(now=NOW) is CircuitState.CLOSED
    assert breaker.allows_request(now=NOW) is True


def test_stays_closed_below_the_failure_threshold():
    breaker = CircuitBreaker(failure_threshold=3)
    breaker.record_failure(now=NOW)
    breaker.record_failure(now=NOW)
    assert breaker.state(now=NOW) is CircuitState.CLOSED
    assert breaker.allows_request(now=NOW) is True


def test_opens_at_the_failure_threshold():
    breaker = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        breaker.record_failure(now=NOW)
    assert breaker.state(now=NOW) is CircuitState.OPEN
    assert breaker.allows_request(now=NOW) is False


def test_a_success_resets_the_failure_streak():
    breaker = CircuitBreaker(failure_threshold=3)
    breaker.record_failure(now=NOW)
    breaker.record_failure(now=NOW)
    breaker.record_success()
    breaker.record_failure(now=NOW)
    assert breaker.state(now=NOW) is CircuitState.CLOSED


def test_moves_to_half_open_after_the_cooldown():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
    breaker.record_failure(now=NOW)
    later = NOW + timedelta(seconds=31)
    assert breaker.state(now=later) is CircuitState.HALF_OPEN


def test_half_open_admits_exactly_one_probe():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
    breaker.record_failure(now=NOW)
    later = NOW + timedelta(seconds=31)

    assert breaker.allows_request(now=later) is True
    assert breaker.allows_request(now=later) is False


def test_a_successful_probe_closes_the_circuit():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
    breaker.record_failure(now=NOW)
    later = NOW + timedelta(seconds=31)
    breaker.allows_request(now=later)
    breaker.record_success()

    assert breaker.state(now=later) is CircuitState.CLOSED
    assert breaker.allows_request(now=later) is True


def test_a_failed_probe_reopens_for_another_cooldown():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
    breaker.record_failure(now=NOW)
    probe_time = NOW + timedelta(seconds=31)
    breaker.allows_request(now=probe_time)
    breaker.record_failure(now=probe_time)

    assert breaker.state(now=probe_time) is CircuitState.OPEN
    assert breaker.allows_request(now=probe_time + timedelta(seconds=29)) is False


def test_retry_after_counts_down_and_floors_at_zero():
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=30)
    breaker.record_failure(now=NOW)

    assert breaker.retry_after(now=NOW) == 30.0
    assert breaker.retry_after(now=NOW + timedelta(seconds=10)) == 20.0
    assert breaker.retry_after(now=NOW + timedelta(seconds=99)) == 0.0


def test_retry_after_is_zero_when_closed():
    assert CircuitBreaker().retry_after(now=NOW) == 0.0


def test_reset_clears_an_open_circuit():
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure(now=NOW)
    breaker.reset()
    assert breaker.state(now=NOW) is CircuitState.CLOSED
