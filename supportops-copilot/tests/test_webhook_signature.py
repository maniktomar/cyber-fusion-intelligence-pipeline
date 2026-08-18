"""Signature verification: the endpoint's only defence against a forged event."""

from __future__ import annotations

import base64
import hmac
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from app.webhooks.signature import (
    VerificationFailure,
    compute_signature,
    verify_webhook,
)

SECRET = "whsec_test_secret_value"
BODY = b'{"type":"zen:event-type:ticket.created","detail":{"id":4242}}'
TIMESTAMP = "2026-08-18T12:00:00Z"
NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def signed(body: bytes = BODY, timestamp: str = TIMESTAMP, secret: str = SECRET) -> str:
    return compute_signature(secret, timestamp, body)


def verify(**overrides):
    kwargs = {
        "secret": SECRET,
        "body": BODY,
        "signature": signed(),
        "timestamp": TIMESTAMP,
        "now": NOW,
    }
    kwargs.update(overrides)
    return verify_webhook(**kwargs)


class TestValidDeliveries:
    def test_a_correctly_signed_payload_is_accepted(self):
        assert verify().ok is True

    def test_the_result_is_truthy(self):
        assert verify()

    def test_signature_matches_an_independently_computed_hmac(self):
        """Pinned against the construction, not against our own helper."""
        expected = base64.b64encode(
            hmac.new(
                SECRET.encode(), TIMESTAMP.encode() + BODY, sha256
            ).digest()
        ).decode()
        assert signed() == expected

    def test_accepts_a_timestamp_with_an_explicit_offset(self):
        stamp = "2026-08-18T14:00:00+02:00"
        assert verify(timestamp=stamp, signature=signed(timestamp=stamp)).ok is True

    def test_accepts_an_empty_body(self):
        assert verify(body=b"", signature=signed(body=b"")).ok is True

    @pytest.mark.parametrize("skew", [0, 60, 299, -299])
    def test_accepts_timestamps_inside_the_tolerance(self, skew):
        assert verify(now=NOW + timedelta(seconds=skew)).ok is True


class TestTamperedDeliveries:
    def test_a_modified_body_is_rejected(self):
        tampered = BODY.replace(b"4242", b"9999")
        result = verify(body=tampered)

        assert result.ok is False
        assert result.failure is VerificationFailure.SIGNATURE_MISMATCH

    def test_a_single_flipped_byte_is_rejected(self):
        tampered = bytearray(BODY)
        tampered[10] ^= 0x01
        assert verify(body=bytes(tampered)).ok is False

    def test_appended_bytes_are_rejected(self):
        assert verify(body=BODY + b" ").ok is False

    def test_a_signature_from_a_different_secret_is_rejected(self):
        result = verify(signature=signed(secret="whsec_the_attackers_guess"))
        assert result.failure is VerificationFailure.SIGNATURE_MISMATCH

    def test_a_signature_for_a_different_timestamp_is_rejected(self):
        """Stops a captured signature being reused with a fresh timestamp."""
        other = "2026-08-18T11:59:00Z"
        result = verify(signature=signed(timestamp=other))
        assert result.failure is VerificationFailure.SIGNATURE_MISMATCH

    def test_a_signature_for_a_different_body_is_rejected(self):
        result = verify(signature=signed(body=b'{"detail":{"id":1}}'))
        assert result.failure is VerificationFailure.SIGNATURE_MISMATCH


class TestReplayProtection:
    def test_a_stale_delivery_is_rejected(self):
        result = verify(now=NOW + timedelta(seconds=301))

        assert result.ok is False
        assert result.failure is VerificationFailure.STALE_TIMESTAMP

    def test_a_far_future_delivery_is_rejected(self):
        """Symmetric: a future timestamp is as suspicious as a stale one."""
        result = verify(now=NOW - timedelta(seconds=301))
        assert result.failure is VerificationFailure.STALE_TIMESTAMP

    def test_the_tolerance_is_configurable(self):
        assert verify(now=NOW + timedelta(seconds=301), tolerance_seconds=600).ok is True

    def test_a_perfectly_valid_signature_still_expires(self):
        """The signature is correct; only its age makes it unacceptable."""
        result = verify(now=NOW + timedelta(days=1))
        assert result.failure is VerificationFailure.STALE_TIMESTAMP


class TestMalformedInput:
    def test_missing_signature_header(self):
        assert verify(signature=None).failure is VerificationFailure.MISSING_SIGNATURE

    def test_missing_timestamp_header(self):
        assert verify(timestamp=None).failure is VerificationFailure.MISSING_TIMESTAMP

    def test_empty_signature_header(self):
        assert verify(signature="").failure is VerificationFailure.MISSING_SIGNATURE

    def test_unparseable_timestamp(self):
        result = verify(timestamp="last Tuesday")
        assert result.failure is VerificationFailure.MALFORMED_TIMESTAMP

    def test_non_base64_signature(self):
        result = verify(signature="!!!not base64!!!")
        assert result.failure is VerificationFailure.MALFORMED_SIGNATURE

    def test_an_unset_secret_rejects_everything(self):
        """Fail closed: an unset secret must not mean an open public endpoint."""
        result = verify(secret="")
        assert result.ok is False
        assert result.failure is VerificationFailure.NOT_CONFIGURED

    def test_an_unset_secret_rejects_even_a_matching_signature(self):
        assert verify_webhook(
            secret="", body=BODY, signature=signed(secret=""), timestamp=TIMESTAMP, now=NOW
        ).ok is False
