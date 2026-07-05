"""Per-user taste model — docs/phases/PHASE-3-recommender.md §4.

Two models per user, blended by data volume:

  (a) Prototype vectors (cold-start backbone, works from ~1 rating):
      p⁺ = weighted mean of feature vectors of films rated ≥ user's mean
           (weight = rating − mean + 0.5; likes add +0.75; recency decay
           exp(−Δdays/1095) on rated_at so taste drifts with the user)
      p⁻ = weighted mean of films rated ≤ 2.0 (weight = 2.5 − rating)
      proto_score(i) = cos(xᵢ, p⁺) − 0.5·cos(xᵢ, p⁻)

  (b) Ridge regression (kicks in ≥30 ratings):
      target y = rating − user_mean; RidgeCV (leave-one-out closed form)
      over the same feature vectors. ridge_score = ŷ(xᵢ), min-max scaled to
      [0,1] over the candidate pool at scoring time.

  Blend: taste = λ·ridge + (1−λ)·proto,  λ = clip((n_ratings−30)/120, 0, 0.8)

Real household data (verified 2026-07-04): user 1 has 129 ratings → λ = 0.8
(Ridge + prototype). User 2 has 22 ratings → λ = 0 (prototype-only, the §4
cold-start path). Both real, documented in the build report.

The fitted per-user model is a `UserTasteModel` holding the two prototype
sparse vectors, the Ridge coefficients (or None), user_mean, n_ratings and λ.
`fit_user_taste()` builds one against a shared corpus FeatureSpace + matrix so
every film (candidate or historical) is embedded in the SAME vector space.
Persisted (§6, scoped) via `save_taste_artifact()` as a joblib file under
`data/models/`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import numpy as np
import scipy.sparse as sp
from sklearn.linear_model import RidgeCV
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Film, Like, Rating
from .features import FeatureSpace, _fit_corpus_cached

logger = logging.getLogger(__name__)

RIDGE_MIN_RATINGS = 30
RECENCY_TAU_DAYS = 1095.0  # exp(-Δdays/1095) recency decay on rated_at


@dataclass
class CorpusSpace:
    """A fitted FeatureSpace + the matrix/id-index over a fixed corpus of films.

    Everything (taste-model training, candidate scoring) is embedded through
    this one space so vectors are comparable. Rebuilt in-process per
    retrain/request — cheap at ~1k films (well under a second).
    """

    space: FeatureSpace
    matrix: sp.csr_matrix
    film_ids: list[int]
    index_of: dict[int, int] = field(default_factory=dict)

    def vector_for(self, film_id: int) -> sp.csr_matrix | None:
        idx = self.index_of.get(film_id)
        if idx is None:
            return None
        return self.matrix[idx]


def build_corpus_space(films: list[Film]) -> CorpusSpace:
    """Fit (or reuse a cached fit of) a FeatureSpace over ALL given films and
    return the reusable space.

    Previously re-extracted features and re-fit a SECOND, separate
    FeatureSpace just to get a `space` object to hand back (on top of the
    fit+transform `features.py`'s `build_corpus_matrix` already does) — real,
    measured double work at the ~5,000-film corpus size (2026-07-05). Now
    shares `features.py`'s `_fit_corpus_cached`, which does the fit once
    (cached across calls with the same film-id set) and returns the
    FeatureSpace alongside the matrix.
    """
    space, matrix, film_ids = _fit_corpus_cached(films)
    index_of = {fid: i for i, fid in enumerate(film_ids)}
    return CorpusSpace(space=space, matrix=matrix, film_ids=film_ids, index_of=index_of)


@dataclass
class UserTasteModel:
    user_id: int
    user_mean: float
    n_ratings: int
    lam: float  # blend weight λ for Ridge
    pos_prototype: sp.csr_matrix | None  # 1 x D
    neg_prototype: sp.csr_matrix | None  # 1 x D
    ridge_coef: np.ndarray | None  # shape (D,) or None if < RIDGE_MIN_RATINGS
    ridge_intercept: float
    ridge_alpha: float | None
    n_dims: int

    def has_ridge(self) -> bool:
        return self.ridge_coef is not None


def _recency_weight(rated_at: str | None, today: date) -> float:
    if not rated_at:
        return 1.0
    try:
        d = datetime.strptime(rated_at[:10], "%Y-%m-%d").date()
    except ValueError:
        return 1.0
    delta_days = max(0, (today - d).days)
    return float(np.exp(-delta_days / RECENCY_TAU_DAYS))


def _weighted_prototype(
    rows: list[tuple[int, float]], corpus: CorpusSpace
) -> sp.csr_matrix | None:
    """rows = list of (matrix_row_index, weight). Weighted mean of those rows,
    L2-normalised. Returns None if no rows.
    """
    if not rows:
        return None
    indices = [r for r, _ in rows]
    weights = np.array([w for _, w in rows], dtype=np.float64)
    sub = corpus.matrix[indices]  # (k x D)
    # weighted mean: (wᵀ · sub) / Σw
    total = weights.sum()
    if total <= 0:
        return None
    proto = sp.csr_matrix(weights @ sub.toarray()) / total  # 1 x D dense-then-sparse
    norm = np.linalg.norm(proto.toarray())
    if norm > 0:
        proto = proto / norm
    return sp.csr_matrix(proto)


def fit_user_taste(session: Session, user_id: int, corpus: CorpusSpace) -> UserTasteModel:
    """Build the blended taste model for one user against the shared corpus."""
    today = datetime.now().date()

    ratings = session.execute(
        select(Rating.film_id, Rating.rating, Rating.rated_at).where(
            Rating.user_id == user_id
        )
    ).all()
    n_ratings = len(ratings)
    if n_ratings == 0:
        # Cold-start with zero ratings: no prototypes at all. Scoring falls
        # back to quality_prior + popularity (handled in scoring.py).
        return UserTasteModel(
            user_id=user_id, user_mean=0.0, n_ratings=0, lam=0.0,
            pos_prototype=None, neg_prototype=None, ridge_coef=None,
            ridge_intercept=0.0, ridge_alpha=None, n_dims=corpus.matrix.shape[1],
        )

    user_mean = float(np.mean([r for _, r, _ in ratings]))
    liked_film_ids = set(
        session.scalars(select(Like.film_id).where(Like.user_id == user_id)).all()
    )

    # --- Prototypes ---
    pos_rows: list[tuple[int, float]] = []
    neg_rows: list[tuple[int, float]] = []
    for film_id, rating, rated_at in ratings:
        idx = corpus.index_of.get(film_id)
        if idx is None:
            continue  # rated film not in corpus (shouldn't happen — corpus = all films)
        recency = _recency_weight(rated_at, today)
        if rating >= user_mean:
            w = (rating - user_mean + 0.5)
            if film_id in liked_film_ids:
                w += 0.75
            pos_rows.append((idx, max(0.0, w) * recency))
        if rating <= 2.0:
            neg_rows.append((idx, max(0.0, 2.5 - rating) * recency))

    pos_prototype = _weighted_prototype(pos_rows, corpus)
    neg_prototype = _weighted_prototype(neg_rows, corpus)

    # --- Ridge (only if enough data) ---
    ridge_coef = None
    ridge_intercept = 0.0
    ridge_alpha = None
    if n_ratings >= RIDGE_MIN_RATINGS:
        X_rows: list[int] = []
        y: list[float] = []
        for film_id, rating, _ in ratings:
            idx = corpus.index_of.get(film_id)
            if idx is None:
                continue
            X_rows.append(idx)
            y.append(rating - user_mean)
        if len(X_rows) >= RIDGE_MIN_RATINGS:
            X = corpus.matrix[X_rows]
            y_arr = np.array(y, dtype=np.float64)
            # RidgeCV: efficient leave-one-out over a small alpha grid.
            model = RidgeCV(alphas=np.logspace(-1, 3, 20))
            model.fit(X, y_arr)
            ridge_coef = np.asarray(model.coef_, dtype=np.float64).ravel()
            ridge_intercept = float(model.intercept_)
            ridge_alpha = float(model.alpha_)

    # λ = clip((n_ratings − 30)/120, 0, 0.8), but 0 if we don't actually have ridge
    lam = float(np.clip((n_ratings - 30) / 120.0, 0.0, 0.8))
    if ridge_coef is None:
        lam = 0.0

    return UserTasteModel(
        user_id=user_id,
        user_mean=user_mean,
        n_ratings=n_ratings,
        lam=lam,
        pos_prototype=pos_prototype,
        neg_prototype=neg_prototype,
        ridge_coef=ridge_coef,
        ridge_intercept=ridge_intercept,
        ridge_alpha=ridge_alpha,
        n_dims=corpus.matrix.shape[1],
    )


def top_ridge_features(model: UserTasteModel, corpus: CorpusSpace, k: int = 6) -> list[str]:
    """Human-readable strongest-positive Ridge features for the `why` payload.

    Maps the highest-weight coefficient dimensions back to their block/feature
    label (genre name, keyword term, decade, etc.). Best-effort — returns []
    if the user has no Ridge model.
    """
    if model.ridge_coef is None:
        return []
    labels = _feature_labels(corpus)
    coef = model.ridge_coef
    order = np.argsort(coef)[::-1]
    out: list[str] = []
    for idx in order[: k * 3]:
        if coef[idx] <= 0:
            break
        label = labels.get(int(idx))
        if label:
            out.append(label)
        if len(out) >= k:
            break
    return out


def _feature_labels(corpus: CorpusSpace) -> dict[int, str]:
    """Column-index -> readable label, reconstructing the block layout from
    features.py's fixed vocabularies + the fitted TF-IDF vocab. Only the
    interpretable blocks (genres/keywords/decade/runtime/language) are
    labelled; hashed people dims are opaque by design (and skipped).
    """
    from . import features as F

    labels: dict[int, str] = {}
    col = 0
    # genres
    for name in F.TMDB_GENRES:
        labels[col] = f"genre:{name}"
        col += 1
    # keywords (tfidf vocab, in feature-index order)
    vocab = corpus.space._tfidf.vocabulary_ if corpus.space._fitted else {}  # noqa: SLF001
    inv = {v: k for k, v in vocab.items()}
    kw_dim = len(inv)
    for j in range(kw_dim):
        term = inv.get(j)
        if term:
            labels[col + j] = f"keyword:{term}"
    col += kw_dim
    # cast (hashed, opaque) + director (hashed, opaque) — skip labelling
    col += F.CAST_HASH_DIM
    col += F.DIRECTOR_HASH_DIM
    # decade
    for d in F.DECADE_BUCKETS:
        labels[col] = f"decade:{d}s"
        col += 1
    # runtime
    for b in F.RUNTIME_BUCKETS:
        labels[col] = f"runtime:{b}"
        col += 1
    # language (top15 + other)
    for lang in F.TOP15_LANGUAGES:
        labels[col] = f"lang:{lang}"
        col += 1
    labels[col] = "lang:other"
    col += 1
    return labels
