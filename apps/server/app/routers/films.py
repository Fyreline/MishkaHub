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
GET /api/films/{tmdb_id}/rematch/search   proxy TMDB search for the
                            "fix the match" UI — candidates to re-point a
                            wrongly-matched film to.
POST /api/films/{tmdb_id}/rematch         move all watches/ratings/likes/
                            reviews off a wrongly-matched film onto the
                            correct TMDB id, hydrating the destination first.
                            See _rematch_film's docstring for the full
                            collision/merge policy.
"""
from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
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
from ..importers.merge import upsert_film, upsert_watch
from ..models import Availability, Film, Like, Rating, Review, UnmatchedImport, Watch
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


async def _get_or_hydrate_film_by_id(
    tmdb_id: int, tmdb: TMDBClient, session: Session
) -> Film:
    """Look up a film locally; if it's not in our library yet, fetch it from
    TMDB (credits/keywords/release_dates appended, same as Phase 2 hydration)
    and upsert it via the shared importers/merge.py logic, then commit.

    `request`-free core of `_get_or_hydrate_film` below, so callers that
    already hold a `TMDBClient` directly (e.g. the media scanner,
    importers/media_scan.py) don't need a FastAPI `Request` just to reuse
    this hydration path.
    """
    film = session.get(Film, tmdb_id)
    if film is not None:
        return film

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


async def _get_or_hydrate_film(
    tmdb_id: int, request: Request, session: Session
) -> Film:
    """Used by both GET /films/{tmdb_id} and GET /films/{tmdb_id}/similar so
    "search any film -> see full detail / see similar" works even for films
    the household has never watched or previously searched.
    """
    tmdb: TMDBClient = request.app.state.tmdb
    return await _get_or_hydrate_film_by_id(tmdb_id, tmdb, session)


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

    from ..models import MediaFile

    owned = (
        session.scalar(select(MediaFile.id).where(MediaFile.film_id == tmdb_id)) is not None
    )

    return {
        "film_id": tmdb_id,
        "region": settings.region,
        "fetched_at": fetched_at,
        "attribution": ATTRIBUTION,
        "offers": offers,
        "owned": owned,
        "tmdb_watch_page": f"https://www.themoviedb.org/movie/{tmdb_id}/watch?locale={settings.region}",
    }


# ============================================================================
# Re-match: fix a Letterboxd import that auto-matched to the wrong TMDB film.
#
# tmdb_match.py's scoring auto-accepts a candidate above a score/margin
# threshold; it is occasionally still wrong (e.g. a same-titled, near-zero-
# vote unrelated film outscores the intended one). That module's matching
# logic is intentionally NOT touched here — this is strictly an after-the-
# fact repair tool for the household to point a film's watch/rating/like/
# review history at the correct TMDB id once they notice a bad match.
# ============================================================================


@router.get("/{tmdb_id}/rematch/search")
async def search_rematch_candidates(
    tmdb_id: int,
    request: Request,
    q: str = Query(..., min_length=1, description="Title to search TMDB for"),
    session: Session = Depends(get_session),
) -> dict:
    """Proxy TMDB search for the "fix the match" UI. `tmdb_id` (the film
    currently believed to be wrong) isn't used to filter results — it's only
    in the path for symmetry with POST .../rematch and so the endpoint reads
    naturally as "search for what {tmdb_id} should actually be" — but it IS
    validated to exist locally, so the UI can't be pointed at a bogus film.
    """
    film = session.get(Film, tmdb_id)
    if film is None:
        raise MishkaHTTPException(
            status_code=404,
            detail=f"Film {tmdb_id} not found",
            code="not_found",
        )

    tmdb: TMDBClient = request.app.state.tmdb
    try:
        result = await tmdb.search_movie(q)
    except TMDBError as exc:
        raise MishkaHTTPException(
            status_code=502,
            detail=f"TMDB search failed: {exc}",
            code="tmdb_error",
        ) from exc

    candidates = result.get("results") or []
    items = [
        {
            "id": c["id"],
            "title": c.get("title") or c.get("original_title") or "",
            "year": (
                int(c["release_date"][:4])
                if c.get("release_date") and len(c["release_date"]) >= 4
                and c["release_date"][:4].isdigit()
                else None
            ),
            "poster": TMDBClient.poster_url(c.get("poster_path")),
            "overview": c.get("overview"),
        }
        for c in candidates[:8]
    ]
    return {"query": q, "items": items}


class RematchBody(BaseModel):
    correct_tmdb_id: int


def _other_references_exist(session: Session, film_id: int) -> bool:
    """True if anything besides the four moved tables still points at
    `film_id` after a rematch — i.e. it is NOT safe to delete the row.

    Checked: watches/ratings/likes/reviews (should always be empty by the
    time this is called, since _rematch_film moves/drops every row from
    those four tables first — this is a defensive re-check, not redundant
    trust), unmatched_imports.matched_film_id, availability,
    recommendations_cache, feedback_events, media_files. Anything else with
    a films.id foreign key added later should be added to this list too.
    """
    from ..models import FeedbackEvent, MediaFile, RecommendationCache

    checks = [
        select(Watch.id).where(Watch.film_id == film_id),
        select(Rating.user_id).where(Rating.film_id == film_id),
        select(Like.user_id).where(Like.film_id == film_id),
        select(Review.id).where(Review.film_id == film_id),
        select(UnmatchedImport.id).where(UnmatchedImport.matched_film_id == film_id),
        select(Availability.film_id).where(Availability.film_id == film_id),
        select(RecommendationCache.film_id).where(RecommendationCache.film_id == film_id),
        select(FeedbackEvent.id).where(FeedbackEvent.film_id == film_id),
        select(MediaFile.id).where(MediaFile.film_id == film_id),
    ]
    for stmt in checks:
        if session.scalars(stmt.limit(1)).first() is not None:
            return True
    return False


async def _hydrate_correct_film(
    correct_tmdb_id: int, request: Request, session: Session
) -> Film:
    """Same hydration pattern as _get_or_hydrate_film above (GET
    /films/{tmdb_id} / /similar): look up locally first, else fetch
    credits/keywords/release_dates from TMDB and upsert via the shared
    importers/merge.py logic. Duplicated rather than reusing
    _get_or_hydrate_film directly only because that helper doesn't commit at
    a point compatible with wrapping the whole rematch in one transaction —
    the upsert itself (upsert_film) is the exact same call.
    """
    film = session.get(Film, correct_tmdb_id)
    if film is not None:
        return film

    tmdb: TMDBClient = request.app.state.tmdb
    try:
        payload = await tmdb.movie(correct_tmdb_id, append="credits,keywords,release_dates")
    except TMDBError as exc:
        raise MishkaHTTPException(
            status_code=404,
            detail=f"TMDB lookup for {correct_tmdb_id} failed: {exc}",
            code="not_found",
        ) from exc

    return upsert_film(session, payload)


@router.post("/{tmdb_id}/rematch")
async def rematch_film(
    tmdb_id: int,
    body: RematchBody,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Move every watches/ratings/likes/reviews row off `tmdb_id` (the wrong
    film) onto `body.correct_tmdb_id` (the right one), then delete the wrong
    film's row if nothing else references it.

    Collision / merge policy (both films can independently already have
    activity for the same user — e.g. one row landed on the wrong match via
    a CSV import, another correct row already exists on the right film via a
    later RSS import of the same diary entry):

    - watches: re-inserted via importers/merge.py's `upsert_watch`, the SAME
      dedup path every import uses (dedup by letterboxd_guid, then by exact
      (user, film, date), then dateless-upgrades-to-dated) — never a raw
      UPDATE of film_id, so this can't reintroduce the duplicate-watch bug
      the dedup rules in merge.py exist to prevent. The old watch row is
      deleted once its content has been merged in.
    - ratings / likes (PK is (user_id, film_id), so a straight re-point
      would collide if the destination already has a row for that user):
      if the destination has NO existing row for that user, the source row
      is re-pointed (moved) as-is. If the destination ALREADY has a row for
      that user, the destination's existing row wins and the source row is
      dropped — the destination is treated as the more-trustworthy state
      (typically the correctly-matched film that was ALSO reached
      independently, e.g. via RSS/scrape, and is therefore corroborated by a
      second source) rather than attempting a field-by-field "most recent"
      merge, which risks silently overwriting a real edit with stale
      Letterboxd data. This is a conservative, documented choice, not an
      oversight.
    - reviews (no unique constraint — insert-or-skip by
      (user_id, film_id, watched_date) + exact text equality is merge.py's
      existing convention): if the destination already has a row with
      identical (user, watched_date, text), the source row is dropped as a
      pure duplicate; otherwise the source row is re-pointed (review history
      is kept rather than merged/collapsed, since two differently-worded
      reviews for the same (user, film) are legitimate history, same as
      upsert_review's own "changed text -> new row" rule).

    Wrapped in one transaction: on any failure, session.rollback() undoes
    every partial move so nothing is left half-migrated.
    """
    if tmdb_id == body.correct_tmdb_id:
        raise MishkaHTTPException(
            status_code=422,
            detail="correct_tmdb_id is the same as the current film — nothing to rematch",
            code="noop_rematch",
        )

    wrong_film = session.get(Film, tmdb_id)
    if wrong_film is None:
        raise MishkaHTTPException(
            status_code=404,
            detail=f"Film {tmdb_id} not found",
            code="not_found",
        )

    try:
        correct_film = await _hydrate_correct_film(body.correct_tmdb_id, request, session)

        moved = {"watches": 0, "ratings": 0, "likes": 0, "reviews": 0}
        dropped = {"ratings": 0, "likes": 0, "reviews": 0}

        # --- watches: re-point via the shared dedup path, not a raw UPDATE ---
        old_watches = session.scalars(
            select(Watch).where(Watch.film_id == tmdb_id)
        ).all()
        for w in old_watches:
            tags = json.loads(w.tags_json) if w.tags_json else None
            upsert_watch(
                session,
                w.user_id,
                body.correct_tmdb_id,
                watched_date=w.watched_date,
                rewatch=bool(w.rewatch),
                tags=tags,
                source=w.source,
                letterboxd_guid=w.letterboxd_guid,
                letterboxd_uri=w.letterboxd_uri,
            )
            session.delete(w)
            moved["watches"] += 1
        session.flush()

        # --- ratings: PK collision -> destination wins, source dropped ---
        old_ratings = session.scalars(
            select(Rating).where(Rating.film_id == tmdb_id)
        ).all()
        for r in old_ratings:
            existing = session.get(Rating, (r.user_id, body.correct_tmdb_id))
            if existing is not None:
                session.delete(r)
                dropped["ratings"] += 1
            else:
                r.film_id = body.correct_tmdb_id
                moved["ratings"] += 1
        session.flush()

        # --- likes: PK collision -> destination wins, source dropped ---
        old_likes = session.scalars(
            select(Like).where(Like.film_id == tmdb_id)
        ).all()
        for like in old_likes:
            existing = session.get(Like, (like.user_id, body.correct_tmdb_id))
            if existing is not None:
                session.delete(like)
                dropped["likes"] += 1
            else:
                like.film_id = body.correct_tmdb_id
                moved["likes"] += 1
        session.flush()

        # --- reviews: dup-by-exact-text dropped, else re-pointed (history kept) ---
        old_reviews = session.scalars(
            select(Review).where(Review.film_id == tmdb_id)
        ).all()
        for review in old_reviews:
            dup = session.scalars(
                select(Review).where(
                    Review.user_id == review.user_id,
                    Review.film_id == body.correct_tmdb_id,
                    Review.watched_date == review.watched_date,
                    Review.review_text == review.review_text,
                )
            ).first()
            if dup is not None:
                session.delete(review)
                dropped["reviews"] += 1
            else:
                review.film_id = body.correct_tmdb_id
                moved["reviews"] += 1
        session.flush()

        # --- orphan cleanup: only delete the wrong film if truly unreferenced ---
        deleted_film = False
        if not _other_references_exist(session, tmdb_id):
            session.delete(wrong_film)
            deleted_film = True

        session.commit()
    except Exception:
        session.rollback()
        raise

    return {
        "old_film_id": tmdb_id,
        "new_film_id": body.correct_tmdb_id,
        "new_film": {
            "id": correct_film.id,
            "title": correct_film.title,
            "year": correct_film.release_year,
            "poster": TMDBClient.poster_url(correct_film.poster_path),
        },
        "moved": moved,
        "dropped": dropped,
        "old_film_deleted": deleted_film,
    }
