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
import re
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .clients.tmdb import TMDBClient
from .models import Availability

logger = logging.getLogger(__name__)

# The kinds a TMDB /movie/{id}/watch/providers region block may carry.
# Matches the Availability.kind CHECK constraint exactly.
_KINDS = ("flatrate", "free", "ads", "rent", "buy")

# Lower = better. Used by dedupe_offers_by_provider to pick which row wins
# when the same provider (or the same real-world brand) appears more than
# once with differing kinds.
_KIND_PRIORITY = {"flatrate": 0, "free": 0, "ads": 1}

# TMDB/JustWatch sometimes gives a brand's ad-supported tier its own,
# DIFFERENT provider_id (e.g. "Netflix" id=8 vs "Netflix Standard with Ads"
# id=1796; "Amazon Prime Video" id=9 vs "Amazon Prime Video with Ads"
# id=2100). Stripping this suffix lets both collapse to the same brand key.
_AD_TIER_SUFFIX_RE = re.compile(
    r"\s*(free|standard|basic)?\s*with ads\s*$", re.IGNORECASE
)


def _dedupe_key(offer: dict) -> str | int:
    name = offer.get("provider_name")
    if name:
        return _AD_TIER_SUFFIX_RE.sub("", name).strip().casefold()
    return offer["provider_id"]


def dedupe_offers_by_provider(offers: list[dict]) -> list[dict]:
    """Collapse duplicate rows for the same real-world streaming service.

    Two distinct patterns both surface as "the same service listed twice" in
    "Where to watch" / recommendation offers, so both are handled here:
    (1) the same provider_id appearing under more than one `kind` (e.g. a
    flatrate slot and a separate ad-supported slot for one provider id), and
    (2) a brand's ad-supported tier having its own, different provider_id
    (see `_AD_TIER_SUFFIX_RE`). Keeps whichever row has the best kind
    (flatrate/free over ads) — a `subscribed: True` row wins regardless of
    kind, since that's the entry that actually matters to the household.
    Ties keep the first occurrence. Expects each offer dict to have at least
    "provider_id" and "kind"; "provider_name"/"subscribed" are used when
    present but not required (falls back to provider_id-only dedup).
    """
    best: dict[str | int, dict] = {}
    for offer in offers:
        key = _dedupe_key(offer)
        existing = best.get(key)
        if existing is None:
            best[key] = offer
            continue
        if existing.get("subscribed") and not offer.get("subscribed"):
            continue
        if offer.get("subscribed") and not existing.get("subscribed"):
            best[key] = offer
            continue
        if _KIND_PRIORITY.get(offer["kind"], 99) < _KIND_PRIORITY.get(existing["kind"], 99):
            best[key] = offer
    return list(best.values())

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
