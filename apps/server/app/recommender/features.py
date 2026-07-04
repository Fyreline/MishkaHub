"""Feature engineering for content-similarity — docs/phases/PHASE-3-recommender.md §2.

One sparse row vector per film, built from ``films.metadata_json`` (the raw
TMDB ``/movie/{id}?append_to_response=credits,keywords,release_dates``
payload, per Phase 2 hydration). Blocks, each L2-normalised then scaled by a
block weight, horizontally stacked into one sparse row per film:

    Genres          one-hot over TMDB's ~19 genres           weight 1.0
    Keywords        TF-IDF, vocab capped at 2000 terms       weight 1.0
    Cast (top 5)    FeatureHasher signed, 256 dims            weight 0.5
    Director(s)     FeatureHasher signed, 128 dims            weight 0.7
    Decade          one-hot (1950s..2020s, clamp ends)        weight 0.4
    Runtime bucket  one-hot <90/90-110/110-140/140-180/>180   weight 0.3
    Original lang   one-hot top-15 + other                   weight 0.4

Deviation from the doc: keyword TF-IDF uses ``min_df=1`` instead of the
doc's ``min_df=3``. The doc's min_df=3 assumes a much larger candidate pool
(the full nightly-refreshed 1-2k film discover pool, §3); this is v0 running
directly against the local household library, which currently has on the
order of ~60-200 films. At that size min_df=3 would drop almost every
keyword (most keywords appear on only one or two films in a corpus this
small), gutting the keyword block entirely. min_df=1 keeps the block
meaningful now; revisit when the candidate-pool machinery (§3) is built and
the corpus is in the thousands.

Quality/popularity priors (§2's last table row) are deliberately NOT
included here — the doc itself says they're kept outside the similarity
vector and used only as a ranking prior (§5), which is out of scope for v0.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import json
from typing import TYPE_CHECKING

import numpy as np
import scipy.sparse as sp
from sklearn.feature_extraction import FeatureHasher
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .vibes import vibe_tags

if TYPE_CHECKING:
    from ..models import Film

# ---------------------------------------------------------------------------
# Fixed vocabularies (hand-rolled one-hot blocks; small + stable, no need for
# sklearn's OneHotEncoder machinery).
# ---------------------------------------------------------------------------

# TMDB's official movie genre list (19 genres, ids per
# https://developer.themoviedb.org/reference/genre-movie-list). We one-hot by
# name (not id) since metadata_json already carries names and this is more
# robust to any id/name drift.
TMDB_GENRES: list[str] = [
    "Action",
    "Adventure",
    "Animation",
    "Comedy",
    "Crime",
    "Documentary",
    "Drama",
    "Family",
    "Fantasy",
    "History",
    "Horror",
    "Music",
    "Mystery",
    "Romance",
    "Science Fiction",
    "TV Movie",
    "Thriller",
    "War",
    "Western",
]

# Decade buckets, clamped at both ends (anything earlier folds into the
# earliest bucket, anything later into the last).
DECADE_BUCKETS: list[int] = [1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020]

# Runtime buckets per §2's exact boundaries: <90, 90-110, 110-140, 140-180, >180.
RUNTIME_BUCKETS: list[str] = ["<90", "90-110", "110-140", "140-180", ">180"]

# Top-15 original languages + "other". Chosen from TMDB's most common
# original_language values for mainstream film catalogues; "other" absorbs
# anything not listed (the local corpus check showed en/ko/ja in practice,
# but we keep the full top-15 so the vector space is stable if/when more
# languages enter the library).
TOP15_LANGUAGES: list[str] = [
    "en", "fr", "es", "ja", "ko", "de", "it", "hi", "zh", "ru",
    "pt", "sv", "da", "cn", "nl",
]

# Block weights, exactly per §2's table.
WEIGHT_GENRES = 1.0
WEIGHT_KEYWORDS = 1.0
WEIGHT_CAST = 0.5
WEIGHT_DIRECTOR = 0.7
WEIGHT_DECADE = 0.4
WEIGHT_RUNTIME = 0.3
WEIGHT_LANGUAGE = 0.4

CAST_HASH_DIM = 256
DIRECTOR_HASH_DIM = 128
KEYWORDS_MAX_FEATURES = 2000


def _runtime_bucket(runtime_min: int | None) -> str:
    if runtime_min is None:
        # Neutral fallback: put unknown-runtime films in the modal bucket
        # rather than crashing or silently zeroing the whole block.
        return "110-140"
    if runtime_min < 90:
        return "<90"
    if runtime_min <= 110:
        return "90-110"
    if runtime_min <= 140:
        return "110-140"
    if runtime_min <= 180:
        return "140-180"
    return ">180"


def _decade_bucket(release_year: int | None) -> int:
    if release_year is None:
        return DECADE_BUCKETS[-1]
    decade = (release_year // 10) * 10
    if decade < DECADE_BUCKETS[0]:
        return DECADE_BUCKETS[0]
    if decade > DECADE_BUCKETS[-1]:
        return DECADE_BUCKETS[-1]
    return decade


@dataclass
class RawFeatures:
    """Raw fields pulled from one film's metadata_json, pre-vectorisation."""

    film_id: int
    genres: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    cast_ids: list[int] = field(default_factory=list)  # top-5 billed, by TMDB person id
    director_ids: list[int] = field(default_factory=list)
    release_year: int | None = None
    runtime_min: int | None = None
    original_language: str | None = None


def extract_features(film_id: int, metadata_json: dict | None) -> RawFeatures:
    """Pull the raw fields needed per block out of one film's parsed TMDB payload.

    Handles missing/partial data gracefully (older-hydrated films might lack
    ``credits``/``keywords`` sub-keys) by falling back to empty/neutral
    values — never raises. Verified against the live DB (2026-07-03): all 60
    locally hydrated films currently carry both ``credits`` and ``keywords``
    in full, but this function does not assume that holds for every future
    row (e.g. a film hydrated via a thinner payload shape, see
    ``importers/merge.py``'s ``is_thin_payload`` guard).
    """
    metadata_json = metadata_json or {}

    genres = [
        g.get("name") for g in (metadata_json.get("genres") or []) if g.get("name")
    ]

    # keywords sub-payload shape for /movie/{id} is {"keywords": [...]}
    # (NOT "results" — that's the /movie/{id}/keywords standalone endpoint
    # shape). Confirmed against real DB rows.
    keywords_block = metadata_json.get("keywords") or {}
    keyword_names = [
        k.get("name") for k in (keywords_block.get("keywords") or []) if k.get("name")
    ]

    credits = metadata_json.get("credits") or {}
    cast_list = credits.get("cast") or []
    # Top-5 billed = lowest "order" value; TMDB already returns cast sorted by
    # order in practice, but sort explicitly to be safe against any variance.
    cast_sorted = sorted(
        (c for c in cast_list if c.get("id") is not None),
        key=lambda c: c.get("order", 10_000),
    )
    cast_ids = [c["id"] for c in cast_sorted[:5]]

    crew_list = credits.get("crew") or []
    director_ids = [
        c["id"] for c in crew_list if c.get("job") == "Director" and c.get("id") is not None
    ]

    release_date = metadata_json.get("release_date") or ""
    release_year = None
    if len(release_date) >= 4 and release_date[:4].isdigit():
        release_year = int(release_date[:4])

    return RawFeatures(
        film_id=film_id,
        genres=genres,
        keywords=keyword_names,
        cast_ids=cast_ids,
        director_ids=director_ids,
        release_year=release_year,
        runtime_min=metadata_json.get("runtime"),
        original_language=metadata_json.get("original_language"),
    )


def _l2_normalize_dense_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class FeatureSpace:
    """Fit-once, transform-many feature builder over a corpus of films.

    Not persisted as an artefact (no §6 retrain/versioning machinery in
    v0) — callers rebuild a FeatureSpace + matrix in-process per request,
    which is fine at this corpus size (tens to low hundreds of films).
    """

    def __init__(self) -> None:
        self._tfidf = TfidfVectorizer(
            max_features=KEYWORDS_MAX_FEATURES,
            min_df=1,  # see module docstring: deviation from doc's min_df=3
            sublinear_tf=True,
            tokenizer=lambda s: s,  # keywords already tokenized as list[str]
            preprocessor=lambda s: s,
            token_pattern=None,  # silence sklearn warning: tokenizer overrides pattern anyway
            lowercase=False,
        )
        self._fitted = False

    def fit(self, raw_features: list[RawFeatures]) -> "FeatureSpace":
        keyword_docs = [rf.keywords for rf in raw_features]
        # Guard: TfidfVectorizer errors on an all-empty vocabulary (e.g. if
        # literally zero films have any keywords at all). Fall back to a
        # dummy token so .transform() still works and simply contributes a
        # zero vector for every film.
        if not any(keyword_docs):
            keyword_docs = [["__no_keywords__"] for _ in raw_features]
        self._tfidf.fit(keyword_docs)
        self._fitted = True
        return self

    def transform(self, raw_features: list[RawFeatures]) -> sp.csr_matrix:
        if not self._fitted:
            raise RuntimeError("FeatureSpace.transform() called before fit()")

        n = len(raw_features)
        blocks: list[sp.spmatrix] = []

        # --- Genres: one-hot over TMDB_GENRES ---
        genre_index = {name: i for i, name in enumerate(TMDB_GENRES)}
        genre_mat = np.zeros((n, len(TMDB_GENRES)), dtype=np.float64)
        for row, rf in enumerate(raw_features):
            for g in rf.genres:
                idx = genre_index.get(g)
                if idx is not None:
                    genre_mat[row, idx] = 1.0
        genre_mat = _l2_normalize_dense_rows(genre_mat) * WEIGHT_GENRES
        blocks.append(sp.csr_matrix(genre_mat))

        # --- Keywords: TF-IDF (already L2-normalised by sklearn by default) ---
        keyword_docs = [rf.keywords or ["__no_keywords__"] for rf in raw_features]
        tfidf_mat = self._tfidf.transform(keyword_docs)  # already L2 row-normalised
        blocks.append(tfidf_mat.multiply(WEIGHT_KEYWORDS).tocsr())

        # --- Cast: FeatureHasher (signed) on "cast:{person_id}" ---
        hasher_cast = FeatureHasher(n_features=CAST_HASH_DIM, input_type="string", alternate_sign=True)
        cast_tokens = [[f"cast:{pid}" for pid in rf.cast_ids] for rf in raw_features]
        cast_mat = hasher_cast.transform(cast_tokens).toarray().astype(np.float64)
        cast_mat = _l2_normalize_dense_rows(cast_mat) * WEIGHT_CAST
        blocks.append(sp.csr_matrix(cast_mat))

        # --- Director(s): FeatureHasher (signed) on "director:{person_id}" ---
        hasher_dir = FeatureHasher(n_features=DIRECTOR_HASH_DIM, input_type="string", alternate_sign=True)
        director_tokens = [[f"director:{pid}" for pid in rf.director_ids] for rf in raw_features]
        director_mat = hasher_dir.transform(director_tokens).toarray().astype(np.float64)
        director_mat = _l2_normalize_dense_rows(director_mat) * WEIGHT_DIRECTOR
        blocks.append(sp.csr_matrix(director_mat))

        # --- Decade: one-hot ---
        decade_index = {d: i for i, d in enumerate(DECADE_BUCKETS)}
        decade_mat = np.zeros((n, len(DECADE_BUCKETS)), dtype=np.float64)
        for row, rf in enumerate(raw_features):
            decade_mat[row, decade_index[_decade_bucket(rf.release_year)]] = 1.0
        decade_mat = _l2_normalize_dense_rows(decade_mat) * WEIGHT_DECADE
        blocks.append(sp.csr_matrix(decade_mat))

        # --- Runtime bucket: one-hot ---
        runtime_index = {b: i for i, b in enumerate(RUNTIME_BUCKETS)}
        runtime_mat = np.zeros((n, len(RUNTIME_BUCKETS)), dtype=np.float64)
        for row, rf in enumerate(raw_features):
            runtime_mat[row, runtime_index[_runtime_bucket(rf.runtime_min)]] = 1.0
        runtime_mat = _l2_normalize_dense_rows(runtime_mat) * WEIGHT_RUNTIME
        blocks.append(sp.csr_matrix(runtime_mat))

        # --- Original language: one-hot top-15 + other ---
        lang_index = {lang: i for i, lang in enumerate(TOP15_LANGUAGES)}
        lang_mat = np.zeros((n, len(TOP15_LANGUAGES) + 1), dtype=np.float64)  # +1 = "other"
        other_col = len(TOP15_LANGUAGES)
        for row, rf in enumerate(raw_features):
            idx = lang_index.get(rf.original_language, other_col)
            lang_mat[row, idx] = 1.0
        lang_mat = _l2_normalize_dense_rows(lang_mat) * WEIGHT_LANGUAGE
        blocks.append(sp.csr_matrix(lang_mat))

        return sp.hstack(blocks, format="csr")


def _parse_metadata(film: "Film") -> dict:
    if not film.metadata_json:
        return {}
    try:
        return json.loads(film.metadata_json)
    except (json.JSONDecodeError, TypeError):
        return {}


def build_corpus_matrix(films: list["Film"]) -> tuple[sp.csr_matrix, list[int]]:
    """Fit a FeatureSpace over all given films (non-null metadata_json expected;
    films with unparseable/missing metadata still get a row of neutral/empty
    values via extract_features's graceful fallbacks) and return the stacked
    matrix + aligned film-id list.

    Recomputed in-process on every call — no persisted artefact/versioning
    (§6 is out of scope for v0). Fine at this corpus size (tens to low
    hundreds of films): fitting + transforming is well under a second.
    """
    film_ids = [f.id for f in films]
    raw_features = [extract_features(f.id, _parse_metadata(f)) for f in films]
    space = FeatureSpace().fit(raw_features)
    matrix = space.transform(raw_features)
    return matrix, film_ids


def similar_films(
    target_film_id: int,
    films: list["Film"],
    *,
    limit: int = 12,
    max_runtime: int | None = None,
    vibe: str | None = None,
) -> list[dict]:
    """Cosine-similarity "similar films" query against the local corpus.

    ``films`` must include the target film itself (it's excluded from the
    results after the corpus matrix is built) plus every other film to
    compare against. Filters (``max_runtime``, ``vibe``) are applied BEFORE
    truncating to ``limit``, per spec.
    """
    matrix, film_ids = build_corpus_matrix(films)

    if target_film_id not in film_ids:
        raise ValueError(f"film {target_film_id} not in corpus passed to similar_films()")

    target_idx = film_ids.index(target_film_id)
    films_by_id = {f.id: f for f in films}
    target_film = films_by_id[target_film_id]
    target_meta = _parse_metadata(target_film)
    target_genres = {g.get("name") for g in (target_meta.get("genres") or []) if g.get("name")}
    target_keywords_block = target_meta.get("keywords") or {}
    target_keyword_names = {
        k.get("name") for k in (target_keywords_block.get("keywords") or []) if k.get("name")
    }

    sims = cosine_similarity(matrix[target_idx], matrix).ravel()  # shape (n_films,)

    scored: list[tuple[int, float]] = []
    for i, fid in enumerate(film_ids):
        if fid == target_film_id:
            continue
        film = films_by_id[fid]

        if max_runtime is not None:
            if film.runtime_min is None or film.runtime_min > max_runtime:
                continue

        if vibe is not None:
            if vibe == "quick_watch":
                if film.runtime_min is None or film.runtime_min > 95:
                    continue
            else:
                film_meta = _parse_metadata(film)
                tags = vibe_tags(film_meta, runtime_min=film.runtime_min)
                if vibe not in tags:
                    continue

        scored.append((fid, float(sims[i])))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    scored = scored[:limit]

    results: list[dict] = []
    for fid, score in scored:
        film = films_by_id[fid]
        film_meta = _parse_metadata(film)
        film_genres = {g.get("name") for g in (film_meta.get("genres") or []) if g.get("name")}
        film_keywords_block = film_meta.get("keywords") or {}
        film_keyword_names = {
            k.get("name") for k in (film_keywords_block.get("keywords") or []) if k.get("name")
        }

        shared_genres = sorted(target_genres & film_genres)
        shared_keywords = sorted(target_keyword_names & film_keyword_names)[:5]

        results.append(
            {
                "film_id": fid,
                "score": score,
                "why": {
                    "top_shared_genres": shared_genres,
                    "shared_keywords_sample": shared_keywords,
                },
            }
        )

    return results
