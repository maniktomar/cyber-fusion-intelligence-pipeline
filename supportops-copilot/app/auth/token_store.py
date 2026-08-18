"""Encrypted-at-rest storage for Zendesk OAuth tokens.

Tokens are Fernet-encrypted (AES-128-CBC + HMAC-SHA256) before touching the
disk, so a leaked repo, backup, or container layer does not leak a live
Zendesk credential. The key itself comes from the environment and is never
written alongside the ciphertext.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TokenRecord:
    """A Zendesk OAuth token plus everything needed to reason about its life."""

    access_token: str
    token_type: str = "bearer"
    scope: str = "read write"
    refresh_token: str | None = None
    # None means the token does not expire (Zendesk's historical default).
    expires_at: datetime | None = None
    obtained_at: datetime | None = None

    @classmethod
    def from_token_response(
        cls, payload: dict, *, now: datetime | None = None
    ) -> TokenRecord:
        """Build a record from a Zendesk `/api/v2/oauth/tokens` response body."""
        now = now or utcnow()
        expires_in = payload.get("expires_in")
        expires_at = None
        if isinstance(expires_in, int | float) and expires_in > 0:
            expires_at = now + timedelta(seconds=float(expires_in))
        return cls(
            access_token=payload["access_token"],
            token_type=payload.get("token_type", "bearer"),
            scope=payload.get("scope", ""),
            refresh_token=payload.get("refresh_token"),
            expires_at=expires_at,
            obtained_at=now,
        )

    @property
    def expires(self) -> bool:
        return self.expires_at is not None

    def is_stale(self, *, leeway_seconds: int = 0, now: datetime | None = None) -> bool:
        """True when the token is expired, or close enough that we should renew.

        A non-expiring token is never stale.
        """
        if self.expires_at is None:
            return False
        now = now or utcnow()
        return now + timedelta(seconds=leeway_seconds) >= self.expires_at

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "token_type": self.token_type,
            "scope": self.scope,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "obtained_at": self.obtained_at.isoformat() if self.obtained_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TokenRecord:
        def _dt(value: str | None) -> datetime | None:
            if not value:
                return None
            parsed = datetime.fromisoformat(value)
            # Anything persisted without a zone is treated as UTC rather than
            # silently inheriting the host's local time.
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)

        return cls(
            access_token=data["access_token"],
            token_type=data.get("token_type", "bearer"),
            scope=data.get("scope", ""),
            refresh_token=data.get("refresh_token"),
            expires_at=_dt(data.get("expires_at")),
            obtained_at=_dt(data.get("obtained_at")),
        )

    def redacted(self) -> TokenRecord:
        """A copy safe to log: secrets replaced with a length-preserving marker."""
        return replace(
            self,
            access_token=f"<{len(self.access_token)} chars>",
            refresh_token=(
                f"<{len(self.refresh_token)} chars>" if self.refresh_token else None
            ),
        )


class CorruptTokenStoreError(Exception):
    """The token file exists but could not be decrypted or parsed."""


class EncryptedTokenStore:
    """Reads and writes a single encrypted `TokenRecord` on the local disk."""

    def __init__(self, path: str | Path, encryption_key: str | bytes) -> None:
        if not encryption_key:
            raise ValueError(
                "TOKEN_ENCRYPTION_KEY is empty. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        self.path = Path(path)
        self._fernet = Fernet(
            encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
        )

    def save(self, record: TokenRecord) -> None:
        """Encrypt and write atomically, so a crash cannot truncate the token."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        blob = self._fernet.encrypt(json.dumps(record.to_dict()).encode())
        fd, tmp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        # Best effort on POSIX; a no-op ACL-wise on Windows.
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o600)

    def load(self) -> TokenRecord | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self._fernet.decrypt(self.path.read_bytes()))
        except InvalidToken as exc:
            raise CorruptTokenStoreError(
                f"Could not decrypt {self.path}. Wrong TOKEN_ENCRYPTION_KEY, "
                "or the file was tampered with."
            ) from exc
        except json.JSONDecodeError as exc:
            raise CorruptTokenStoreError(f"{self.path} decrypted to invalid JSON") from exc
        return TokenRecord.from_dict(payload)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
