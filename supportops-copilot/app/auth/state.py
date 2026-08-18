"""Single-use, expiring `state` values for the OAuth authorization-code flow.

The `state` parameter is the CSRF defence for OAuth: without it, an attacker can
hand the victim's browser a callback URL carrying the *attacker's* auth code and
quietly bind the attacker's Zendesk account to our stored token. So state values
here are random, time-limited, and consumed exactly once.
"""

from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime, timedelta


def _utcnow() -> datetime:
    return datetime.now(UTC)


class OAuthStateStore:
    """In-process store of issued state values.

    Deliberately in-memory: this is a single-instance dev/demo service, and a
    restart invalidating in-flight logins is the safe failure. Running more than
    one replica requires moving this to Redis — see README "Known Limitations".
    """

    def __init__(self, ttl_seconds: int = 600) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._issued: dict[str, datetime] = {}
        self._lock = threading.Lock()

    def issue(self, *, now: datetime | None = None) -> str:
        now = now or _utcnow()
        state = secrets.token_urlsafe(32)
        with self._lock:
            self._purge(now)
            self._issued[state] = now + self._ttl
        return state

    def consume(self, state: str, *, now: datetime | None = None) -> bool:
        """Validate and burn a state value. False if unknown, expired, or replayed."""
        if not state:
            return False
        now = now or _utcnow()
        with self._lock:
            self._purge(now)
            expiry = self._issued.pop(state, None)
        return expiry is not None and expiry > now

    def _purge(self, now: datetime) -> None:
        expired = [s for s, exp in self._issued.items() if exp <= now]
        for s in expired:
            del self._issued[s]

    def __len__(self) -> int:
        """Number of states currently held, including any not yet purged.

        Purging happens on issue/consume, not here: a diagnostic accessor must
        not depend on the wall clock, or tests that inject a clock elsewhere
        silently disagree with it.
        """
        with self._lock:
            return len(self._issued)
