"""Letterboxd credentials — docs/API.md §Phase 2 "Letterboxd credentials"
and docs/phases/PHASE-2-credentials.md.

INTERIM SHAPE (documented deviation, see final report): there is no real
per-user login/session yet (Phase 4 auth doesn't exist), so there's no
"signed-in user" the request is naturally scoped to. Every endpoint here
therefore accepts an explicit ``user`` identifier (query param on GET/DELETE,
body field on PUT) to say which of the two household users (1 or 2) the
credential belongs to. Phase 4 replaces this explicit field with "derived
from the access token" and adds the 403 "acting as the other user" check
that API.md describes — that check needs a real signed-in identity to
compare against, which does not exist yet, so it is not enforced here.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import MishkaHTTPException
from ..models import AppSetting, User
from ..secretstore import LETTERBOXD_SERVICE, delete_session_blob, get_secret_store

router = APIRouter(prefix="/letterboxd/credentials", tags=["credentials"])

ACK_KEY_TEMPLATE = "letterboxd_automation_ack_user_{user_id}"


def _get_user_or_404(session: Session, user_id: int) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise MishkaHTTPException(
            status_code=404, detail=f"User {user_id} not found", code="not_found"
        )
    if not user.letterboxd_username:
        raise MishkaHTTPException(
            status_code=422,
            detail=f"User {user_id} has no letterboxd_username configured",
            code="no_letterboxd_username",
        )
    return user


def _ack_setting(session: Session, user_id: int) -> AppSetting | None:
    key = ACK_KEY_TEMPLATE.format(user_id=user_id)
    return session.get(AppSetting, key)


@router.get("/status")
async def credentials_status(
    request: Request,
    user: int = Query(..., description="Interim: 1 or 2 (see module docstring)"),
    session: Session = Depends(get_session),
) -> dict:
    settings = request.app.state.settings
    db_user = _get_user_or_404(session, user)
    store = get_secret_store(settings)

    secret = store.get(LETTERBOXD_SERVICE, db_user.letterboxd_username)
    ack = _ack_setting(session, user)

    return {
        "configured": secret is not None,
        "tos_acknowledged": ack is not None,
        "backend": settings.secret_backend,
    }


class PutCredentialsBody(BaseModel):
    user: int
    password: str
    acknowledge_tos: bool = False


@router.put("")
async def put_credentials(
    body: PutCredentialsBody,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    settings = request.app.state.settings
    db_user = _get_user_or_404(session, body.user)
    store = get_secret_store(settings)

    ack = _ack_setting(session, body.user)
    is_first_save = store.get(LETTERBOXD_SERVICE, db_user.letterboxd_username) is None

    if is_first_save and not body.acknowledge_tos and ack is None:
        raise MishkaHTTPException(
            status_code=403,
            detail="Automation risk must be acknowledged first",
            code="tos_not_acknowledged",
        )

    store.set(LETTERBOXD_SERVICE, db_user.letterboxd_username, body.password)

    if body.acknowledge_tos and ack is None:
        key = ACK_KEY_TEMPLATE.format(user_id=body.user)
        now_iso = datetime.now(timezone.utc).isoformat()
        session.add(AppSetting(key=key, value_json=json.dumps(now_iso)))
        session.commit()
    else:
        # Rotate (PHASE-2-credentials.md §5): overwriting the password
        # invalidates any existing session — the next automation run must
        # log in fresh.
        session.commit()

    delete_session_blob(body.user)

    return {"configured": True}


@router.delete("")
async def delete_credentials(
    request: Request,
    user: int = Query(..., description="Interim: 1 or 2 (see module docstring)"),
    session: Session = Depends(get_session),
) -> dict:
    settings = request.app.state.settings
    db_user = _get_user_or_404(session, user)
    store = get_secret_store(settings)

    store.delete(LETTERBOXD_SERVICE, db_user.letterboxd_username)
    delete_session_blob(user)

    return {"configured": False}
