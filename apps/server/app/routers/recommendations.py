"""Personalised recommender endpoints — docs/API.md §Phase 3, PHASE-3-recommender.md §7.

GET  /api/recommendations                 ranked, availability-filtered recs
GET  /api/recommendations/{film_id}/why   score-component breakdown for a film
POST /api/model/retrain                    candidate-gen + refit taste models
GET  /api/model/status                     active model version + real counts

Computed synchronously per request (this pass skips §6's nightly cache-table
write — recompute-on-request is fast enough at ~1k films, matching v0's
per-call precedent). See app/recommender/pipeline.py for the pipeline.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..availability import dedupe_offers_by_provider, get_provider_catalogue
from ..clients.tmdb import TMDBClient, TMDBError
from ..db import get_session
from ..errors import MishkaHTTPException
from ..models import Film, ModelArtifact, Rating, Watch
from ..recommender.artifacts import active_taste_artifact
from ..recommender.pipeline import recommend, retrain

logger = logging.getLogger(__name__)

router = APIRouter(tags=["recommendations"])

ATTRIBUTION = "Streaming availability by JustWatch"

Profile = Literal["me", "partner", "together"]


def _parse_providers(providers: str | None) -> list[int] | None:
    if not providers:
        return None
    out: list[int] = []
    for part in providers.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out or None


async def _resolve_offer_names(
    request: Request, items: list[dict]
) -> None:
    """Fill provider_name/logo on each item's offers from the cached catalogue.
    Mutates items in place. Best-effort — leaves names null if TMDB unreachable.

    Also re-dedupes by provider now that names are available: pipeline.py's
    `_availability_boost_map` only dedupes by provider_id (names aren't known
    yet at that point), which misses the case where a brand's ad-tier has a
    DIFFERENT provider_id (e.g. "Netflix" vs "Netflix Standard with Ads") —
    that pattern can only be caught once `provider_name` is filled in.
    """
    tmdb: TMDBClient = request.app.state.tmdb
    region = request.app.state.settings.region
    try:
        catalogue = await get_provider_catalogue(tmdb, region)
    except TMDBError:
        catalogue = {}
    for item in items:
        for offer in item.get("providers", []):
            info = catalogue.get(offer.get("provider_id"), {})
            offer["provider_name"] = info.get("name")
            offer["logo"] = TMDBClient.poster_url(info.get("logo_path"), size="small")
        item["providers"] = dedupe_offers_by_provider(item.get("providers", []))


@router.get("/recommendations")
async def get_recommendations(
    request: Request,
    profile: Profile = Query(default="me"),
    providers: str | None = Query(default=None, description="CSV of TMDB provider ids to narrow below the household set"),
    include_unavailable: bool = Query(default=False),
    novelty: float | None = Query(default=None, ge=0.0, le=1.0),
    genre: str | None = Query(default=None),
    max_runtime: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    tmdb: TMDBClient = request.app.state.tmdb

    artifact = active_taste_artifact(session)
    model_version = artifact.version if artifact else "in-process"

    try:
        result = await recommend(
            session, tmdb,
            profile=profile,
            limit=limit,
            offset=offset,
            include_unavailable=include_unavailable,
            novelty=novelty,
            genre=genre,
            max_runtime=max_runtime,
            providers=_parse_providers(providers),
            model_version=model_version,
        )
    except ValueError as exc:
        raise MishkaHTTPException(status_code=422, detail=str(exc), code="invalid_profile") from exc

    await _resolve_offer_names(request, result.items)

    return {
        "profile": profile,
        "model_version": result.model_version,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attribution": ATTRIBUTION,
        "items": result.items,
    }


@router.get("/recommendations/{film_id}/why")
async def get_recommendation_why(
    film_id: int,
    request: Request,
    profile: Profile = Query(default="me"),
    include_unavailable: bool = Query(default=False),
    session: Session = Depends(get_session),
) -> dict:
    """Score-component breakdown for one film in a profile's ranked results.

    Recomputes the profile's scoring and returns the breakdown for `film_id`.
    404 if the film isn't in the profile's (post-eligibility/availability)
    candidate set.
    """
    tmdb: TMDBClient = request.app.state.tmdb
    artifact = active_taste_artifact(session)
    model_version = artifact.version if artifact else "in-process"

    try:
        result = await recommend(
            session, tmdb,
            profile=profile,
            limit=100000,  # score everything so `film_id` is present if eligible
            offset=0,
            include_unavailable=include_unavailable,
            model_version=model_version,
        )
    except ValueError as exc:
        raise MishkaHTTPException(status_code=422, detail=str(exc), code="invalid_profile") from exc

    c = result.scored_by_id.get(film_id)
    if c is None:
        raise MishkaHTTPException(
            status_code=404,
            detail=f"Film {film_id} is not in the '{profile}' candidate set "
                   "(seen recently, unavailable, or filtered out)",
            code="not_in_profile",
        )

    film = session.get(Film, film_id)
    why = {
        "content_similarity": round(c.taste, 4),
        "quality_prior": round(c.quality_prior, 4),
        "novelty": round(c.novelty, 4),
        "availability_boost": round(c.availability_boost, 4),
    }
    if profile == "together":
        why["together"] = {
            "user_1": round(c.user_scores.get(1, 0.0), 4),
            "user_2": round(c.user_scores.get(2, 0.0), 4),
            "blend": "0.7*min+0.3*mean",
        }
    # The single-user score is a weighted sum of components; expose the weights
    # so the breakdown is auditable (sums to score within rounding).
    return {
        "film_id": film_id,
        "title": film.title if film else None,
        "profile": profile,
        "model_version": model_version,
        "score": round(c.score, 4),
        "weights": {
            "content_similarity": 0.55,
            "quality_prior": 0.20,
            "novelty": 0.15,
            "availability_boost": 0.10,
        },
        "why": why,
        "providers": result.offers_by_id.get(film_id, []),
    }


@router.post("/model/retrain")
async def post_model_retrain(
    request: Request,
    skip_candidates: bool = Query(
        default=False,
        description="Skip the live TMDB candidate-generation sweep and only refit the taste models",
    ),
    session: Session = Depends(get_session),
) -> dict:
    """Force a retrain: candidate generation (§3) + refit taste models (§4) +
    persist artefact (§6, scoped). Synchronous recompute-and-respond (not an
    async job) — fine at this corpus size per the scope-down.
    """
    tmdb: TMDBClient = request.app.state.tmdb
    report = await retrain(session, tmdb, run_candidate_gen=not skip_candidates)

    cand = report.candidate_report
    candidate_summary = None
    if cand is not None:
        candidate_summary = {
            "discover_seen": cand.discover_seen,
            "recs_seen": cand.recs_seen,
            "films_before": cand.films_before,
            "films_after": cand.films_after,
            "newly_inserted": cand.newly_inserted,
            "newly_hydrated": cand.newly_hydrated,
            "hydration_skipped": cand.hydration_skipped,
            "per_strategy": cand.per_strategy,
            "tmdb_calls": cand.tmdb_calls,
            "errors": cand.errors[:10],
        }

    return {
        "status": "ok",
        "model_version": report.version,
        "corpus_size": report.film_count,
        "candidate_generation": candidate_summary,
        "users": {str(uid): s for uid, s in report.user_summaries.items()},
    }


@router.get("/model/status")
async def get_model_status(
    session: Session = Depends(get_session),
) -> dict:
    """Real model status: active version, corpus size, per-user rating counts,
    when last trained. (§8's full eval harness is deferred — see report.)
    """
    artifact = active_taste_artifact(session)

    corpus_size = session.scalar(
        select(func.count()).select_from(Film).where(Film.metadata_json.is_not(None))
    ) or 0
    total_films = session.scalar(select(func.count()).select_from(Film)) or 0

    per_user = {}
    for (uid,) in session.execute(select(Rating.user_id).distinct()).all():
        n_ratings = session.scalar(
            select(func.count()).select_from(Rating).where(Rating.user_id == uid)
        ) or 0
        n_watches = session.scalar(
            select(func.count(func.distinct(Watch.film_id))).where(Watch.user_id == uid)
        ) or 0
        lam = max(0.0, min(0.8, (n_ratings - 30) / 120.0)) if n_ratings >= 30 else 0.0
        per_user[str(uid)] = {
            "n_ratings": n_ratings,
            "distinct_films_watched": n_watches,
            "blend": "ridge+prototype" if n_ratings >= 30 else "prototype-only",
            "lambda": round(lam, 4),
        }

    metrics = None
    if artifact and artifact.metrics_json:
        try:
            metrics = json.loads(artifact.metrics_json)
        except (json.JSONDecodeError, TypeError):
            metrics = None

    return {
        "active_model": {
            "version": artifact.version if artifact else None,
            "kind": artifact.kind if artifact else None,
            "trained_at": artifact.trained_at if artifact else None,
            "path": artifact.path if artifact else None,
        } if artifact else None,
        "trained": artifact is not None,
        "corpus_size_hydrated": corpus_size,
        "total_films": total_films,
        "users": per_user,
        "train_metrics": metrics,
        "note": "Temporal-holdout evaluation (§8) deferred — status reports real counts, not eval metrics.",
    }
