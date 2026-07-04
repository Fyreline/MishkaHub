"""Thin async client for The Movie Database (TMDB).

Provides the three things Mishka Hub needs from TMDB:
  - film search + metadata (genres, cast, keywords) -> features for the recommender
  - poster image URLs
  - UK (region GB) streaming availability, powered by JustWatch

Attribution note: watch-provider data is supplied by JustWatch and must be
attributed as such wherever it is shown.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..config import Settings

TMDB_BASE = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p"

# Friendly names -> TMDB image bucket sizes.
POSTER_SIZES = {"small": "w185", "medium": "w500", "large": "w780", "original": "original"}


class TMDBError(RuntimeError):
    """TMDB returned an error, or the client isn't configured."""


class TMDBClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        headers = {"accept": "application/json"}
        if settings.tmdb_read_token:
            headers["Authorization"] = f"Bearer {settings.tmdb_read_token}"
        self._client = httpx.AsyncClient(base_url=TMDB_BASE, headers=headers, timeout=15.0)

    @property
    def configured(self) -> bool:
        return self._settings.tmdb_configured

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.configured:
            raise TMDBError(
                "TMDB is not configured — set MISHKA_TMDB_READ_TOKEN or MISHKA_TMDB_API_KEY."
            )
        params = dict(params or {})
        # Fall back to v3 query-param auth when no bearer token is present.
        if not self._settings.tmdb_read_token and self._settings.tmdb_api_key:
            params["api_key"] = self._settings.tmdb_api_key
        try:
            resp = await self._client.get(path, params=params)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TMDBError(f"TMDB {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise TMDBError(f"TMDB request failed: {exc}") from exc
        return resp.json()

    async def search_movie(self, query: str, *, page: int = 1) -> dict[str, Any]:
        return await self._get(
            "/search/movie",
            {
                "query": query,
                "page": page,
                "include_adult": "false",
                "language": self._settings.language,
                "region": self._settings.region,
            },
        )

    async def movie(self, movie_id: int, *, append: str = "credits,keywords,release_dates") -> dict[str, Any]:
        return await self._get(
            f"/movie/{movie_id}",
            {"language": self._settings.language, "append_to_response": append},
        )

    async def watch_providers(self, movie_id: int) -> dict[str, Any]:
        """Streaming/rental/buy options for our region only."""
        data = await self._get(f"/movie/{movie_id}/watch/providers")
        return data.get("results", {}).get(self._settings.region, {})

    async def discover_movies(
        self,
        *,
        with_watch_providers: str,
        watch_region: str,
        sort_by: str = "popularity.desc",
        vote_count_gte: int = 50,
        vote_average_gte: float | None = None,
        primary_release_date_gte: str | None = None,
        primary_release_date_lte: str | None = None,
        with_genres: str | None = None,
        page: int = 1,
    ) -> dict[str, Any]:
        """Page through GET /discover/movie — the candidate-pool generator for
        the Phase 3 recommender (docs/phases/PHASE-3-recommender.md §3).

        NEW method, added for §3 candidate generation. TMDBClient previously
        exposed only per-title lookups (search/movie/watch_providers) and the
        flat provider catalogue; nothing here queried /discover. Kept thin and
        parameterised to §3's documented filter set: with_watch_providers
        (pipe-joined OR of provider ids), watch_region, sort_by, vote_count.gte,
        include_adult=false (always).

        The optional filters (``vote_average_gte``, the two
        ``primary_release_date_*`` bounds, ``with_genres``) back the
        multi-strategy corpus expansion in candidates.py — a single
        popularity.desc sweep only reaches ~a few hundred distinct titles, so
        the expansion runs additional acclaimed / recent-release / per-genre
        sweeps to grow the pool into the thousands. All are plain TMDB
        /discover query params; omitting one leaves it out entirely so the
        original popularity.desc call is byte-for-byte unchanged.
        """
        params: dict[str, Any] = {
            "with_watch_providers": with_watch_providers,
            "watch_region": watch_region,
            "sort_by": sort_by,
            "vote_count.gte": vote_count_gte,
            "include_adult": "false",
            "language": self._settings.language,
            "page": page,
        }
        if vote_average_gte is not None:
            params["vote_average.gte"] = vote_average_gte
        if primary_release_date_gte is not None:
            params["primary_release_date.gte"] = primary_release_date_gte
        if primary_release_date_lte is not None:
            params["primary_release_date.lte"] = primary_release_date_lte
        if with_genres is not None:
            params["with_genres"] = with_genres
        return await self._get("/discover/movie", params)

    async def movie_recommendations(self, movie_id: int, *, page: int = 1) -> dict[str, Any]:
        """GET /movie/{id}/recommendations — TMDB's own "similar to this" list.

        NEW method for §3 point 3: pull recommendations for each user's
        top-rated films to catch low-popularity gems that the popularity-sorted
        /discover sweep misses. Returns the raw paginated payload
        ({"results": [...], "total_pages": N, ...}).
        """
        return await self._get(
            f"/movie/{movie_id}/recommendations",
            {"language": self._settings.language, "page": page},
        )

    async def watch_providers_catalogue(self, region: str | None = None) -> list[dict[str, Any]]:
        """Full TMDB/JustWatch movie-provider catalogue for a region.

        Added for GET /api/providers (docs/API.md §Phase 2/6 "Settings &
        admin") — TMDBClient previously only exposed per-movie availability
        (watch_providers above), not the flat catalogue used by provider
        pickers in Settings. Wraps GET /watch/providers/movie.
        """
        data = await self._get(
            "/watch/providers/movie",
            {"watch_region": region or self._settings.region, "language": self._settings.language},
        )
        return data.get("results", [])

    @staticmethod
    def poster_url(path: str | None, size: str = "medium") -> str | None:
        if not path:
            return None
        bucket = POSTER_SIZES.get(size, size)
        return f"{IMAGE_BASE}/{bucket}{path}"
