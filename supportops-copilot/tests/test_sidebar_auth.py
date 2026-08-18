"""Sidebar token verification.

Without this check the sidebar endpoint reads any ticket in the account by id,
unauthenticated. These tests exist to keep that from regressing quietly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.sidebar.auth import TokenFailure, extract_bearer, verify_sidebar_token

# 64 bytes. Below 32, PyJWT warns (RFC 7518 s3.2) and an offline brute force
# against one captured token becomes realistic -- so the tests use a
# realistically-sized secret rather than muting the warning.
SECRET = "app_shared_secret_" + "x" * 46
ISSUER = "acme-sandbox.zendesk.com"


def a_token(secret: str = SECRET, algorithm: str = "HS256", **claims) -> str:
    payload = {
        "iss": ISSUER,
        "exp": datetime.now(UTC) + timedelta(minutes=2),
        "sub": "agent@example.com",
    }
    payload.update(claims)
    return jwt.encode(payload, secret, algorithm=algorithm)


class TestExtractBearer:
    def test_pulls_the_token(self):
        assert extract_bearer("Bearer abc.def.ghi") == "abc.def.ghi"

    def test_is_case_insensitive_on_the_scheme(self):
        assert extract_bearer("bearer abc") == "abc"

    @pytest.mark.parametrize(
        "header", [None, "", "abc.def.ghi", "Basic dXNlcjpwYXNz", "Bearer", "Bearer   "]
    )
    def test_anything_else_yields_none(self, header):
        assert extract_bearer(header) is None


class TestValidTokens:
    def test_a_correctly_signed_token_is_accepted(self):
        result = verify_sidebar_token(a_token(), secret=SECRET)

        assert result.ok is True
        assert result.claims["sub"] == "agent@example.com"

    def test_the_result_is_truthy(self):
        assert verify_sidebar_token(a_token(), secret=SECRET)

    def test_a_matching_issuer_is_accepted(self):
        result = verify_sidebar_token(
            a_token(), secret=SECRET, expected_issuer=ISSUER
        )
        assert result.ok is True

    def test_small_clock_skew_is_tolerated(self):
        just_expired = a_token(exp=datetime.now(UTC) - timedelta(seconds=10))
        assert verify_sidebar_token(just_expired, secret=SECRET).ok is True


class TestRejectedTokens:
    def test_a_missing_token_is_rejected(self):
        result = verify_sidebar_token(None, secret=SECRET)
        assert result.failure is TokenFailure.MISSING

    def test_a_token_signed_with_the_wrong_secret_is_rejected(self):
        result = verify_sidebar_token(a_token(secret="attacker_guess_" + "y" * 49), secret=SECRET)
        assert result.failure is TokenFailure.BAD_SIGNATURE

    def test_an_expired_token_is_rejected(self):
        expired = a_token(exp=datetime.now(UTC) - timedelta(hours=1))
        result = verify_sidebar_token(expired, secret=SECRET)
        assert result.failure is TokenFailure.EXPIRED

    def test_a_token_with_no_expiry_is_rejected(self):
        """A token that never expires is a permanent key in a URL bar."""
        forever = jwt.encode({"iss": ISSUER}, SECRET, algorithm="HS256")
        result = verify_sidebar_token(forever, secret=SECRET)
        assert result.failure is TokenFailure.MALFORMED

    def test_a_wrong_issuer_is_rejected(self):
        result = verify_sidebar_token(
            a_token(iss="evil.zendesk.com"), secret=SECRET, expected_issuer=ISSUER
        )
        assert result.failure is TokenFailure.WRONG_ISSUER

    @pytest.mark.parametrize("garbage", ["", "not.a.token", "a.b", "....", "x" * 200])
    def test_structurally_broken_tokens_are_rejected(self, garbage):
        result = verify_sidebar_token(garbage, secret=SECRET)
        assert result.ok is False

    def test_an_unset_secret_rejects_everything(self):
        """Fail closed: an unset secret must not mean 'no auth required'."""
        result = verify_sidebar_token(a_token(), secret="")
        assert result.failure is TokenFailure.NOT_CONFIGURED


class TestAlgorithmConfusion:
    """The classic JWT attack: control the algorithm, control the verification."""

    def test_an_unsigned_alg_none_token_is_rejected(self):
        unsigned = jwt.encode(
            {"iss": ISSUER, "exp": datetime.now(UTC) + timedelta(minutes=2)},
            key="",
            algorithm="none",
        )
        assert verify_sidebar_token(unsigned, secret=SECRET).ok is False

    def test_a_token_signed_with_a_different_hmac_algorithm_is_rejected(self):
        other = a_token(algorithm="HS512")
        assert verify_sidebar_token(other, secret=SECRET).ok is False

    def test_the_algorithm_is_never_read_from_the_token_header(self):
        """Pinned to HS256 in code; the token's own header has no say."""
        for algorithm in ("HS384", "HS512"):
            assert verify_sidebar_token(
                a_token(algorithm=algorithm), secret=SECRET
            ).ok is False
