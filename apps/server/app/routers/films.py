"""Films endpoints — docs/API.md §Phase 2 "Films" / §Phase 3.

GET /api/films              poster-wall listing with filters
GET /api/films/lucky        "Feeling Lucky": weighted-random staleness pick
                            (docs/phases/PHASE-3-recommender.md §0) — MUST be
                            registered before /{tmdb_id} below, else FastAPI's
                            registration-order matching lets {tmdb_id} swallow
                            the literal "lucky" path segment.
GET /api/films/{tmdb_id}    full detail (metadata + both users' state) —
                            auto-hydrates from TMDB if not yet in our library
GET /api/films/{tmdb_id}/availability  GB streaming-only availability
                            (flatrate/free/ads; rent/buy always excluded),
                            cached (TTL 7d), `subscribed_only` query param
                            (default true)
GET /api/films/{tmdb_id}/similar       content-similarity recommender v0
                            (docs/phases/PHASE-3-recommender.md §1-2) —
                            auto-hydrates from TMDB if not yet in our library
"""
from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..availability import (
    dedupe_offers_by_provider,
    get_provider_catalogue,
    needs_refresh,
    refresh_film_availability,
)
from ..clients.tmdb import TMDBClient, TMDBError
from ..db import get_session
from ..errors import MishkaHTTPException
from ..importers.merge import upsert_film
from ..models import Availability, Film, Like, Rating, Watch
from ..recommender.features import similar_films
from ..recommender.lucky import eligible_film_ids_for_user, pick_lucky_film
from ..recommender.vibes import ALL_VIBE_TAGS, vibe_tags

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/films", tags=["films"])

ATTRIBUTION = "Streaming availability by JustWatch"

SortKey = Literal["watched_desc", "rating_desc", "title", "year"]


def _user_state_block(
    session: Session, user_id: int, film_id: int
) -> dict:
    rating_row = session.get(Rating, (user_id, film_id))
    like_row = session.get(Like, (user_id, film_id))
    watch_count = session.scalar(
        select(func.count()).select_from(Watch).where(
            Watch.user_id == user_id, Watch.film_id == film_id
        )
    ) or 0
    last_watched = session.scalar(
        select(func.max(Watch.watched_date)).where(
            Watch.user_id == user_id, Watch.film_id == film_id
        )
    )
    return {
        "rating": rating_row.rating if rating_row else None,
        "letterboxd_rating": rating_row.letterboxd_rating if rating_row else None,
        "liked": like_row is not None,
        "watch_count": watch_count,
        "last_watched": last_watched,
    }


async def _get_or_hydrate_film(
    tmdb_id: int, request: Request, session: Session
) -> Film:
    """Look up a film locally; if it's not in our library yet, fetch it from
    TMDB (credits/keywords/release_dates appended, same as Phase 2 hydration)
    and upsert it via the shared importers/merge.py logic, then commit.

    Used by both GET /films/{tmdb_id} and GET /films/{tmdb_id}/similar so
    "search any film -> see full detail / see similar" works even for films
    the household has never watched or previously searched.
    """
    film = session.get(Film, tmdb_id)
    if film is not None:
        return film

    tmdb: TMDBClient = request.app.state.tmdb
    try:
        payload = await tmdb.movie(tmdb_id, append="credits,keywords,release_dates")
    except TMDBError as exc:
        raise MishkaHTTPException(
            status_code=404,
            detail=f"Film {tmdb_id} not found locally and TMDB lookup failed: {exc}",
            code="not_found",
        ) from exc

    film = upsert_film(session, payload)
    session.commit()
    return film


@router.get("")
async def list_films(
    request: Request,
    user: int | None = Query(default=None, description="1 or 2 — filter to this user's activity"),
    seen: bool | None = Query(default=None),
    seen_by: Literal["both", "either"] | None = Query(
        default=None,
        description=(
            "Household-wide watched filter, independent of `user`/`seen`: "
            "'both' = both users (1 and 2) have watched it, 'either' = at "
            "least one has. Exists because the Cat-alogue's Both/Either "
            "toggle has no single-user equivalent — without this the "
            "frontend had to fetch a page unfiltered and narrow it "
            "client-side, which desynced pagination/totals from what was "
            "actually shown (2026-07-04 fix)."
        ),
    ),
    rated: bool | None = Query(default=None),
    liked: bool | None = Query(default=None),
    min_rating: float | None = Query(
        default=None,
        ge=0.5,
        le=5.0,
        description="Household-wide: either user's rating >= this value.",
    ),
    year_from: int | None = Query(default=None),
    year_to: int | None = Query(default=None),
    genre: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Title substring search"),
    sort: SortKey = Query(default="title"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    stmt = select(Film)

    if year_from is not None:
        stmt = stmt.where(Film.release_year >= year_from)
    if year_to is not None:
        stmt = stmt.where(Film.release_year <= year_to)
    if q:
        stmt = stmt.where(Film.title.ilike(f"%{q}%"))
    if genre:
        # metadata_json holds the raw TMDB payload (genres list) when hydrated;
        # no dedicated genre column/table exists yet, so this is a best-effort
        # substring match against the stored JSON blob.
        stmt = stmt.where(Film.metadata_json.ilike(f"%{genre}%"))

    if seen_by is not None:
        watched_1 = select(Watch.film_id).where(Watch.user_id == 1)
        watched_2 = select(Watch.film_id).where(Watch.user_id == 2)
        if seen_by == "both":
            stmt = stmt.where(Film.id.in_(watched_1)).where(Film.id.in_(watched_2))
        else:
            stmt = stmt.where(or_(Film.id.in_(watched_1), Film.id.in_(watched_2)))

    if min_rating is not None:
        rated_1 = select(Rating.film_id).where(Rating.user_id == 1, Rating.rating >= min_rating)
        rated_2 = select(Rating.film_id).where(Rating.user_id == 2, Rating.rating >= min_rating)
        stmt = stmt.where(or_(Film.id.in_(rated_1), Film.id.in_(rated_2)))

    if seen is not None or rated is not None or liked is not None:
        if user is None:
            raise MishkaHTTPException(
                status_code=422,
                detail="seen/rated/liked filters require a user query param",
                code="user_required",
            )
        if seen is not None:
            watched_subq = select(Watch.film_id).where(Watch.user_id == user)
            stmt = stmt.where(Film.id.in_(watched_subq)) if seen else stmt.where(
                Film.id.not_in(watched_subq)
            )
        if rated is not None:
            rated_subq = select(Rating.film_id).where(Rating.user_id == user)
            stmt = stmt.where(Film.id.in_(rated_subq)) if rated else stmt.where(
                Film.id.not_in(rated_subq)
            )
        if liked is not None:
            liked_subq = select(Like.film_id).where(Like.user_id == user)
            stmt = stmt.where(Film.id.in_(liked_subq)) if liked else stmt.where(
                Film.id.not_in(liked_subq)
            )

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    if sort == "title":
        stmt = stmt.order_by(Film.title.asc())
    elif sort == "year":
        stmt = stmt.order_by(Film.release_year.desc().nullslast())
    elif sort == "rating_desc":
        # No direct rating column on Film; fall back to vote_average (TMDB
        # community rating) since per-user rating sort needs a join we do in
        # Python below when a user is specified, else this is the best proxy.
        stmt = stmt.order_by(Film.vote_average.desc().nullslast())
    elif sort == "watched_desc":
        stmt = stmt.order_by(Film.id.desc())

    stmt = stmt.limit(limit).offset(offset)
    films = session.scalars(stmt).all()

    items = []
    for film in films:
        my = _user_state_block(session, user, film.id) if user else {
            "rating": None,
            "liked": False,
            "watch_count": 0,
            "last_watched": None,
        }
        partner_id = None
        if user is not None:
            partner_id = 2 if user == 1 else 1
        partner = (
            _user_state_block(session, partner_id, film.id)
            if partner_id is not None
            else {"rating": None, "liked": False, "watch_count": 0, "last_watched": None}
        )
        items.append(
            {
                "id": film.id,
                "title": film.title,
                "year": film.release_year,
                "poster": TMDBClient.poster_url(film.poster_path),
                "my": my,
                "partner": partner,
            }
        )

    if sort == "watched_desc" and user is not None:
        items.sort(key=lambda it: it["my"]["last_watched"] or "", reverse=True)
    elif sort == "rating_desc" and user is not None:
        items.sort(key=lambda it: (it["my"]["rating"] is None, -(it["my"]["rating"] or 0)))

    return {"total": total, "items": items}


@router.get("/lucky")
async def get_lucky_film(
    user: int = Query(..., description="1 or 2 — eligibility/staleness is per-person"),
    genre: str | None = Query(default=None),
    max_runtime: int | None = Query(default=None, ge=1, description="Max runtime_min, inclusive"),
    vibe: str | None = Query(
        default=None,
        description=f"One of: {', '.join(ALL_VIBE_TAGS)}",
    ),
    session: Session = Depends(get_session),
) -> dict:
    """Feeling Lucky: weighted-random pick of ONE eligible film for `user`.

    Registered ABOVE `GET /{tmdb_id}` (see module note below) so FastAPI's
    registration-order route matching doesn't let `{tmdb_id}` swallow the
    literal `/lucky` path. docs/API.md §Phase 3 / PHASE-3-recommender.md §0.
    """
    if vibe is not None and vibe not in ALL_VIBE_TAGS:
        raise MishkaHTTPException(
            status_code=422,
            detail=f"Unknown vibe '{vibe}'. Must be one of: {', '.join(ALL_VIBE_TAGS)}",
            code="invalid_vibe",
        )

    result = pick_lucky_film(
        session, user, genre=genre, max_runtime=max_runtime, vibe=vibe
    )
    if result is None:
        raise MishkaHTTPException(
            status_code=503,
            detail="No eligible films match these filters",
            code="lucky_pool_empty",
        )
    return result


@router.get("/{tmdb_id}")
async def get_film(
    tmdb_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    # Auto-hydrate from TMDB if this film isn't in our library yet, so
    # "click a search result -> see full detail" works for ANY searched
    # film, not just ones already imported/watched. Only 404s now if TMDB
    # itself doesn't recognise the id (see _get_or_hydrate_film).
    film = await _get_or_hydrate_film(tmdb_id, request, session)

    genres: list[str] = []
    if film.metadata_json:
        try:
            genres = [g["name"] for g in json.loads(film.metadata_json).get("genres", [])]
        except (json.JSONDecodeError, KeyError, TypeError):
            genres = []

    # Provenance: most recent watch row for this film, across either user.
    source_row = session.scalar(
        select(Watch.source)
        .where(Watch.film_id == film.id)
        .order_by(Watch.created_at.desc())
        .limit(1)
    )

    return {
        "id": film.id,
        "imdb_id": film.imdb_id,
        "title": film.title,
        "original_title": film.original_title,
        "year": film.release_year,
        "release_date": film.release_date,
        "runtime_min": film.runtime_min,
        "original_language": film.original_language,
        "overview": film.overview,
        "poster": TMDBClient.poster_url(film.poster_path),
        "backdrop": TMDBClient.poster_url(film.backdrop_path, size="large"),
        "popularity": film.popularity,
        "vote_average": film.vote_average,
        "vote_count": film.vote_count,
        "letterboxd_slug": film.letterboxd_slug,
        "letterboxd_uri": film.letterboxd_uri,
        "genres": genres,
        "source": source_row,
        "my": _user_state_block(session, 1, film.id),
        "partner": _user_state_block(session, 2, film.id),
    }


@router.get("/{tmdb_id}/similar")
async def get_similar_films(
    tmdb_id: int,
    request: Request,
    limit: int = Query(default=12, ge=1, le=50),
    max_runtime: int | None = Query(default=None, ge=1, description="Max runtime_min, inclusive"),
    vibe: str | None = Query(
        default=None,
        description=f"One of: {', '.join(ALL_VIBE_TAGS)}",
    ),
    session: Session = Depends(get_session),
) -> dict:
    """Recommender v0 (docs/phases/PHASE-3-recommender.md §1-2): pure
    content-similarity between films via cosine similarity over TMDB-derived
    feature vectors (genres, keywords, cast, director, decade, runtime,
    language). NOT personalized recommendations — this compares films to
    films, not to a user's taste model (that's §4/§5, a later phase).

    Auto-hydrates the seed film from TMDB if it isn't in our library yet.
    """
    if vibe is not None and vibe not in ALL_VIBE_TAGS:
        raise MishkaHTTPException(
            status_code=422,
            detail=f"Unknown vibe '{vibe}'. Must be one of: {', '.join(ALL_VIBE_TAGS)}",
            code="invalid_vibe",
        )

    seed_film = await _get_or_hydrate_film(tmdb_id, request, session)

    # Corpus = every film with hydrated metadata, but filtered to the
    # ELIGIBLE pool: the whole point of "similar" is to surface things the
    # household hasn't seen (or hasn't seen in a while), not to echo back
    # films they've both watched recently. Eligibility is the SAME rule
    # /lucky enforces (never watched, OR most-recent dated watch >=365d ago;
    # undated-only history is deliberately withheld — see lucky.py).
    #
    # /similar is household-wide (no `user` param), so we keep a film if it's
    # eligible for AT LEAST ONE of the two users (ids 1 and 2): don't suggest
    # something BOTH people have watched recently, but don't hide something
    # only one of them has seen recently either.
    hydrated_films = session.scalars(
        select(Film).where(Film.metadata_json.is_not(None))
    ).all()
    hydrated_ids = {f.id for f in hydrated_films}

    eligible_ids = eligible_film_ids_for_user(
        session, 1, hydrated_ids
    ) | eligible_film_ids_for_user(session, 2, hydrated_ids)

    # Keep the eligible corpus, and ALWAYS include the seed film itself even
    # if it's ineligible (you can look up "similar to" something you've
    # already watched); similar_films() excludes the seed from its own results.
    all_films = [f for f in hydrated_films if f.id in eligible_ids]
    if seed_film.id not in {f.id for f in all_films}:
        all_films = list(all_films) + [seed_film]

    items = similar_films(
        tmdb_id, all_films, limit=limit, max_runtime=max_runtime, vibe=vibe
    )

    films_by_id = {f.id: f for f in all_films}

    def _film_card(film: Film) -> dict:
        return {
            "id": film.id,
            "title": film.title,
            "year": film.release_year,
            "poster": TMDBClient.poster_url(film.poster_path),
            "runtime_min": film.runtime_min,
        }

    result_items = []
    for item in items:
        film = films_by_id[item["film_id"]]
        film_meta = json.loads(film.metadata_json) if film.metadata_json else {}
        result_items.append(
            {
                "film": _film_card(film),
                "score": round(item["score"], 4),
                "vibe_tags": vibe_tags(film_meta, runtime_min=film.runtime_min),
                "why": item["why"],
            }
        )

    return {
        "seed": {
            "id": seed_film.id,
            "title": seed_film.title,
            "year": seed_film.release_year,
            "poster": TMDBClient.poster_url(seed_film.poster_path),
        },
        "items": result_items,
    }


# Kinds that are ever eligible to appear in the /availability response.
# rent/buy are cached in the `availability` table (app/availability.py caches
# ALL kinds — that's correct/useful for any future purchase-listing feature)
# but the household explicitly never wants purchase/rental listings surfaced
# in "Where to watch" — this is a permanent response-layer filter, not a
# toggle. See docs/API.md §Phase 2 Films.
_STREAMING_KINDS = ("flatrate", "free", "ads")


@router.get("/{tmdb_id}/availability")
async def get_film_availability(
    tmdb_id: int,
    request: Request,
    subscribed_only: bool = Query(
        default=True,
        description=(
            "When true (default), offers are filtered to only providers the "
            "household actively subscribes to (active Subscription rows). "
            "When false, all streaming-kind offers are returned (rent/buy "
            "are still always excluded), each still carrying a `subscribed` "
            "flag."
        ),
    ),
    session: Session = Depends(get_session),
) -> dict:
    settings = request.app.state.settings
    tmdb: TMDBClient = request.app.state.tmdb
    film = session.get(Film, tmdb_id)
    if film is None:
        raise MishkaHTTPException(
            status_code=404,
            detail=f"Film {tmdb_id} not found",
            code="not_found",
        )

    # Lazy on-demand refresh per docs/DATA_MODEL.md §2 TTL policy: "rows
    # older than 7 days are refreshed lazily on read". Nothing else in the
    # codebase ever wrote into `availability`, so without this the endpoint
    # always returned offers: [].
    if needs_refresh(session, tmdb_id, settings.region):
        try:
            await refresh_film_availability(session, tmdb, tmdb_id, settings.region)
        except TMDBError:
            # TMDB unreachable/unconfigured — fall back to serving whatever
            # (possibly empty/stale) rows are already cached rather than
            # 500ing the whole endpoint.
            logger.warning(
                "availability refresh failed for film_id=%s region=%s",
                tmdb_id,
                settings.region,
                exc_info=True,
            )

    rows = session.scalars(
        select(Availability).where(
            Availability.film_id == tmdb_id,
            Availability.region == settings.region,
            Availability.kind.in_(_STREAMING_KINDS),
        )
    ).all()

    # Household subscription provider ids, to flag "subscribed" per offer.
    from ..models import Subscription

    subscribed_ids = set(
        session.scalars(
            select(Subscription.provider_id).where(Subscription.active == 1)
        ).all()
    )

    if subscribed_only:
        rows = [row for row in rows if row.provider_id in subscribed_ids]

    catalogue = await get_provider_catalogue(tmdb, settings.region)

    offers = [
        {
            "provider_id": row.provider_id,
            "provider_name": catalogue.get(row.provider_id, {}).get("name"),
            "kind": row.kind,
            "logo": TMDBClient.poster_url(
                catalogue.get(row.provider_id, {}).get("logo_path"), size="small"
            ),
            "subscribed": row.provider_id in subscribed_ids,
        }
        for row in rows
    ]
    # A single real-world service can carry more than one `kind` row for the
    # same film (e.g. a flatrate slot and a separate ad-supported slot) —
    # collapse those down to one entry so "Where to watch" never shows the
    # same provider name twice.
    offers = dedupe_offers_by_provider(offers)

    # fetched_at should reflect the cache freshness regardless of filtering,
    # so look it up from the full (unfiltered-by-kind) cache rather than
    # `rows` (which may now be empty after subscribed_only/kind filtering).
    fetched_at = session.scalar(
        select(Availability.fetched_at)
        .where(Availability.film_id == tmdb_id, Availability.region == settings.region)
        .order_by(Availability.fetched_at.desc())
        .limit(1)
    )

    return {
        "film_id": tmdb_id,
        "region": settings.region,
        "fetched_at": fetched_at,
        "attribution": ATTRIBUTION,
        "offers": offers,
        "tmdb_watch_page": f"https://www.themoviedb.org/movie/{tmdb_id}/watch?locale={settings.region}",
    }
