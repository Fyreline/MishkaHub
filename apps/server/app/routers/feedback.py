"""Feedback & active learning — docs/API.md §Phase 4 "Feedback & active learning".

PUT    /api/films/{id}/rating   upsert rating (also logs feedback event)
DELETE /api/films/{id}/rating   remove rating
PUT    /api/films/{id}/like     like/unlike
POST   /api/films/{id}/seen     create a watch (diary entry)
POST   /api/feedback            generic feedback event

INTERIM SHAPE (documented deviation, same pattern as app/routers/credentials.py):
there is no real per-user login/session yet (Phase 4 auth doesn't exist), so
there's no "signed-in user" a request is naturally scoped to. Every endpoint
here therefore accepts an explicit ``user`` identifier (query param on
DELETE, body field on PUT/POST) to say which of the two household users (1
or 2) is acting. Phase 4 replaces this with "derived from the access token".

Design choices worth flagging explicitly:
- DELETE /rating deletes the whole Rating row (including the
  letterboxd_rating shadow column) rather than preserving it — this matches
  docs/API.md's plain "remove rating" wording. If a later Letterboxd sync
  runs after this, upsert_rating's existing-None branch will recreate the
  row from scratch, which is correct behaviour either way.
- DELETE /rating is idempotent: deleting a rating that doesn't exist returns
  200 (not 404), since "no rating exists" and "rating removed" converge to
  the same end state and there's no real reason for a client to treat a
  double-delete as an error here.
- context="poster_wall" is used for all of the rating/like/seen events
  logged by this module, since these all originate from the film detail
  drawer (the Cat-alogue's poster-wall detail overlay) — the closest fit
  among the feedback_events.context CHECK values
  ('rec','search','poster_wall','prompt','import'). POST /api/feedback
  (the generic endpoint) takes context from the caller instead, since that
  one is meant to be fired from anywhere (e.g. "rec" for a recommendation
  card's not-interested button).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import MishkaHTTPException
from ..importers.merge import delete_like, upsert_like, upsert_rating, upsert_watch
from ..models import FeedbackEvent, Like, Rating, Watch

router = APIRouter(prefix="/films", tags=["feedback"])

EVENT_TYPES = {
    "rating", "like", "seen", "not_interested", "snooze", "clicked",
    "watchlisted", "prompt_answer",
}
CONTEXTS = {"rec", "search", "poster_wall", "prompt", "import"}


def _validate_rating_step(rating: float) -> float:
    # rating must be 0.5-5.0 in steps of 0.5 (half-star increments, matching
    # Letterboxd's own rating granularity). Pydantic's ge/le handles the
    # range; this handles the step, which Pydantic can't express directly.
    doubled = rating * 2
    if abs(doubled - round(doubled)) > 1e-9:
        raise ValueError("rating must be in 0.5 steps (e.g. 0.5, 1.0, 1.5, ... 5.0)")
    return rating


class PutRatingBody(BaseModel):
    rating: float
    user: int

    @field_validator("rating")
    @classmethod
    def _check_rating(cls, v: float) -> float:
        if not (0.5 <= v <= 5.0):
            raise ValueError("rating must be between 0.5 and 5.0")
        return _validate_rating_step(v)


@router.put("/{tmdb_id}/rating")
async def put_rating(
    tmdb_id: int,
    body: PutRatingBody,
    session: Session = Depends(get_session),
) -> dict:
    upsert_rating(session, body.user, tmdb_id, body.rating, source="in-app")
    session.commit()

    session.add(
        FeedbackEvent(
            user_id=body.user,
            film_id=tmdb_id,
            event_type="rating",
            value=body.rating,
            context="poster_wall",
        )
    )
    session.commit()

    row = session.get(Rating, (body.user, tmdb_id))
    return {
        "rating": row.rating if row else None,
        "letterboxd_rating": row.letterboxd_rating if row else None,
        "source": row.source if row else None,
    }


@router.delete("/{tmdb_id}/rating")
async def delete_rating(
    tmdb_id: int,
    user: int = Query(..., description="Interim: 1 or 2 (see module docstring)"),
    session: Session = Depends(get_session),
) -> dict:
    # Idempotent: whether or not a row existed, the end state ("no rating for
    # this user+film") is the same, so this always returns 200 rather than
    # 404ing on a double-delete. The whole row (including the
    # letterboxd_rating shadow value) is removed — see module docstring.
    row = session.get(Rating, (user, tmdb_id))
    existed = row is not None
    if row is not None:
        session.delete(row)
        session.commit()

    session.add(
        FeedbackEvent(
            user_id=user,
            film_id=tmdb_id,
            event_type="rating",
            value=None,
            context="poster_wall",
        )
    )
    session.commit()

    return {"deleted": existed, "rating": None, "letterboxd_rating": None}


class PutLikeBody(BaseModel):
    liked: bool
    user: int


@router.put("/{tmdb_id}/like")
async def put_like(
    tmdb_id: int,
    body: PutLikeBody,
    session: Session = Depends(get_session),
) -> dict:
    if body.liked:
        upsert_like(session, body.user, tmdb_id, source="in-app")
    else:
        delete_like(session, body.user, tmdb_id)
    session.commit()

    session.add(
        FeedbackEvent(
            user_id=body.user,
            film_id=tmdb_id,
            event_type="like",
            value=1.0 if body.liked else 0.0,
            context="poster_wall",
        )
    )
    session.commit()

    like_row = session.get(Like, (body.user, tmdb_id))
    return {"liked": like_row is not None}


class PostSeenBody(BaseModel):
    watched_date: str | None = None
    rewatch: bool = False
    user: int


@router.post("/{tmdb_id}/seen")
async def post_seen(
    tmdb_id: int,
    body: PostSeenBody,
    session: Session = Depends(get_session),
) -> dict:
    watch = upsert_watch(
        session,
        body.user,
        tmdb_id,
        watched_date=body.watched_date,
        rewatch=body.rewatch,
        tags=None,
        source="in-app",
    )
    session.commit()

    session.add(
        FeedbackEvent(
            user_id=body.user,
            film_id=tmdb_id,
            event_type="seen",
            value=None,
            context="poster_wall",
        )
    )
    session.commit()

    return {
        "id": watch.id,
        "watched_date": watch.watched_date,
        "rewatch": bool(watch.rewatch),
        "source": watch.source,
    }


generic_router = APIRouter(tags=["feedback"])


class PostFeedbackBody(BaseModel):
    film_id: int
    event_type: str
    context: str | None = None
    value: float | None = None
    user: int


@generic_router.post("/feedback")
async def post_feedback(
    body: PostFeedbackBody,
    session: Session = Depends(get_session),
) -> dict:
    if body.event_type not in EVENT_TYPES:
        raise MishkaHTTPException(
            status_code=422,
            detail=f"Unknown event_type '{body.event_type}'. Must be one of: "
            f"{', '.join(sorted(EVENT_TYPES))}",
            code="invalid_event_type",
        )
    if body.context is not None and body.context not in CONTEXTS:
        raise MishkaHTTPException(
            status_code=422,
            detail=f"Unknown context '{body.context}'. Must be one of: "
            f"{', '.join(sorted(CONTEXTS))}",
            code="invalid_context",
        )

    event = FeedbackEvent(
        user_id=body.user,
        film_id=body.film_id,
        event_type=body.event_type,
        value=body.value,
        context=body.context,
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    return {
        "id": event.id,
        "user_id": event.user_id,
        "film_id": event.film_id,
        "event_type": event.event_type,
        "value": event.value,
        "context": event.context,
        "created_at": event.created_at,
    }
