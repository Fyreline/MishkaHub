"""Recommender pipeline orchestration — ties §3-§7 together.

`retrain()`: run candidate generation (§3), fit both users' taste models (§4),
persist the artefact (§6, scoped). Called by POST /api/model/retrain.

`recommend()`: compute a ranked, availability-filtered, eligibility-respecting,
MMR-diversified recommendation list for a profile (§5, §7). Computed
synchronously per request (this pass skips §6's nightly cache-table write —
recompute-on-request is fast enough at ~1k films, matching v0's per-call
precedent). Reuses the fitted taste models from the active artefact if present,
otherwise fits them in-process.

Availability boost (§5): 1.0 flatrate/free on a subscribed service, 0.6 ads,
0.0 otherwise. Built from the `availability` cache table (populated by
app/availability.py). Candidates with no cached availability get a lazy
refresh, capped at AVAIL_REFRESH_CAP films per request to bound TMDB calls;
beyond the cap they're scored with a neutral availability boost and only shown
under include_unavailable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

import json
from typing import Callable

from ..availability import dedupe_offers_by_provider, needs_refresh, refresh_film_availability
from ..clients.tmdb import TMDBClient, TMDBError
from ..models import Availability, Film, MediaFile, Rating, Subscription, Watch
from .candidates import CandidateGenReport, generate_candidates
from .scoring import (
    ScoredCandidate,
    eligible_film_ids,
    mmr_rerank,
    score_candidates,
)
from .taste import CorpusSpace, UserTasteModel, build_corpus_space, fit_user_taste
from .vibes import vibe_tags

logger = logging.getLogger(__name__)

REGION = "GB"
# Availability-boost weights per kind (§5).
KIND_BOOST = {"flatrate": 1.0, "free": 1.0, "ads": 0.6}
# Cap on lazy availability refreshes per recommend() call (bounds TMDB calls).
AVAIL_REFRESH_CAP = 40


# --------------------------------------------------------------------------
# Corpus + models
# --------------------------------------------------------------------------
def _all_films(session: Session) -> list[Film]:
    return list(session.scalars(select(Film).where(Film.metadata_json.is_not(None))).all())


def build_models(session: Session) -> tuple[CorpusSpace, dict[int, UserTasteModel], float]:
    """Build the shared corpus space + fit both users' taste models.

    Returns (corpus, {user_id: model}, global_mean).
    """
    films = _all_films(session)
    corpus = build_corpus_space(films)

    global_mean = _global_vote_mean(films)

    user_ids = [uid for (uid,) in session.execute(select(Rating.user_id).distinct()).all()]
    if not user_ids:
        user_ids = [1, 2]
    models = {uid: fit_user_taste(session, uid, corpus) for uid in user_ids}
    return corpus, models, global_mean


def _global_vote_mean(films: list[Film]) -> float:
    vals = [f.vote_average for f in films if f.vote_average is not None]
    if not vals:
        return 6.0  # TMDB-ish neutral fallback
    return float(sum(vals) / len(vals))


# --------------------------------------------------------------------------
# Retrain
# --------------------------------------------------------------------------
@dataclass
class RetrainReport:
    candidate_report: CandidateGenReport | None
    version: str
    film_count: int
    user_summaries: dict[int, dict] = field(default_factory=dict)


async def retrain(
    session: Session, tmdb: TMDBClient, *, run_candidate_gen: bool = True
) -> RetrainReport:
    """Full retrain: candidate generation → fit taste models → persist artefact."""
    from .artifacts import save_taste_artifact

    cand_report = None
    if run_candidate_gen:
        cand_report = await generate_candidates(session, tmdb, hydrate=True)

    corpus, models, global_mean = build_models(session)

    metrics = {
        "corpus_size": len(corpus.film_ids),
        "global_vote_mean": round(global_mean, 4),
        "users": {},
    }
    user_summaries: dict[int, dict] = {}
    for uid, m in models.items():
        summary = {
            "n_ratings": m.n_ratings,
            "user_mean": round(m.user_mean, 4),
            "lambda": round(m.lam, 4),
            "has_ridge": m.has_ridge(),
            "ridge_alpha": m.ridge_alpha,
            "blend": "ridge+prototype" if m.has_ridge() else "prototype-only",
        }
        metrics["users"][str(uid)] = summary
        user_summaries[uid] = summary

    version, _ = save_taste_artifact(
        session,
        models=models,
        film_ids=corpus.film_ids,
        global_mean=global_mean,
        metrics=metrics,
    )

    return RetrainReport(
        candidate_report=cand_report,
        version=version,
        film_count=len(corpus.film_ids),
        user_summaries=user_summaries,
    )


# --------------------------------------------------------------------------
# Availability
# --------------------------------------------------------------------------
def _seen_by(session: Session, user_id: int) -> set[int]:
    """All films this user has any watch row for (for the novelty term)."""
    return set(
        session.scalars(select(Watch.film_id).where(Watch.user_id == user_id)).all()
    )


def _subscribed_provider_ids(session: Session) -> set[int]:
    return set(
        session.scalars(
            select(Subscription.provider_id).where(Subscription.active == 1)
        ).all()
    )


def _owned_film_ids(session: Session, film_ids: list[int]) -> set[int]:
    """Films matched to a local media_files row (PHASE-7 §2) — "you already
    own your shelf," so these count as available with no streaming service
    at all, same as the phase doc's "availability boost equal to flatrate."
    """
    if not film_ids:
        return set()
    return set(
        session.scalars(
            select(MediaFile.film_id).where(
                MediaFile.film_id.in_(film_ids), MediaFile.film_id.is_not(None)
            )
        ).all()
    )


async def _availability_boost_map(
    session: Session,
    tmdb: TMDBClient,
    film_ids: list[int],
    subscribed_ids: set[int],
    *,
    allow_refresh: bool = True,
) -> tuple[dict[int, float], dict[int, list[dict]], set[int]]:
    """Return (boost_by_film, offers_by_film, available_film_ids).

    boost = best KIND_BOOST across offers on a *subscribed* provider.
    available_film_ids = films with any subscribed flatrate/free/ads offer.
    Lazily refreshes availability for up to AVAIL_REFRESH_CAP films missing a
    fresh cache row.
    """
    refreshed = 0
    for fid in film_ids:
        if allow_refresh and refreshed < AVAIL_REFRESH_CAP and needs_refresh(
            session, fid, REGION
        ):
            try:
                await refresh_film_availability(session, tmdb, fid, REGION)
                refreshed += 1
            except TMDBError:
                logger.warning("availability refresh failed for %s", fid, exc_info=True)

    rows = session.execute(
        select(Availability.film_id, Availability.provider_id, Availability.kind).where(
            Availability.film_id.in_(film_ids),
            Availability.region == REGION,
            Availability.kind.in_(tuple(KIND_BOOST.keys())),
        )
    ).all()

    boost: dict[int, float] = {}
    offers: dict[int, list[dict]] = {}
    available: set[int] = set()
    for film_id, provider_id, kind in rows:
        if provider_id not in subscribed_ids:
            continue
        b = KIND_BOOST.get(kind, 0.0)
        boost[film_id] = max(boost.get(film_id, 0.0), b)
        offers.setdefault(film_id, []).append(
            {"provider_id": provider_id, "kind": kind}
        )
        available.add(film_id)
    # Same provider can carry more than one kind row for a film (e.g. a
    # flatrate slot and a separate ad-supported slot) — collapse to one
    # entry per provider so the `why`/providers payload never lists the same
    # service twice (see films.py's get_film_availability for the same fix).
    offers = {film_id: dedupe_offers_by_provider(rows) for film_id, rows in offers.items()}
    return boost, offers, available


# --------------------------------------------------------------------------
# Recommend
# --------------------------------------------------------------------------
_PROFILE_USERS = {"me": [1], "partner": [2], "together": [1, 2]}

# Runtime bucket key -> predicate over a non-None runtime_min.
_RUNTIME_BUCKETS: dict[str, Callable[[int], bool]] = {
    "under95": lambda r: r < 95,
    "95to120": lambda r: 95 <= r <= 120,
    "121to180": lambda r: 121 <= r <= 180,
    "over180": lambda r: r > 180,
}


@dataclass
class RecommendResult:
    profile: str
    model_version: str
    items: list[dict]
    scored_by_id: dict[int, ScoredCandidate]
    offers_by_id: dict[int, list[dict]]


async def recommend(
    session: Session,
    tmdb: TMDBClient,
    *,
    profile: str,
    limit: int = 50,
    offset: int = 0,
    include_unavailable: bool = False,
    novelty: float | None = None,
    genres: list[str] | None = None,
    runtime_buckets: list[str] | None = None,
    vibe: str | None = None,
    providers: list[int] | None = None,
    model_version: str = "in-process",
) -> RecommendResult:
    """Compute ranked recommendations for a profile. See module docstring."""
    profile_user_ids = _PROFILE_USERS.get(profile)
    if profile_user_ids is None:
        raise ValueError(f"unknown profile {profile!r}")

    corpus, models, global_mean = build_models(session)

    # Candidate pool = films eligible for EVERY user in the profile.
    ineligible: set[int] = set()
    for uid in profile_user_ids:
        ineligible |= eligible_film_ids(session, uid)

    films_by_id = {f.id: f for f in _all_films(session)}
    candidate_ids = [fid for fid in corpus.film_ids if fid not in ineligible]

    # Request-time narrowing: genres / runtime_buckets / vibe.
    def _passes_filters(f: Film) -> bool:
        if runtime_buckets:
            if f.runtime_min is None:
                return False
            if not any(_RUNTIME_BUCKETS[b](f.runtime_min) for b in runtime_buckets):
                return False
        if genres:
            # AND, not OR: each additional genre narrows the result set (a
            # film must match every selected genre, not just one) — matches
            # the household's stated preference, not the more common
            # "broaden" multi-select convention.
            #
            # Bug fixed here (2026-07-04): this used to substring-match
            # against the ENTIRE raw metadata_json blob, so a film whose
            # keywords/overview/etc. merely happened to contain the word
            # (e.g. Okja's "live action and animation" keyword) would match
            # "Animation" even though its real genre list doesn't include
            # Animation at all — confirmed live (Okja has no Animation genre
            # but matched `?genres=Animation`). Now parses the actual
            # `genres` array and checks real membership.
            if not f.metadata_json:
                return False
            try:
                film_genres = {g["name"].lower() for g in json.loads(f.metadata_json).get("genres", [])}
            except (json.JSONDecodeError, TypeError, AttributeError, KeyError):
                return False
            if not all(g.lower() in film_genres for g in genres):
                return False
        if vibe:
            meta = json.loads(f.metadata_json) if f.metadata_json else {}
            if vibe not in vibe_tags(meta, runtime_min=f.runtime_min):
                return False
        return True

    candidate_films = [
        films_by_id[fid] for fid in candidate_ids
        if fid in films_by_id and _passes_filters(films_by_id[fid])
    ]

    # Availability: narrow providers to the household set ∩ request `providers`.
    subscribed_ids = _subscribed_provider_ids(session)
    if providers:
        subscribed_ids = subscribed_ids & set(providers)

    cand_ids = [f.id for f in candidate_films]
    boost, offers, available = await _availability_boost_map(
        session, tmdb, cand_ids, subscribed_ids, allow_refresh=True
    )

    # Owned films (PHASE-7): count as available regardless of streaming,
    # with a flatrate-equivalent boost — you already own your shelf. Kept
    # out of `offers` (real provider dicts flow through
    # dedupe_offers_by_provider downstream, which requires provider_id/kind)
    # and surfaced instead as a separate `owned` flag per item.
    owned_ids = _owned_film_ids(session, cand_ids)
    for fid in owned_ids:
        boost[fid] = max(boost.get(fid, 0.0), KIND_BOOST["flatrate"])
        available.add(fid)

    if not include_unavailable:
        candidate_films = [f for f in candidate_films if f.id in available]
    else:
        # rent/buy score a low 0.2 boost per §5 when include_unavailable
        for f in candidate_films:
            if f.id not in available:
                boost.setdefault(f.id, 0.2)

    if not candidate_films:
        return RecommendResult(
            profile=profile, model_version=model_version, items=[],
            scored_by_id={}, offers_by_id={},
        )

    seen_by = {uid: _seen_by(session, uid) for uid in profile_user_ids}

    novelty_mult = 1.0
    if novelty is not None:
        # UI 0–1 rescales the novelty weight 0–2× (§5 note).
        novelty_mult = max(0.0, min(2.0, novelty * 2.0))

    scored = score_candidates(
        corpus=corpus,
        candidate_films=candidate_films,
        models=models,
        seen_by=seen_by,
        availability_boost=boost,
        global_mean=global_mean,
        profile_user_ids=profile_user_ids,
        novelty_weight_mult=novelty_mult,
    )

    reranked = mmr_rerank(corpus, scored, limit=offset + limit)
    page = reranked[offset : offset + limit]

    scored_by_id = {c.film_id: c for c in scored}
    items = [
        _item_payload(
            films_by_id[c.film_id], c, offers.get(c.film_id, []), profile_user_ids,
            owned=c.film_id in owned_ids,
        )
        for c in page
    ]
    return RecommendResult(
        profile=profile,
        model_version=model_version,
        items=items,
        scored_by_id=scored_by_id,
        offers_by_id=offers,
    )


def _item_payload(
    film: Film, c: ScoredCandidate, offers: list[dict], profile_user_ids: list[int],
    *, owned: bool = False,
) -> dict:
    from ..clients.tmdb import TMDBClient

    why = {
        "content_similarity": round(c.taste, 4),
        "quality_prior": round(c.quality_prior, 4),
        "novelty": round(c.novelty, 4),
        "availability_boost": round(c.availability_boost, 4),
    }
    if len(profile_user_ids) == 2:
        u1, u2 = profile_user_ids
        why["together"] = {
            f"user_{u1}": round(c.user_scores.get(u1, 0.0), 4),
            f"user_{u2}": round(c.user_scores.get(u2, 0.0), 4),
            "blend": "0.7*min+0.3*mean",
        }
    return {
        "film": {
            "id": film.id,
            "title": film.title,
            "year": film.release_year,
            "poster": TMDBClient.poster_url(film.poster_path),
            "runtime_min": film.runtime_min,
        },
        "score": round(c.score, 4),
        "providers": offers,
        "owned": owned,
        "why": why,
    }
