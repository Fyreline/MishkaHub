""""Coming soon" — docs/phases/PHASE-8-coming-soon.md.

GET /api/upcoming   theatrical releases coming soon in the household's region

Scoped-down v1: TMDB doesn't reliably expose *streaming* arrival dates (see
PHASE-8's tier assessment) — that would need a weekly snapshot-diff job and a
new `coming_soon` table (Tiers 1-3 there), not yet built. This is the
honest subset available today: cinema release dates, straight from TMDB's
own `/movie/upcoming`, clearly labelled as such rather than implying a
streaming date we can't actually promise.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from ..clients.tmdb import TMDBClient, TMDBError
from ..errors import MishkaHTTPException

router = APIRouter(tags=["upcoming"])


@router.get("/upcoming")
async def get_upcoming(
    request: Request,
    region: str | None = Query(default=None),
    page: int = Query(default=1, ge=1, le=10),
) -> dict:
    tmdb: TMDBClient = request.app.state.tmdb
    settings = request.app.state.settings
    try:
        data = await tmdb.upcoming_movies(region=region or settings.region, page=page)
    except TMDBError as exc:
        raise MishkaHTTPException(
            status_code=502, detail=str(exc), code="tmdb_upstream_error"
        ) from exc

    results = [
        {
            "id": m["id"],
            "title": m.get("title"),
            "overview": m.get("overview"),
            "poster": TMDBClient.poster_url(m.get("poster_path")),
            "release_date": m.get("release_date"),
        }
        for m in data.get("results", [])
    ]
    return {
        "region": region or settings.region,
        "page": data.get("page", page),
        "total_pages": data.get("total_pages", 1),
        "results": results,
    }
