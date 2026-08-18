"""A small circuit breaker for the LLM dependency.

Rationale: when the model API is down, hammering it once per inbound ticket adds
latency to every single ticket before they all fall back anyway. Opening the
circuit makes the fallback immediate, which matters when a Zendesk webhook is
waiting on the response.

Deliberately not a library: three states and two counters is less code than the
configuration for a general-purpose one, and it keeps the failure semantics
readable in a code review.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Opens after `failure_threshold` consecutive failures.

    After `cooldown_seconds` it moves to half-open and lets exactly one probe
    through: success closes it, failure re-opens it for another cooldown.
    """

    failure_threshold: int = 3
    cooldown_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be at least 1")
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._opened_at: datetime | None = None
        self._probe_in_flight = False

    def state(self, *, now: datetime | None = None) -> CircuitState:
        now = now or _utcnow()
        with self._lock:
            return self._state_locked(now)

    def _state_locked(self, now: datetime) -> CircuitState:
        if self._opened_at is None:
            return CircuitState.CLOSED
        if now >= self._opened_at + timedelta(seconds=self.cooldown_seconds):
            return CircuitState.HALF_OPEN
        return CircuitState.OPEN

    def allows_request(self, *, now: datetime | None = None) -> bool:
        """True if a call should be attempted. Half-open admits one probe only."""
        now = now or _utcnow()
        with self._lock:
            state = self._state_locked(now)
            if state is CircuitState.CLOSED:
                return True
            if state is CircuitState.OPEN:
                return False
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def retry_after(self, *, now: datetime | None = None) -> float:
        now = now or _utcnow()
        with self._lock:
            if self._opened_at is None:
                return 0.0
            reopens_at = self._opened_at + timedelta(seconds=self.cooldown_seconds)
            return max(0.0, (reopens_at - now).total_seconds())

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None
            self._probe_in_flight = False

    def record_failure(self, *, now: datetime | None = None) -> None:
        now = now or _utcnow()
        with self._lock:
            self._probe_in_flight = False
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._opened_at = now

    def reset(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._opened_at = None
            self._probe_in_flight = False
