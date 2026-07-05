"""Login/session endpoints — docs/phases/PHASE-4-accounts-feedback.md §1-3.

POST /api/auth/login    email + password -> access + refresh token pair
POST /api/auth/refresh  refresh token -> new access + refresh token pair (rotated)
POST /api/auth/logout   refresh token -> revoked
GET  /api/auth/me       the authenticated user's own profile

No registration endpoint exists anywhere in this codebase, on purpose —
the two accounts are seeded once via `scripts/set_password.py`, run
locally on the household's own machine.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_session
from ..errors import MishkaHTTPException
from ..models import RefreshToken, User
from ..security import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)

router = APIRouter(tags=["auth"])

# --- simple in-process login rate limit (docs §3: "5 failures / 15 min per
# IP"). In-process is fine for a single-uvicorn-process household app; a
# restart clears it, which is an acceptable trade-off here. ---
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_FAILURES = 5
_login_failures: dict[str, deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    # cloudflared terminates TLS and proxies to loopback-only uvicorn, so
    # X-Forwarded-For is trustworthy here (no untrusted intermediary can
    # reach uvicorn directly to spoof it) — see DEPLOYMENT.md §3.
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    window = _login_failures[ip]
    while window and now - window[0] > _LOGIN_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= _LOGIN_MAX_FAILURES:
        raise MishkaHTTPException(
            status_code=429,
            detail="Too many failed login attempts — try again later.",
            code="rate_limited",
        )


def _record_failure(ip: str) -> None:
    _login_failures[ip].append(time.monotonic())


def _record_success(ip: str) -> None:
    _login_failures.pop(ip, None)


class LoginBody(BaseModel):
    email: str
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int
    user: dict


def _issue_tokens(session: Session, user: User, request: Request) -> TokenPair:
    settings = request.app.state.settings
    access = create_access_token(user.id, settings)
    raw_refresh, refresh_hash = generate_refresh_token()
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_ttl_days)
    ).strftime("%Y-%m-%d %H:%M:%S")
    session.add(RefreshToken(user_id=user.id, token_hash=refresh_hash, expires_at=expires_at))
    session.commit()
    return TokenPair(
        access_token=access,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
        user={"id": user.id, "email": user.email, "display_name": user.display_name},
    )


@router.post("/auth/login")
async def login(body: LoginBody, request: Request, session: Session = Depends(get_session)) -> TokenPair:
    ip = _client_ip(request)
    _check_rate_limit(ip)

    settings = request.app.state.settings
    if not settings.auth_configured:
        raise MishkaHTTPException(
            status_code=503,
            detail="Login is not configured on this server (MISHKA_JWT_SECRET unset).",
            code="auth_not_configured",
        )

    user = session.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not user.password_hash or not verify_password(body.password, user.password_hash):
        _record_failure(ip)
        raise MishkaHTTPException(status_code=401, detail="Incorrect email or password", code="invalid_credentials")

    _record_success(ip)
    if needs_rehash(user.password_hash):
        from ..security import hash_password

        user.password_hash = hash_password(body.password)
        session.commit()

    return _issue_tokens(session, user, request)


class RefreshBody(BaseModel):
    refresh_token: str


@router.post("/auth/refresh")
async def refresh(
    body: RefreshBody, request: Request, session: Session = Depends(get_session)
) -> TokenPair:
    token_hash = hash_refresh_token(body.refresh_token)
    row = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))

    if row is None:
        raise MishkaHTTPException(status_code=401, detail="Invalid refresh token", code="invalid_refresh_token")

    if row.revoked:
        # Reuse of an already-rotated-away token: classic theft tripwire —
        # revoke every refresh token this user holds, forcing a fresh login
        # everywhere (docs §3 "reuse-detection").
        for other in session.scalars(
            select(RefreshToken).where(RefreshToken.user_id == row.user_id, RefreshToken.revoked == 0)
        ):
            other.revoked = 1
        session.commit()
        raise MishkaHTTPException(
            status_code=401,
            detail="Refresh token already used — all sessions revoked, please log in again.",
            code="refresh_reuse_detected",
        )

    expires_at = datetime.strptime(row.expires_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        raise MishkaHTTPException(status_code=401, detail="Refresh token expired", code="invalid_refresh_token")

    user = session.get(User, row.user_id)
    if user is None:
        raise MishkaHTTPException(status_code=401, detail="Invalid refresh token", code="invalid_refresh_token")

    row.revoked = 1  # rotate: this token is now spent
    session.commit()
    return _issue_tokens(session, user, request)


@router.post("/auth/logout")
async def logout(body: RefreshBody, session: Session = Depends(get_session)) -> dict:
    token_hash = hash_refresh_token(body.refresh_token)
    row = session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if row is not None:
        row.revoked = 1
        session.commit()
    return {"logged_out": True}


@router.get("/auth/me")
async def me(user_id: int = Depends(current_user), session: Session = Depends(get_session)) -> dict:
    user = session.get(User, user_id)
    if user is None:
        raise MishkaHTTPException(status_code=404, detail="User not found", code="not_found")
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "letterboxd_username": user.letterboxd_username,
    }
