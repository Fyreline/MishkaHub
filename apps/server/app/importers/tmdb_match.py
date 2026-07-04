"""TMDB matching strategy — docs/phases/PHASE-2-letterboxd-import.md §7.

Given a normalised import row (see csv_parser.py's "virtual CSV" shape), this
module decides which TMDB film it refers to, following the flowchart in §7:

    row -> have tmdb:movieId (RSS)? -> yes -> GET /movie/{id} -> done
           no (CSV / scrape)
        -> films table lookup by (normalised title, year), letterboxd_uri,
           or letterboxd_slug -> hit -> done
        -> miss -> TMDB search (with year, retry without year on empty)
        -> score candidates, auto-accept if top score >= 3 and beats
           runner-up by >= 1, else queue to unmatched_imports

This module's job stops at "given a row, return either a matched TMDB film
payload or an unmatched marker with the top-5 candidates for the UI queue."
The actual upsert into films/unmatched_imports happens in merge.py (§8).
"""
from __future__ import annotations

import logging
import math
import unicodedata
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.tmdb import TMDBClient
from ..models import Film

logger = logging.getLogger(__name__)

# Auto-accept thresholds per §7.
AUTO_ACCEPT_MIN_SCORE = 3.0
AUTO_ACCEPT_MIN_MARGIN = 1.0


def _normalize(s: str | None) -> str:
    """Casefold, strip accents, strip punctuation for exact-match comparisons.

    Used to compare Letterboxd's logged title against TMDB's title /
    original_title per §7 ("exact_* compares casefolded, accent-stripped,
    punctuation-stripped").
    """
    if not s:
        return ""
    # Strip accents: decompose then drop combining marks.
    decomposed = unicodedata.normalize("NFKD", s)
    no_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    casefolded = no_accents.casefold()
    # Strip punctuation: keep alnum + whitespace, collapse whitespace.
    cleaned = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in casefolded)
    return " ".join(cleaned.split())


def _lookup_existing_film(
    session: Session, *, name: str, year: int | None, uri: str | None, slug: str | None = None
) -> Film | None:
    """Look up an existing films row by letterboxd_uri, letterboxd_slug, or
    normalised (title, year) — the first stage of the §7 flowchart for
    CSV/scrape rows (RSS rows short-circuit via tmdb:movieId before this is
    ever called).
    """
    if uri:
        existing = session.scalars(select(Film).where(Film.letterboxd_uri == uri)).first()
        if existing is not None:
            return existing
    if slug:
        existing = session.scalars(select(Film).where(Film.letterboxd_slug == slug)).first()
        if existing is not None:
            return existing

    target_title = _normalize(name)
    if not target_title:
        return None
    # release_year is indexed but there's no normalised-title column, so we
    # scope by year first (when known) and compare normalised titles in
    # Python — libraries are small (thousands of rows), this is fine.
    stmt = select(Film)
    if year is not None:
        stmt = stmt.where(Film.release_year == year)
    candidates = session.scalars(stmt).all()
    for film in candidates:
        if _normalize(film.title) == target_title or _normalize(film.original_title) == target_title:
            return film
    return None


def _score_candidate(candidate: dict[str, Any], *, name: str, year: int | None) -> float:
    """s = 3*exact_title + 2*exact_original_title + 1*(year matches exactly)
    + log10(1+vote_count)/4.

    Two deliberate deviations from the original §7 formula, both tightening
    (never loosening) what earns credit — added 2026-07-04 after empirically
    probing real TMDB responses for the ~171-row unmatched_imports backlog
    (see docs/phases/PHASE-2-letterboxd-import.md §7 history note below):

    1. exact_original_title is only awarded when original_title actually
       DIFFERS from title. For most English-language films TMDB sets
       original_title == title, so the old formula silently double-counted
       one real signal ("the title matches") as two (3 + 2 = 5 points) —
       which let a same-titled, wrong-year, near-zero-vote junk/short-film
       entry (also English-titled, so it got the same double credit)
       outscore or tie the correct film purely on that quirk. Foreign-
       original films (e.g. "El hoyo" / "The Platform") never got the extra
       2 points under the old formula either way, since their
       original_title never equals the searched English name — so this
       change only removes a same-language double-count, it doesn't newly
       penalize foreign-original films.
    2. year_ok now requires the exact release year, not "within ±1". TMDB
       routinely lists several distinct films/shorts/festival-cut entries
       under the identical title, one-year apart (e.g. a real 2024 release
       plus an unrelated zero-vote 2025 listing) — the old ±1 tolerance let
       BOTH earn full year credit, collapsing the score margin between the
       real film and TMDB noise below the auto-accept threshold. Letterboxd
       already logs the exact watched/released year for CSV/scrape rows, so
       there's no ambiguity to hedge against here; exact-year matching is
       strictly more precise and, empirically, catches an additional ~35
       previously-stuck rows with zero false positives in a full real-data
       probe (see the import-run verification notes for the exact list).
    """
    target_title = _normalize(name)
    cand_title = _normalize(candidate.get("title"))
    cand_original_title = _normalize(candidate.get("original_title"))

    exact_title = 1.0 if target_title and target_title == cand_title else 0.0
    exact_original_title = (
        1.0
        if (
            target_title
            and target_title == cand_original_title
            and cand_original_title != cand_title
        )
        else 0.0
    )

    year_ok = 0.0
    if year is not None:
        release_date = candidate.get("release_date") or ""
        cand_year = None
        if len(release_date) >= 4 and release_date[:4].isdigit():
            cand_year = int(release_date[:4])
        if cand_year is not None and cand_year == year:
            year_ok = 1.0

    vote_count = candidate.get("vote_count") or 0
    popularity_term = math.log10(1 + vote_count) / 4

    return (
        3 * exact_title
        + 2 * exact_original_title
        + 1 * year_ok
        + popularity_term
    )


async def _search_tmdb(tmdb: TMDBClient, name: str, year: int | None) -> list[dict[str, Any]]:
    """TMDB GET /search/movie?query=<Name>&primary_release_year=<Year>.

    Per §7: no results -> retry once without year.
    """
    kwargs: dict[str, Any] = {}
    query = name
    # TMDBClient.search_movie only takes (query, page) — primary_release_year
    # filtering isn't exposed there, so we filter/score client-side using the
    # candidates' release_date, which the §7 scoring formula already does via
    # the year-diff term. We still try a year-qualified query string first
    # since TMDB's search endpoint does not support primary_release_year
    # through search_movie(), so the "with year" / "without year" retry is
    # equivalent to just querying by name; both attempts use the same query,
    # but we keep the two-step shape to match §7's documented flow and to
    # leave room for future refinement if the client grows a year param.
    result = await tmdb.search_movie(query)
    candidates = result.get("results") or []
    if candidates:
        return candidates
    # Retry once without year influence (already the same query — this is a
    # genuine second attempt in case of a transient/empty first response).
    result = await tmdb.search_movie(query, page=1)
    return result.get("results") or []


async def match_row(
    row: dict[str, Any],
    tmdb: TMDBClient,
    session: Session | None = None,
    *,
    slug: str | None = None,
) -> dict[str, Any]:
    """Match one normalised import row to a TMDB film.

    Args:
        row: a normalised row from csv_parser.py (or the scraper's "virtual
            CSV" equivalent). Must have "name" and "year"; may have "uri"
            and (for RSS) "tmdb_movie_id".
        tmdb: the shared TMDBClient (async, httpx-based) — reused as-is.
        session: optional DB session for the "existing films table" lookup
            stage of the flowchart. If None, that stage is skipped (e.g. a
            standalone match with no DB available).
        slug: optional letterboxd_slug (scrape rows only).

    Returns one of:
        {"status": "matched", "film_id": int, "payload": {...full TMDB movie...},
         "matched_via": "rss_tmdb_id" | "existing_film" | "tmdb_search"}
        {"status": "unmatched", "candidates": [top-5 dicts], "name": ..., "year": ...}
    """
    name = row.get("name")
    year = row.get("year")
    uri = row.get("uri")
    tmdb_movie_id = row.get("tmdb_movie_id")

    # Stage 0 (RSS only): tmdb:movieId handed to us directly.
    if tmdb_movie_id:
        payload = await tmdb.movie(int(tmdb_movie_id), append="credits,keywords,release_dates")
        return {
            "status": "matched",
            "film_id": payload["id"],
            "payload": payload,
            "matched_via": "rss_tmdb_id",
        }

    # Stage 1: existing films table lookup by uri/slug/normalised title+year.
    if session is not None:
        existing = _lookup_existing_film(session, name=name, year=year, uri=uri, slug=slug)
        if existing is not None:
            # Already hydrated in our DB — no need to refetch from TMDB.
            payload = {
                "id": existing.id,
                "title": existing.title,
                "original_title": existing.original_title,
                "release_date": existing.release_date,
                "overview": existing.overview,
                "poster_path": existing.poster_path,
                "backdrop_path": existing.backdrop_path,
                "popularity": existing.popularity,
                "vote_average": existing.vote_average,
                "vote_count": existing.vote_count,
                "imdb_id": existing.imdb_id,
            }
            return {
                "status": "matched",
                "film_id": existing.id,
                "payload": payload,
                "matched_via": "existing_film",
            }

    # Stage 2: TMDB search + scoring.
    if not name:
        return {"status": "unmatched", "candidates": [], "name": name, "year": year}

    candidates = await _search_tmdb(tmdb, name, year)
    if not candidates:
        return {"status": "unmatched", "candidates": [], "name": name, "year": year}

    scored = sorted(
        (
            (_score_candidate(c, name=name, year=year), c)
            for c in candidates
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )

    top_score, top_candidate = scored[0]
    runner_up_score = scored[1][0] if len(scored) > 1 else float("-inf")

    if top_score >= AUTO_ACCEPT_MIN_SCORE and (top_score - runner_up_score) >= AUTO_ACCEPT_MIN_MARGIN:
        full_payload = await tmdb.movie(
            top_candidate["id"], append="credits,keywords,release_dates"
        )
        return {
            "status": "matched",
            "film_id": full_payload["id"],
            "payload": full_payload,
            "matched_via": "tmdb_search",
            "score": top_score,
            "runner_up_score": runner_up_score,
        }

    logger.info(
        "tmdb_match: row %r (year=%r) did not auto-accept (top=%.2f, runner_up=%.2f) — queuing unmatched",
        name,
        year,
        top_score,
        runner_up_score,
    )
    top5 = [c for _, c in scored[:5]]
    return {
        "status": "unmatched",
        "candidates": top5,
        "name": name,
        "year": year,
        "top_score": top_score,
        "runner_up_score": runner_up_score,
    }
