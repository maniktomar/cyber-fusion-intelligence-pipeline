"""Verifying the signed request a Zendesk App sends to its own backend.

When a ZAF app calls `client.request()` against a URL declared as `secure` in
the app manifest, Zendesk signs the outbound request with a JWT minted from the
app's shared secret. The backend verifies that JWT before answering. Without it
the sidebar endpoint is an unauthenticated read of any ticket in the account by
id, which is a data-leak endpoint with a friendly name.

Two deliberate choices:

* **HS256 only, pinned.** Accepting the `alg` in the token's own header is the
  classic JWT vulnerability -- an attacker sets `alg: none` or swaps to a
  public-key algorithm and signs with a key they control. The algorithm is
  pinned here and the token's header is not consulted.
* **Fail closed on an unset secret.** Same reasoning as the webhook: an unset
  secret must not silently mean "no authentication required".

Caveat, stated plainly: the *shape* of Zendesk's claims (`iss`, `sub`,
`qsh`, and their exact contents) is implemented from documented behaviour and
verified against tokens this codebase mints in its own tests, not against a
token from a live Zendesk app installation. Confirming the claim names is part
of stage 2. The signature, expiry, and algorithm checks are correct regardless;
it is the issuer check that may need adjusting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

import jwt

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"

# Leeway for clock skew between Zendesk and us, in seconds. Small on purpose:
# these tokens are short-lived and a generous window widens the replay
# opportunity for a captured one.
CLOCK_SKEW_LEEWAY = 30


class TokenFailure(StrEnum):
    NOT_CONFIGURED = "not_configured"
    MISSING = "missing"
    MALFORMED = "malformed"
    EXPIRED = "expired"
    BAD_SIGNATURE = "bad_signature"
    WRONG_ISSUER = "wrong_issuer"


@dataclass(frozen=True)
class TokenResult:
    ok: bool
    failure: TokenFailure | None = None
    claims: dict | None = None

    def __bool__(self) -> bool:
        return self.ok


def _fail(failure: TokenFailure) -> TokenResult:
    return TokenResult(False, failure)


def extract_bearer(header: str | None) -> str | None:
    """Pull the token out of an `Authorization: Bearer <token>` header."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def verify_sidebar_token(
    token: str | None,
    *,
    secret: str,
    expected_issuer: str | None = None,
) -> TokenResult:
    """Verify a ZAF-signed request token. Never raises; returns a reason."""
    if not secret:
        logger.error("ZENDESK_APP_SECRET is not set; rejecting all sidebar requests.")
        return _fail(TokenFailure.NOT_CONFIGURED)
    if not token:
        return _fail(TokenFailure.MISSING)

    try:
        claims = jwt.decode(
            token,
            secret,
            # Pinned. Never read the algorithm from the token's own header.
            algorithms=[ALGORITHM],
            leeway=CLOCK_SKEW_LEEWAY,
            options={"require": ["exp"]},
        )
    except jwt.ExpiredSignatureError:
        return _fail(TokenFailure.EXPIRED)
    except jwt.InvalidSignatureError:
        return _fail(TokenFailure.BAD_SIGNATURE)
    except jwt.InvalidTokenError:
        # Covers a missing `exp`, a wrong algorithm, and structurally broken
        # tokens. All of them mean the same thing to a caller: do not trust it.
        return _fail(TokenFailure.MALFORMED)

    if expected_issuer and claims.get("iss") != expected_issuer:
        return _fail(TokenFailure.WRONG_ISSUER)

    return TokenResult(True, claims=claims)
