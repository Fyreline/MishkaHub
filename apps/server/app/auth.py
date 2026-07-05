"""Per-user JWT auth guard (docs/phases/PHASE-4-accounts-feedback.md).

Every router except `/api/health` and the login/refresh endpoints in
`routers/auth.py` requires a valid `Authorization: Bearer <access token>`
JWT, verified here. There is no separate "admin" or "any valid token"
mode — a valid token always resolves to exactly one of the two seeded
household accounts (`current_user` below), which is stashed on
`request.state.user_id` for handlers that want to know who's asking.

Raises via ``MishkaHTTPException`` (see ``app/errors.py``) so the response
body carries the uniform ``{"detail": ..., "code": ...}`` shape documented
in docs/API.md §0.
"""
from __future__ import annotations

from fastapi import Request

from .errors import MishkaHTTPException
from .security import TokenError, decode_access_token


def current_user(request: Request) -> int:
    """Verify the bearer JWT and return the authenticated user's id.

    Also sets ``request.state.user_id`` so downstream handlers can read it
    without re-decoding the token.
    """
    settings = request.app.state.settings

    header = request.headers.get("Authorization")
    if not header or not header.startswith("Bearer "):
        raise MishkaHTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header",
            code="unauthorized",
        )

    token = header.removeprefix("Bearer ").strip()
    try:
        user_id = decode_access_token(token, settings)
    except TokenError as exc:
        raise MishkaHTTPException(
            status_code=401,
            detail=f"Invalid or expired token: {exc}",
            code="unauthorized",
        ) from exc

    request.state.user_id = user_id
    return user_id
