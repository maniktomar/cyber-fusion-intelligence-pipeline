"""Zendesk webhook signature verification.

The endpoint is public: anyone who finds the URL can POST to it. Without this
check, an attacker can hand us a forged `ticket.created` payload and make the
service act on a ticket ID of their choosing. So verification runs before the
body is parsed, let alone acted on.

Zendesk signs `timestamp + raw_body` with HMAC-SHA256 under the webhook's
signing secret and sends the result base64-encoded, alongside the timestamp:

    X-Zendesk-Webhook-Signature-Timestamp: 2026-08-18T12:00:00Z
    X-Zendesk-Webhook-Signature:           base64(HMAC-SHA256(secret, ts + body))

Two things this module deliberately does beyond checking the MAC:

* It compares in constant time. A byte-by-byte `==` on a MAC leaks the correct
  prefix through timing, which is enough to forge one given enough attempts.
* It rejects stale timestamps. A valid signature is valid forever otherwise, so
  a captured request could be replayed indefinitely.

Caveat, stated plainly: this construction was implemented from documented
behaviour and is verified against our own test vectors, not against a signature
produced by a live Zendesk account. Confirming it end-to-end is part of stage 2.
"""

from __future__ import annotations

import base64
import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Zendesk-Webhook-Signature"
TIMESTAMP_HEADER = "X-Zendesk-Webhook-Signature-Timestamp"

DEFAULT_TOLERANCE_SECONDS = 300


class VerificationFailure(StrEnum):
    MISSING_SIGNATURE = "missing_signature"
    MISSING_TIMESTAMP = "missing_timestamp"
    MALFORMED_TIMESTAMP = "malformed_timestamp"
    MALFORMED_SIGNATURE = "malformed_signature"
    STALE_TIMESTAMP = "stale_timestamp"
    SIGNATURE_MISMATCH = "signature_mismatch"
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class VerificationResult:
    ok: bool
    failure: VerificationFailure | None = None

    def __bool__(self) -> bool:
        return self.ok


_OK = VerificationResult(True)


def _fail(failure: VerificationFailure) -> VerificationResult:
    return VerificationResult(False, failure)


def compute_signature(secret: str, timestamp: str, body: bytes) -> str:
    """The signature Zendesk should have sent for this body, base64-encoded."""
    mac = hmac.new(
        secret.encode("utf-8"), timestamp.encode("utf-8") + body, sha256
    ).digest()
    return base64.b64encode(mac).decode("ascii")


def _parse_timestamp(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def verify_webhook(
    *,
    secret: str,
    body: bytes,
    signature: str | None,
    timestamp: str | None,
    now: datetime | None = None,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> VerificationResult:
    """Verify a Zendesk webhook delivery. Never raises; returns a reason."""
    if not secret:
        # Failing closed matters more here than convenience. An unset secret in
        # production would otherwise mean an unauthenticated public endpoint.
        logger.error("ZENDESK_WEBHOOK_SECRET is not set; rejecting all webhooks.")
        return _fail(VerificationFailure.NOT_CONFIGURED)
    if not signature:
        return _fail(VerificationFailure.MISSING_SIGNATURE)
    if not timestamp:
        return _fail(VerificationFailure.MISSING_TIMESTAMP)

    sent_at = _parse_timestamp(timestamp)
    if sent_at is None:
        return _fail(VerificationFailure.MALFORMED_TIMESTAMP)

    now = now or datetime.now(UTC)
    skew = abs((now - sent_at).total_seconds())
    if skew > tolerance_seconds:
        # Symmetric on purpose: a far-future timestamp is as suspicious as a
        # stale one, and clock skew cuts both ways.
        return _fail(VerificationFailure.STALE_TIMESTAMP)

    expected = compute_signature(secret, timestamp, body)
    try:
        provided_bytes = base64.b64decode(signature, validate=True)
    except (ValueError, TypeError):
        return _fail(VerificationFailure.MALFORMED_SIGNATURE)

    if not hmac.compare_digest(base64.b64decode(expected), provided_bytes):
        return _fail(VerificationFailure.SIGNATURE_MISMATCH)

    return _OK
