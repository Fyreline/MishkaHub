"""Availability refresh — populates the `availability` cache table from TMDB.

docs/DATA_MODEL.md §2 (availability table): "TTL policy: rows older than 7
days are refreshed lazily on read and by the nightly job." This module is
the thing that actually performs that refresh — nothing previously wrote
into `availability`, so GET /films/{id}/availability always returned
`offers: []` (see app/routers/films.py get_film_availability).

Callers own the transaction, same convention as importers/merge.py: the
functions here call session.commit() themselves though, since a refresh is
a single self-contained unit of work (delete-then-reinsert for one
(film_id, region) pair), not part of a larger multi-row import transaction.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .clients.tmdb import TMDBClient
from .models import Availability

logger = logging.getLogger(__name__)

# The kinds a TMDB /movie/{id}/watch/providers region block may carry.
# Matches the Availability.kind CHECK constraint exactly.
_KINDS = ("flatrate", "free", "ads", "rent", "buy")

# Module-level cache of the provider catalogue, keyed by region, so repeated
# availability refreshes/serves don't all hit TMDB for the catalogue too.
# This data (provider id -> name/logo) changes rarely, so a simple
# process-lifetime cache (no TTL) is enough — worst case it's a few hours
# stale until the server restarts, which is harmless for names/logos.
_catalogue_cache: dict[str, dict[int, dict]] = {}


async def get_provider_catalogue(tmdb_client: TMDBClient, region: str) -> dict[int, dict]:
    """Return {provider_id: {"name":..., "logo_path":...}} for the region,
    fetching once per region per process and caching thereafter.
    """
    cached = _catalogue_cache.get(region)
    if cached is not None:
        return cached

    raw = await tmdb_client.watch_providers_catalogue(region=region)
    resolved = {
        p["provider_id"]: {"name": p.get("provider_name"), "logo_path": p.get("logo_path")}
        for p in raw
        if p.get("provider_id") is not None
    }
    _catalogue_cache[region] = resolved
    return resolved


async def refresh_film_availability(
    session: Session, tmdb_client: TMDBClient, film_id: int, region: str
) -> None:
    """Fetch this film's current watch-provider offers from TMDB and replace
    the cached `availability` rows for (film_id, region) with them.

    Delete-then-reinsert for the (film_id, region) pair in one transaction:
    the simplest correct way to make sure kinds/providers that disappeared
    since the last fetch (e.g. left Netflix) don't linger forever.
    """
    logger.info("refreshing availability: film_id=%s region=%s", film_id, region)

    data = await tmdb_client.watch_providers(film_id)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    new_rows: list[Availability] = []
    for kind in _KINDS:
        for entry in data.get(kind, None) or []:
            provider_id = entry.get("provider_id")
            if provider_id is None:
                continue
            new_rows.append(
                Availability(
                    film_id=film_id,
                    provider_id=provider_id,
                    kind=kind,
                    region=region,
                    fetched_at=now,
                )
            )

    session.execute(
        delete(Availability).where(
            Availability.film_id == film_id, Availability.region == region
        )
    )
    for row in new_rows:
        session.add(row)
    session.commit()


def needs_refresh(session: Session, film_id: int, region: str, *, ttl_days: int = 7) -> bool:
    """True if there are zero cached rows for this film+region, or the
    newest row's fetched_at is older than ttl_days (docs/DATA_MODEL.md §2
    TTL policy).
    """
    newest_fetched_at = session.scalar(
        select(Availability.fetched_at)
        .where(Availability.film_id == film_id, Availability.region == region)
        .order_by(Availability.fetched_at.desc())
        .limit(1)
    )
    if newest_fetched_at is None:
        return True

    fetched_dt = datetime.strptime(newest_fetched_at, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc
    )
    age = datetime.now(timezone.utc) - fetched_dt
    return age.days >= ttl_days
