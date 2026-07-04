"""Scoring & ranking — docs/phases/PHASE-3-recommender.md §5.

    score(u,i) = 0.55·taste(u,i)          personal fit (§4)
               + 0.20·quality_prior(i)     Bayesian-smoothed vote mean (§2)
               + 0.15·novelty(u,i)         1 − max cos to films u has seen
               + 0.10·availability_boost(i)

Together blend (least-misery + mean nudge):
    score(together,i) = 0.7·min(s₁,s₂) + 0.3·mean(s₁,s₂)

Diversity (MMR, greedy): re-rank the top-N by
    argmax [ 0.7·score(i) − 0.3·max_{j∈selected} cos(xᵢ,xⱼ) ].

Eligibility — the SAME rule /lucky enforces (task #18, the household's
explicit "haven't watched, or it's been a year" rule): a film is eligible for
a user iff never watched by them OR the most-recent DATED watch is ≥365 days
ago. Undated-only watch history → not eligible (can't compute staleness), same
deliberate edge case as lucky.py. For the `together` profile a candidate must
be eligible for BOTH partners (date night is new to both).

Scores are computed against the shared CorpusSpace (taste.py) so every vector
is comparable. Quality prior is the §2 Bayesian-smoothed vote mean, min-max
scaled across the candidate pool at scoring time (kept OUT of the similarity
vector, per §2). novelty uses the max cosine to the user's SEEN films.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import scipy.sparse as sp
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Film, Watch
from .taste import CorpusSpace, UserTasteModel

logger = logging.getLogger(__name__)

# §5 top-level score weights.
W_TASTE = 0.55
W_QUALITY = 0.20
W_NOVELTY = 0.15
W_AVAIL = 0.10

# §2 Bayesian smoothing: (v·R + m·C)/(v+m).
BAYES_M = 500.0

# MMR (§5).
MMR_LAMBDA = 0.7  # 0.7·score − 0.3·max_sim
MMR_POOL = 200  # re-rank the top-200 by score


# --------------------------------------------------------------------------
# Eligibility (reuses lucky.py's rule)
# --------------------------------------------------------------------------
def eligible_film_ids(session: Session, user_id: int) -> set[int]:
    """Film ids this user is NOT allowed to see recommended (watched <365d ago,
    OR undated-only watch history). The complement — everything else — is
    eligible. Returns the INELIGIBLE set (cheaper to test candidates against).

    Mirrors lucky.py exactly: never-watched → eligible; most-recent dated watch
    ≥365d → eligible; <365d → excluded; undated-only → excluded.
    """
    today = datetime.now().date()
    rows = session.execute(
        select(Watch.film_id, Watch.watched_date).where(Watch.user_id == user_id)
    ).all()

    by_film: dict[int, list[str | None]] = {}
    for film_id, wd in rows:
        by_film.setdefault(film_id, []).append(wd)

    ineligible: set[int] = set()
    for film_id, dates in by_film.items():
        dated = [d for d in dates if d is not None]
        if not dated:
            # undated-only history → cannot compute staleness → not eligible
            ineligible.add(film_id)
            continue
        try:
            most_recent = max(datetime.strptime(d, "%Y-%m-%d").date() for d in dated)
        except ValueError:
            ineligible.add(film_id)
            continue
        days_since = (today - most_recent).days
        if days_since < 365:
            ineligible.add(film_id)
    return ineligible


# --------------------------------------------------------------------------
# Component scores
# --------------------------------------------------------------------------
def _quality_prior_raw(film: Film, global_mean: float) -> float:
    v = float(film.vote_count or 0)
    r = float(film.vote_average or 0.0)
    if v + BAYES_M <= 0:
        return global_mean
    return (v * r + BAYES_M * global_mean) / (v + BAYES_M)


def _minmax(values: np.ndarray) -> np.ndarray:
    if values.size == 0:
        return values
    lo, hi = float(values.min()), float(values.max())
    if hi - lo < 1e-12:
        return np.full_like(values, 0.5)
    return (values - lo) / (hi - lo)


def taste_score_vector(
    model: UserTasteModel, cand_matrix: sp.csr_matrix
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (blended_taste, proto_component, ridge_component) for candidates,
    each length = cand_matrix.shape[0], all min-max scaled to [0,1] across the
    candidate pool. taste = λ·ridge + (1−λ)·proto.
    """
    n = cand_matrix.shape[0]

    # proto_score = cos(x, p⁺) − 0.5·cos(x, p⁻)
    if model.pos_prototype is not None:
        pos = cosine_similarity(cand_matrix, model.pos_prototype).ravel()
    else:
        pos = np.zeros(n)
    if model.neg_prototype is not None:
        neg = cosine_similarity(cand_matrix, model.neg_prototype).ravel()
    else:
        neg = np.zeros(n)
    proto_raw = pos - 0.5 * neg
    proto = _minmax(proto_raw)

    # ridge_score = ŷ(x), min-max scaled
    if model.ridge_coef is not None:
        ridge_raw = cand_matrix.dot(model.ridge_coef) + model.ridge_intercept
        ridge = _minmax(np.asarray(ridge_raw).ravel())
    else:
        ridge = np.zeros(n)

    lam = model.lam
    blended = lam * ridge + (1.0 - lam) * proto
    # If no signal at all (zero-rating cold start): flat 0.5 so quality/novelty
    # drive the ranking rather than a dead 0.
    if model.pos_prototype is None and model.ridge_coef is None:
        blended = np.full(n, 0.5)
    return blended, proto, ridge


def novelty_vector(
    corpus: CorpusSpace, cand_matrix: sp.csr_matrix, seen_film_ids: set[int]
) -> np.ndarray:
    """novelty(i) = 1 − max_{j∈seen} cos(xᵢ, xⱼ), capped at the 95th pctile of
    the max-sim distribution (per §5) so a single near-duplicate doesn't zero
    a whole cluster. Returns length cand_matrix.shape[0], in [0,1].
    """
    seen_rows = [corpus.index_of[f] for f in seen_film_ids if f in corpus.index_of]
    if not seen_rows:
        return np.ones(cand_matrix.shape[0])
    seen_matrix = corpus.matrix[seen_rows]
    sims = cosine_similarity(cand_matrix, seen_matrix)  # (n_cand x n_seen)
    max_sim = sims.max(axis=1)
    cap = np.percentile(max_sim, 95) if max_sim.size else 1.0
    max_sim = np.minimum(max_sim, cap)
    return np.clip(1.0 - max_sim, 0.0, 1.0)


@dataclass
class ScoredCandidate:
    film_id: int
    score: float
    taste: float
    quality_prior: float
    novelty: float
    availability_boost: float
    # per-user components (populated for together, and for the single-user why)
    user_scores: dict[int, float] = field(default_factory=dict)


def score_candidates(
    *,
    corpus: CorpusSpace,
    candidate_films: list[Film],
    models: dict[int, UserTasteModel],
    seen_by: dict[int, set[int]],
    availability_boost: dict[int, float],
    global_mean: float,
    profile_user_ids: list[int],
    novelty_weight_mult: float = 1.0,
) -> list[ScoredCandidate]:
    """Score every candidate for the given profile.

    profile_user_ids: [uid] for me/partner, [u1, u2] for together.
    novelty_weight_mult: UI `novelty` param rescales the novelty weight 0–2×.
    Returns ScoredCandidate list (unsorted).
    """
    cand_ids = [f.id for f in candidate_films]
    rows = [corpus.index_of[fid] for fid in cand_ids]
    cand_matrix = corpus.matrix[rows]
    n = len(cand_ids)

    # quality prior (shared across users), min-max over the pool
    q_raw = np.array([_quality_prior_raw(f, global_mean) for f in candidate_films])
    quality = _minmax(q_raw)

    avail = np.array([availability_boost.get(fid, 0.0) for fid in cand_ids])

    # per-user taste + novelty
    per_user_total: dict[int, np.ndarray] = {}
    per_user_taste: dict[int, np.ndarray] = {}
    per_user_novelty: dict[int, np.ndarray] = {}
    for uid in profile_user_ids:
        taste, _proto, _ridge = taste_score_vector(models[uid], cand_matrix)
        nov = novelty_vector(corpus, cand_matrix, seen_by.get(uid, set()))
        per_user_taste[uid] = taste
        per_user_novelty[uid] = nov
        per_user_total[uid] = (
            W_TASTE * taste
            + W_QUALITY * quality
            + (W_NOVELTY * novelty_weight_mult) * nov
            + W_AVAIL * avail
        )

    out: list[ScoredCandidate] = []
    for i, fid in enumerate(cand_ids):
        if len(profile_user_ids) == 1:
            uid = profile_user_ids[0]
            total = float(per_user_total[uid][i])
            out.append(
                ScoredCandidate(
                    film_id=fid,
                    score=total,
                    taste=float(per_user_taste[uid][i]),
                    quality_prior=float(quality[i]),
                    novelty=float(per_user_novelty[uid][i]),
                    availability_boost=float(avail[i]),
                    user_scores={uid: total},
                )
            )
        else:
            u1, u2 = profile_user_ids[0], profile_user_ids[1]
            s1 = float(per_user_total[u1][i])
            s2 = float(per_user_total[u2][i])
            together = 0.7 * min(s1, s2) + 0.3 * ((s1 + s2) / 2.0)
            # component breakdown reported as the mean of the two users'
            # components (the together score is a blend of the two totals; the
            # per-term display is the average so the why still decomposes).
            out.append(
                ScoredCandidate(
                    film_id=fid,
                    score=together,
                    taste=float((per_user_taste[u1][i] + per_user_taste[u2][i]) / 2),
                    quality_prior=float(quality[i]),
                    novelty=float((per_user_novelty[u1][i] + per_user_novelty[u2][i]) / 2),
                    availability_boost=float(avail[i]),
                    user_scores={u1: s1, u2: s2},
                )
            )
    return out


def mmr_rerank(
    corpus: CorpusSpace,
    scored: list[ScoredCandidate],
    *,
    limit: int,
) -> list[ScoredCandidate]:
    """Greedy MMR diversity re-rank over the top-MMR_POOL by score.

    MMR = 0.7·score(i) − 0.3·max_{j∈selected} cos(xᵢ, xⱼ).
    """
    if not scored:
        return []
    ranked = sorted(scored, key=lambda c: c.score, reverse=True)[:MMR_POOL]
    rows = [corpus.index_of[c.film_id] for c in ranked]
    sub = corpus.matrix[rows]
    sim = cosine_similarity(sub)  # (k x k)
    scores = np.array([c.score for c in ranked])

    selected: list[int] = []
    remaining = set(range(len(ranked)))
    # seed with the single highest score
    first = int(np.argmax(scores))
    selected.append(first)
    remaining.discard(first)

    target = min(limit, len(ranked))
    while len(selected) < target and remaining:
        best_idx = None
        best_val = -np.inf
        for idx in remaining:
            max_sim_sel = max(sim[idx, s] for s in selected)
            val = MMR_LAMBDA * scores[idx] - (1 - MMR_LAMBDA) * max_sim_sel
            if val > best_val:
                best_val = val
                best_idx = idx
        selected.append(best_idx)
        remaining.discard(best_idx)

    return [ranked[i] for i in selected]
