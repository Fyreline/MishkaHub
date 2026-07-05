"""Password hashing + JWT access tokens + opaque refresh tokens.

docs/phases/PHASE-4-accounts-feedback.md §2-3. Two, and only two, accounts
exist (Mack and Amy) — there is no HTTP path anywhere in this codebase that
creates a user or sets a password; that's done once via
`scripts/set_password.py`, run locally on the household's own machine, so a
password never has to be typed into (or seen by) anything else.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from .config import Settings

_hasher = PasswordHasher()  # library defaults: time_cost=3, memory_cost=64MiB, parallelism=4


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    """True if the hash was made with older/weaker parameters than current
    defaults — checked on every successful login so a parameter bump later
    doesn't require a manual re-hash pass."""
    return _hasher.check_needs_rehash(password_hash)


# --------------------------------------------------------------------------
# JWT access tokens — short-lived, stateless, never revoked individually
# (revocation is via the refresh token; a leaked access token just expires).
# --------------------------------------------------------------------------
_JWT_ALGORITHM = "HS256"


def create_access_token(user_id: int, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_JWT_ALGORITHM)


class TokenError(RuntimeError):
    pass


def decode_access_token(token: str, settings: Settings) -> int:
    """Returns the user id, or raises TokenError on invalid/expired."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_JWT_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    try:
        return int(payload["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError("malformed token payload") from exc


# --------------------------------------------------------------------------
# Refresh tokens — opaque random strings, stored as a sha256 hash
# (`refresh_tokens.token_hash`) so a DB read alone never yields a usable
# token. Rotated on every use (docs §3): the presented token is marked
# revoked and a new one issued in the same request.
# --------------------------------------------------------------------------
def generate_refresh_token() -> tuple[str, str]:
    """Returns (raw_token_to_send_to_client, sha256_hash_to_store)."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
