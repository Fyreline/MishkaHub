"""Feeling Lucky — weighted-random single-film pick.

docs/API.md §Phase 3 `GET /api/films/lucky`; docs/phases/PHASE-3-recommender.md
§0. This is NOT a similarity/personalisation feature — it's a staleness-driven
weighted-random draw over the same local corpus `/similar` uses (films with
non-null ``metadata_json``). No cosine similarity, no FeatureSpace needed;
``vibe_tags()`` is reused for the ``vibe`` filter to stay consistent with
`/similar`'s behaviour.

Eligibility (per user, since "haven't watched" is per-person):
- Never watched by this user (``watch_count == 0``) -> eligible, ``days_since = None``.
- Else: compute days since the MOST RECENT ``watched_date`` for this user+film.
  - If the most recent watch row has ``watched_date IS NULL`` (an undated
    "watched at some point" record with no newer dated watch), we cannot
    compute staleness -> NOT eligible. This is a deliberate edge case: an
    undated watch is real watch history, so "never watched" would be wrong,
    but we also can't safely resurface it as fresh without a date. Documented
    in the API.md/PHASE-3 sync and in the build report.
  - ``days_since < 365`` -> excluded.
  - ``days_since >= 365`` -> eligible.

Weight: never-watched = 1.0; else
``min(1.0, 0.3 + 0.7 * min(days_since - 365, 1460) / 1460)``.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Film, Watch
from .vibes import vibe_tags

if TYPE_CHECKING:
    pass


@dataclass
class _Candidate:
    film: Film
    eligibility: str  # "never_watched" | "stale_rewatch"
    days_since: int | None
    weight: float


def _parse_metadata(film: Film) -> dict:
    if not film.metadata_json:
        return {}
    try:
        return json.loads(film.metadata_json)
    except (json.JSONDecodeError, TypeError):
        return {}


def _lucky_weight(days_since: int | None) -> float:
    if days_since is None:
        return 1.0
    return min(1.0, 0.3 + 0.7 * min(days_since - 365, 1460) / 1460)


def user_film_staleness(session: Session, user_id: int) -> dict[int, int | None]:
    """Per-film eligibility+staleness for ``user_id`` under the household's
    canonical "haven't watched, or it's been a year" rule.

    Returns ``{film_id: days_since}`` for every film this user is ELIGIBLE to
    have (re)surfaced, where:

    - ``days_since is None`` -> never watched by this user (fully novel).
    - ``days_since >= 365``  -> most-recent DATED watch is stale enough to
      resurface (the int is the age in days of that watch).

    Films the user is NOT eligible for are simply absent from the returned
    map. Ineligible means: most-recent dated watch < 365 days ago, OR the
    user's only watch rows for that film are undated (can't compute staleness
    -> deliberately withheld, same edge case documented in this module's
    header and mirrored in scoring.py:eligible_film_ids).

    This is the single source of truth for /lucky's staleness rule, extracted
    so /similar (films.py) can reuse the EXACT same rule without duplicating
    it. ``pick_lucky_film`` below is refactored to consume it, so /lucky's
    observable behaviour is unchanged. (scoring.py/pipeline.py keep their own
    inlined copy — this pass deliberately does not touch the v1 pipeline.)
    """
    today = datetime.now().date()

    by_film: dict[int, list[str | None]] = {}
    rows = session.execute(
        select(Watch.film_id, Watch.watched_date).where(Watch.user_id == user_id)
    ).all()
    for film_id, watched_date in rows:
        by_film.setdefault(film_id, []).append(watched_date)

    # Only WATCHED films appear in `by_film`; we return just the watched-but-
    # stale ones (>=365d) keyed to their age. Never-watched films aren't in
    # this map at all — the caller infers them from the full film universe
    # (a film with no watch rows for this user is eligible, days_since=None).
    eligible: dict[int, int | None] = {}
    for film_id, dates in by_film.items():
        dated = [d for d in dates if d is not None]
        if not dated:
            # undated-only history -> cannot compute staleness -> not eligible
            continue
        try:
            most_recent = max(datetime.strptime(d, "%Y-%m-%d").date() for d in dated)
        except ValueError:
            # malformed date -> treat like "can't compute staleness"
            continue
        days_since = (today - most_recent).days
        if days_since < 365:
            continue  # watched too recently -> not eligible
        eligible[film_id] = days_since

    return eligible


def eligible_film_ids_for_user(
    session: Session, user_id: int, all_film_ids: set[int]
) -> set[int]:
    """Subset of ``all_film_ids`` this user is eligible to have (re)surfaced.

    Eligible = never watched by this user, OR most-recent dated watch >=365d.
    Ineligible = watched <365d ago, OR undated-only watch history. Uses the
    same canonical rule as ``user_film_staleness`` (and thus /lucky).
    """
    # Films this user has ANY watch row for.
    watched = {
        fid
        for (fid,) in session.execute(
            select(Watch.film_id).where(Watch.user_id == user_id).distinct()
        ).all()
    }
    stale_ok = set(user_film_staleness(session, user_id).keys())
    # Never-watched (in universe, no watch rows) OR watched-but-stale.
    return {fid for fid in all_film_ids if fid not in watched or fid in stale_ok}


def pick_lucky_film(
    session: Session,
    user_id: int,
    *,
    genre: str | None = None,
    max_runtime: int | None = None,
    vibe: str | None = None,
    _rng: random.Random | None = None,
) -> dict | None:
    """Weighted-random pick of one eligible film for ``user_id``.

    Returns the response dict shape documented in docs/API.md, or ``None``
    if the eligible+filtered pool is empty (caller turns that into a 503).

    ``_rng`` is an injectable ``random.Random`` for deterministic testing;
    defaults to the module-level ``random`` functions otherwise.
    """
    rng = _rng or random

    # Same corpus /similar uses: every film with hydrated metadata.
    films = session.scalars(select(Film).where(Film.metadata_json.is_not(None))).all()
    if not films:
        return None

    # genre filter: same best-effort substring match against the raw
    # metadata_json blob as list_films() in films.py (no dedicated genre
    # column/table exists yet).
    if genre:
        films = [f for f in films if f.metadata_json and genre.lower() in f.metadata_json.lower()]

    if max_runtime is not None:
        films = [f for f in films if f.runtime_min is not None and f.runtime_min <= max_runtime]

    if vibe is not None:
        filtered = []
        for f in films:
            meta = _parse_metadata(f)
            tags = vibe_tags(meta, runtime_min=f.runtime_min)
            if vibe in tags:
                filtered.append(f)
        films = filtered

    if not films:
        return None

    # Per-user staleness map (the canonical rule, shared with /similar).
    # {film_id: days_since} for watched-but-stale films; films the user has
    # never watched are inferred below (not present in `watched`).
    stale = user_film_staleness(session, user_id)
    watched = {
        fid
        for (fid,) in session.execute(
            select(Watch.film_id).where(Watch.user_id == user_id).distinct()
        ).all()
    }

    candidates: list[_Candidate] = []
    for film in films:
        if film.id not in watched:
            # Never watched by this user -> eligible, fully novel.
            candidates.append(
                _Candidate(film=film, eligibility="never_watched", days_since=None, weight=1.0)
            )
            continue
        days_since = stale.get(film.id)
        if days_since is None:
            # Watched, but not eligible (watched <365d ago, or undated-only
            # history -> can't compute staleness). Hard-excluded.
            continue
        weight = _lucky_weight(days_since)
        candidates.append(
            _Candidate(film=film, eligibility="stale_rewatch", days_since=days_since, weight=weight)
        )

    if not candidates:
        return None

    pool_size = len(candidates)
    picked = rng.choices(candidates, weights=[c.weight for c in candidates], k=1)[0]

    from ..clients.tmdb import TMDBClient

    return {
        "film": {
            "id": picked.film.id,
            "title": picked.film.title,
            "year": picked.film.release_year,
            "poster": TMDBClient.poster_url(picked.film.poster_path),
            "runtime_min": picked.film.runtime_min,
        },
        "eligibility": picked.eligibility,
        "days_since_last_watched": picked.days_since,
        "weight": round(picked.weight, 4),
        "pool_size": pool_size,
    }
