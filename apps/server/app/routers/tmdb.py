from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..clients.tmdb import TMDBClient, TMDBError

router = APIRouter(prefix="/tmdb", tags=["tmdb"])


def get_tmdb(request: Request) -> TMDBClient:
    return request.app.state.tmdb


@router.get("/search")
async def search(
    q: str = Query(min_length=1, description="Film title to search for"),
    tmdb: TMDBClient = Depends(get_tmdb),
) -> dict:
    """Smoke-test endpoint: search TMDB and return trimmed results with poster URLs."""
    try:
        data = await tmdb.search_movie(q)
    except TMDBError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    results = [
        {
            "id": m["id"],
            "title": m.get("title"),
            "year": (m.get("release_date") or "")[:4] or None,
            "overview": m.get("overview"),
            "poster": TMDBClient.poster_url(m.get("poster_path")),
            "vote_average": m.get("vote_average"),
        }
        for m in data.get("results", [])
    ]
    return {"query": q, "count": len(results), "results": results}
