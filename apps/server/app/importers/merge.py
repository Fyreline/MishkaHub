"""Dedup & cross-source merge — docs/phases/PHASE-2-letterboxd-import.md §8.

Each function here does exactly one entity's insert-or-update logic and
commits nothing itself: callers own the transaction (per §6 "each file is
one DB transaction"). Call ``session.commit()`` (or ``rollback()``) at the
call site once all rows for a file have been staged.

Dedup keys, verbatim from §8:
    watch (CSV/scrape) : (user_id, film_id, watched_date) unique, NULL -> ''
                         insert-or-skip; a dated row upgrades a dateless row
    watch (RSS)        : letterboxd_guid unique; insert-or-skip
    rating             : PK (user_id, film_id); upsert, newest wins
    like               : PK (user_id, film_id); insert-or-skip
    review             : (user_id, film_id, watched_date) + exact-text
                         equality; insert-or-skip; changed text -> new row
    whole ZIP          : run-level export_sha256 (handled by the import run
                         orchestrator, not here)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Film, Like, Rating, Review, UnmatchedImport, Watch


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def upsert_film(session: Session, tmdb_payload: dict[str, Any]) -> Film:
    """Insert-or-update a films row by films.id (TMDB id).

    ``tmdb_payload`` is either a full TMDB /movie/{id} response (has genres,
    credits, etc. when append_to_response was used) or the lighter dict
    tmdb_match.py builds when a film was already in our films table
    (matched_via="existing_film") — both shapes carry at least id/title.
    """
    film_id = tmdb_payload["id"]
    film = session.get(Film, film_id)

    release_date = tmdb_payload.get("release_date") or None
    release_year = None
    if release_date and len(release_date) >= 4 and release_date[:4].isdigit():
        release_year = int(release_date[:4])

    imdb_id = tmdb_payload.get("imdb_id")

    # metadata_json: store the full payload we were given so Phase 3 features
    # have everything (credits/keywords/release_dates when present). Only
    # replace it when we actually have a richer payload (i.e. it carries more
    # than the bare minimum fields) so an "existing_film" shortcut lookup
    # doesn't clobber previously hydrated metadata with a thin stand-in.
    is_thin_payload = set(tmdb_payload.keys()) <= {
        "id",
        "title",
        "original_title",
        "release_date",
        "overview",
        "poster_path",
        "backdrop_path",
        "popularity",
        "vote_average",
        "vote_count",
        "imdb_id",
    }

    if film is None:
        film = Film(
            id=film_id,
            imdb_id=imdb_id,
            title=tmdb_payload.get("title") or tmdb_payload.get("original_title") or "",
            original_title=tmdb_payload.get("original_title"),
            release_year=release_year,
            release_date=release_date,
            runtime_min=tmdb_payload.get("runtime"),
            original_language=tmdb_payload.get("original_language"),
            overview=tmdb_payload.get("overview"),
            poster_path=tmdb_payload.get("poster_path"),
            backdrop_path=tmdb_payload.get("backdrop_path"),
            popularity=tmdb_payload.get("popularity"),
            vote_average=tmdb_payload.get("vote_average"),
            vote_count=tmdb_payload.get("vote_count"),
            metadata_json=None if is_thin_payload else json.dumps(tmdb_payload),
            tmdb_fetched_at=None if is_thin_payload else _now_iso(),
        )
        session.add(film)
    else:
        film.title = tmdb_payload.get("title") or film.title
        film.original_title = tmdb_payload.get("original_title") or film.original_title
        film.release_year = release_year if release_year is not None else film.release_year
        film.release_date = release_date or film.release_date
        film.runtime_min = tmdb_payload.get("runtime") or film.runtime_min
        film.original_language = tmdb_payload.get("original_language") or film.original_language
        film.overview = tmdb_payload.get("overview") or film.overview
        film.poster_path = tmdb_payload.get("poster_path") or film.poster_path
        film.backdrop_path = tmdb_payload.get("backdrop_path") or film.backdrop_path
        film.popularity = tmdb_payload.get("popularity") or film.popularity
        film.vote_average = tmdb_payload.get("vote_average") or film.vote_average
        film.vote_count = tmdb_payload.get("vote_count") or film.vote_count
        film.imdb_id = imdb_id or film.imdb_id
        if not is_thin_payload:
            film.metadata_json = json.dumps(tmdb_payload)
            film.tmdb_fetched_at = _now_iso()

    return film


def upsert_watch(
    session: Session,
    user_id: int,
    film_id: int,
    watched_date: str | None,
    rewatch: bool,
    tags: list[str] | None,
    source: str,
    letterboxd_guid: str | None = None,
    letterboxd_uri: str | None = None,
) -> Watch:
    """Insert-or-skip a watch, respecting the §8 dedup rules:

    - (user_id, film_id, watched_date) is the unique key (NULL date -> '').
    - A dated row upgrades a dateless row for the same film if one exists
      (fills date/rewatch/tags) rather than creating a duplicate.
    - A dateless claim (e.g. watched.csv, or a scraped films-grid row with no
      diary date) is redundant once ANY row already exists for that film —
      dated or not — since a coarser "watched at some point" claim adds no
      information over what we already know. Without this, syncing a second
      source after the first (e.g. RSS then a later CSV export, or scrape
      then RSS) created a spurious second row for the same viewing purely
      because one arrived dateless and the other dated (real bug found and
      fixed 2026-07-04 — see the cleanup note in this module's history).
    - RSS rows dedup additionally by letterboxd_guid (unique column).
    """
    tags_json = json.dumps(tags) if tags else None

    if letterboxd_guid:
        existing_by_guid = session.scalars(
            select(Watch).where(Watch.letterboxd_guid == letterboxd_guid)
        ).first()
        if existing_by_guid is not None:
            return existing_by_guid

    exact_match = session.scalars(
        select(Watch).where(
            Watch.user_id == user_id,
            Watch.film_id == film_id,
            Watch.watched_date == watched_date,
        )
    ).first()
    if exact_match is not None:
        return exact_match

    if watched_date is not None:
        # A dated row can upgrade an existing dateless row for the same film.
        dateless = session.scalars(
            select(Watch).where(
                Watch.user_id == user_id,
                Watch.film_id == film_id,
                Watch.watched_date.is_(None),
            )
        ).first()
        if dateless is not None:
            dateless.watched_date = watched_date
            dateless.rewatch = int(rewatch)
            if tags_json is not None:
                dateless.tags_json = tags_json
            if letterboxd_guid:
                dateless.letterboxd_guid = letterboxd_guid
            if letterboxd_uri:
                dateless.letterboxd_uri = letterboxd_uri
            dateless.source = source
            return dateless
    else:
        # Incoming claim is dateless. If ANY row already exists for this
        # (user, film) — dated or not — that already establishes "watched";
        # this coarser claim can't tell us which viewing it refers to, so
        # skip rather than create a redundant duplicate alongside real data.
        any_existing = session.scalars(
            select(Watch).where(Watch.user_id == user_id, Watch.film_id == film_id)
        ).first()
        if any_existing is not None:
            return any_existing

    watch = Watch(
        user_id=user_id,
        film_id=film_id,
        watched_date=watched_date,
        rewatch=int(rewatch),
        tags_json=tags_json,
        source=source,
        letterboxd_guid=letterboxd_guid,
        letterboxd_uri=letterboxd_uri,
    )
    session.add(watch)
    return watch


def upsert_rating(
    session: Session,
    user_id: int,
    film_id: int,
    rating: float,
    source: str,
    rated_at: str | None = None,
) -> Rating:
    """Upsert by PK (user_id, film_id) — newest information wins, with one
    precedence rule: an in-app rating (the user editing it directly in the
    UI) is never silently overwritten by a later Letterboxd sync. Letterboxd
    syncs still update the `letterboxd_rating` shadow column unconditionally,
    so "what does Letterboxd currently say" is never lost even while an
    in-app override is in effect — see docs/phases/PHASE-2-letterboxd-import.md
    §8 and the `Rating.letterboxd_rating` column docstring.
    """
    is_letterboxd_source = source != "in-app"
    existing = session.get(Rating, (user_id, film_id))

    if existing is None:
        row = Rating(
            user_id=user_id,
            film_id=film_id,
            rating=rating,
            source=source,
            rated_at=rated_at,
            letterboxd_rating=rating if is_letterboxd_source else None,
        )
        session.add(row)
        return row

    if is_letterboxd_source:
        existing.letterboxd_rating = rating
        if existing.source != "in-app":
            existing.rating = rating
            existing.source = source
            if rated_at is not None:
                existing.rated_at = rated_at
        # else: an in-app override is in effect — `rating`/`source` stay put,
        # only the shadow value above tracks Letterboxd's current state.
    else:
        existing.rating = rating
        existing.source = source
        if rated_at is not None:
            existing.rated_at = rated_at
    return existing


def upsert_like(session: Session, user_id: int, film_id: int, source: str) -> Like:
    """Insert-or-skip by PK (user_id, film_id)."""
    existing = session.get(Like, (user_id, film_id))
    if existing is not None:
        return existing
    like = Like(user_id=user_id, film_id=film_id, source=source)
    session.add(like)
    return like


def delete_like(session: Session, user_id: int, film_id: int) -> bool:
    """Delete the like row for (user_id, film_id) if present.

    Not part of the §8 import dedup rules above (imports only ever
    insert-or-skip likes) — this exists for the in-app "unlike" action,
    which is the one place a like can be removed. Returns True if a row was
    deleted, False if there was nothing to delete (no-op).
    """
    existing = session.get(Like, (user_id, film_id))
    if existing is None:
        return False
    session.delete(existing)
    return True


def upsert_review(
    session: Session,
    user_id: int,
    film_id: int,
    review_text: str,
    source: str,
    watched_date: str | None = None,
    contains_spoilers: bool = False,
) -> Review:
    """Insert-or-skip on (user_id, film_id, watched_date) + exact-text
    equality; changed text for the same key creates a new row (history kept).
    """
    candidates = session.scalars(
        select(Review).where(
            Review.user_id == user_id,
            Review.film_id == film_id,
            Review.watched_date == watched_date,
        )
    ).all()
    for existing in candidates:
        if existing.review_text == review_text:
            return existing

    review = Review(
        user_id=user_id,
        film_id=film_id,
        review_text=review_text,
        contains_spoilers=int(contains_spoilers),
        watched_date=watched_date,
        source=source,
    )
    session.add(review)
    return review


def queue_unmatched(
    session: Session,
    user_id: int,
    source_file: str,
    raw_row: dict[str, Any],
    name: str | None,
    year: int | None,
    letterboxd_uri: str | None = None,
) -> UnmatchedImport:
    """Insert into unmatched_imports for later manual resolution (§7 UI queue)."""
    row = UnmatchedImport(
        user_id=user_id,
        source_file=source_file,
        raw_row_json=json.dumps(raw_row),
        name=name,
        year=year,
        letterboxd_uri=letterboxd_uri,
    )
    session.add(row)
    return row
