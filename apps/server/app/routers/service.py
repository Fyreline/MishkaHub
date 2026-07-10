"""Read-only household activity feed for Sukumo (Mishka's recommendation
sibling app) — GET /api/activity/service.

Auth is deliberately NOT the per-user JWT flow used everywhere else in this
API (see app/auth.py): Sukumo is a separate service, not a household member,
and never holds a household password. Instead it authenticates with a single
static bearer token (``MISHKA_SERVICE_TOKEN`` / ``settings.service_token``),
compared with ``hmac.compare_digest`` to avoid timing attacks. If the token
isn't configured at all, the endpoint answers 503 rather than pretending to
be a working (but unusable) auth gate; if it's configured but the caller's
token is missing/malformed/wrong, it answers 401. Because this endpoint has
its own auth, it is intentionally wired up in app/main.py the same way as
/api/health and /api/auth/* — i.e. WITHOUT the blanket
``Depends(current_user)`` JWT dependency applied to most other routers.

Response shape::

    {
      "recent": [
        {"title": str, "watched_at": str, "poster_url": str | null, "rating": float | null},
        ... up to 10, most-recent-first
      ],
      "watchlist_count": int
    }

``recent`` is a single household-wide "we watched" feed — Mishka is a
2-person shared household app (user ids 1 and 2), and this is not scoped to
either user individually. Rows are ordered by
``COALESCE(watched_date, created_at)`` descending (most watches have a
``watched_date``; a few in-app/edge-case rows only have ``created_at``),
tie-broken by ``Watch.id`` descending. Each row's ``rating`` is that specific
watch's own user's rating of that film, if any — not "either user's rating".

``watchlist_count`` — v1 approximation, documented deviation:
Mishka's schema (docs/DATA_MODEL.md) has no dedicated watchlist table, and no
'unwatchlisted' event type in ``feedback_events.event_type`` CHECK
constraint — so there is no way to record "removed from the watchlist"
today, only "marked to watch" (a 'watchlisted' FeedbackEvent). The closest
useful approximation of "what's still on the watchlist" is: distinct
``film_id``s that have at least one 'watchlisted' FeedbackEvent AND do NOT
yet have any Watch row at all (any user). Once a film is watched by either
user, it drops out of this count — that's the intentional (if imperfect)
proxy for "no longer wanted on the watchlist", since there's currently no
way to distinguish "watched, so implicitly done with it" from "watched, but
still separately wanted again". This is a known v1 scope limitation, not an
oversight; a real watchlist table is the eventual fix.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clients.tmdb import TMDBClient
from ..db import get_session
from ..errors import MishkaHTTPException
from ..models import FeedbackEvent, Film, Rating, Watch

router = APIRouter(tags=["service"])

RECENT_LIMIT = 10


def _require_service_token(request: Request) -> None:
    """Auth dependency for this router only (see module docstring)."""
    settings = request.app.state.settings

    if not settings.service_token:
        raise MishkaHTTPException(
            status_code=503,
            detail="Service token not configured (MISHKA_SERVICE_TOKEN unset)",
            code="service_not_configured",
        )

    header = request.headers.get("Authorization")
    if not header or not header.startswith("Bearer "):
        raise MishkaHTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header",
            code="unauthorized",
        )

    token = header.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(token, settings.service_token):
        raise MishkaHTTPException(
            status_code=401,
            detail="Invalid service token",
            code="unauthorized",
        )


@router.get("/activity/service", dependencies=[Depends(_require_service_token)])
async def get_service_activity(session: Session = Depends(get_session)) -> dict:
    order_key = func.coalesce(Watch.watched_date, Watch.created_at)
    rows = session.execute(
        select(Watch, Film)
        .join(Film, Film.id == Watch.film_id)
        .order_by(order_key.desc(), Watch.id.desc())
        .limit(RECENT_LIMIT)
    ).all()

    recent = []
    for watch, film in rows:
        rating_row = session.get(Rating, (watch.user_id, watch.film_id))
        recent.append(
            {
                "title": film.title,
                "watched_at": watch.watched_date or watch.created_at,
                "poster_url": TMDBClient.poster_url(film.poster_path),
                "rating": rating_row.rating if rating_row else None,
            }
        )

    # watchlist_count: see module docstring for the v1 approximation reasoning.
    not_yet_watched = (
        select(Watch.id).where(Watch.film_id == FeedbackEvent.film_id).exists()
    )
    watchlist_count = session.execute(
        select(func.count(func.distinct(FeedbackEvent.film_id)))
        .where(FeedbackEvent.event_type == "watchlisted")
        .where(~not_yet_watched)
    ).scalar_one()

    return {"recent": recent, "watchlist_count": watchlist_count}
