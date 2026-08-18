"""Tokens must be unreadable at rest and survive a round trip intact."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

from app.auth.token_store import (
    CorruptTokenStoreError,
    EncryptedTokenStore,
    TokenRecord,
)


def _record(**overrides) -> TokenRecord:
    base = {
        "access_token": "zd_access_abc123",
        "token_type": "bearer",
        "scope": "read write",
        "refresh_token": "zd_refresh_xyz789",
        "expires_at": datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        "obtained_at": datetime(2026, 8, 18, 12, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return TokenRecord(**base)


def test_round_trip_preserves_every_field(token_store):
    original = _record()
    token_store.save(original)
    assert token_store.load() == original


def test_load_returns_none_when_nothing_stored(token_store):
    assert token_store.load() is None


def test_plaintext_token_never_appears_on_disk(token_store):
    token_store.save(_record())
    raw = token_store.path.read_bytes()
    assert b"zd_access_abc123" not in raw
    assert b"zd_refresh_xyz789" not in raw


def test_wrong_key_cannot_decrypt(token_store, tmp_path):
    token_store.save(_record())
    attacker = EncryptedTokenStore(token_store.path, Fernet.generate_key().decode())
    with pytest.raises(CorruptTokenStoreError):
        attacker.load()


def test_tampered_ciphertext_is_rejected(token_store):
    token_store.save(_record())
    blob = bytearray(token_store.path.read_bytes())
    blob[-5] = blob[-5] ^ 0xFF
    token_store.path.write_bytes(bytes(blob))
    with pytest.raises(CorruptTokenStoreError):
        token_store.load()


def test_save_overwrites_previous_token(token_store):
    token_store.save(_record(access_token="first"))
    token_store.save(_record(access_token="second"))
    assert token_store.load().access_token == "second"


def test_clear_removes_the_file(token_store):
    token_store.save(_record())
    token_store.clear()
    assert token_store.load() is None
    token_store.clear()  # idempotent


def test_empty_encryption_key_is_refused(tmp_path):
    with pytest.raises(ValueError, match="TOKEN_ENCRYPTION_KEY"):
        EncryptedTokenStore(tmp_path / "t.enc", "")


def test_naive_stored_datetime_is_read_as_utc():
    record = TokenRecord.from_dict(
        {"access_token": "a", "expires_at": "2026-09-01T12:00:00"}
    )
    assert record.expires_at == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def test_non_utc_stored_datetime_is_normalised_to_utc():
    record = TokenRecord.from_dict(
        {"access_token": "a", "expires_at": "2026-09-01T14:00:00+02:00"}
    )
    assert record.expires_at == datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def test_redacted_hides_secrets_but_keeps_shape():
    red = _record().redacted()
    assert "abc123" not in red.access_token
    assert "xyz789" not in (red.refresh_token or "")
    assert red.scope == "read write"


def test_directory_is_created_on_first_save(tmp_path, encryption_key):
    nested = tmp_path / "deep" / "nested" / "tokens.enc"
    store = EncryptedTokenStore(nested, encryption_key)
    store.save(_record())
    assert nested.exists()


def test_no_temp_files_left_behind(token_store):
    token_store.save(_record())
    leftovers = list(token_store.path.parent.glob("*.tmp"))
    assert leftovers == []


class TestStaleness:
    NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    def test_non_expiring_token_is_never_stale(self):
        record = _record(expires_at=None)
        assert record.expires is False
        assert record.is_stale(leeway_seconds=86_400, now=self.NOW) is False

    def test_token_well_inside_its_life_is_fresh(self):
        record = _record(expires_at=self.NOW + timedelta(hours=2))
        assert record.is_stale(leeway_seconds=300, now=self.NOW) is False

    def test_token_inside_the_leeway_window_is_stale(self):
        record = _record(expires_at=self.NOW + timedelta(seconds=60))
        assert record.is_stale(leeway_seconds=300, now=self.NOW) is True

    def test_expired_token_is_stale(self):
        record = _record(expires_at=self.NOW - timedelta(seconds=1))
        assert record.is_stale(leeway_seconds=0, now=self.NOW) is True

    def test_boundary_exactly_at_leeway_counts_as_stale(self):
        record = _record(expires_at=self.NOW + timedelta(seconds=300))
        assert record.is_stale(leeway_seconds=300, now=self.NOW) is True


class TestFromTokenResponse:
    NOW = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)

    def test_expires_in_becomes_an_absolute_utc_deadline(self):
        record = TokenRecord.from_token_response(
            {"access_token": "a", "expires_in": 3600}, now=self.NOW
        )
        assert record.expires_at == self.NOW + timedelta(hours=1)

    def test_absent_expires_in_means_non_expiring(self):
        record = TokenRecord.from_token_response({"access_token": "a"}, now=self.NOW)
        assert record.expires_at is None
        assert record.expires is False

    def test_zero_expires_in_is_treated_as_non_expiring(self):
        record = TokenRecord.from_token_response(
            {"access_token": "a", "expires_in": 0}, now=self.NOW
        )
        assert record.expires_at is None

    def test_defaults_are_applied_for_optional_fields(self):
        record = TokenRecord.from_token_response({"access_token": "a"}, now=self.NOW)
        assert record.token_type == "bearer"
        assert record.refresh_token is None
        assert record.obtained_at == self.NOW
