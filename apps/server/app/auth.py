"""Interim bearer-token auth guard (docs/API.md closing note).

Full login (JWT access/refresh tokens, per-user identity) ships in Phase 4.
For Phases 2-3 the whole API is guarded by a single static bearer token
(`MISHKA_DEV_TOKEN`) checked against every request except `/api/health`.
This is a FastAPI dependency, applied per-router via
``app.include_router(..., dependencies=[Depends(require_auth)])`` in
``app/main.py`` — see that module for wiring.

Raises via ``MishkaHTTPException`` (see ``app/errors.py``) so the response
body carries the uniform ``{"detail": ..., "code": ...}`` shape documented
in docs/API.md §0.
"""
from __future__ import annotations

from fastapi import Request

from .errors import MishkaHTTPException


def require_auth(request: Request) -> None:
    """Raise 401 unless `Authorization: Bearer <MISHKA_DEV_TOKEN>` matches."""
    settings = request.app.state.settings
    expected = settings.dev_token

    header = request.headers.get("Authorization")
    if not header or not header.startswith("Bearer "):
        raise MishkaHTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header",
            code="unauthorized",
        )

    token = header.removeprefix("Bearer ").strip()
    if not expected or token != expected:
        raise MishkaHTTPException(
            status_code=401,
            detail="Invalid token",
            code="unauthorized",
        )
