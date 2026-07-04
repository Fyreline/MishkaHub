"""Candidate generation — docs/phases/PHASE-3-recommender.md §3.

Grows the local `films` table with fresh, GB-available candidates so the
per-user taste model (§4) and scoring (§5), plus /similar and /lucky, have
something *new* to rank — not just the films the household has already
interacted with. This matters more than the §4/§5 model itself: the two
users have watched 208 and 947 of the ~1,470 films they'd imported, so the
"eligible for at least one" pool /similar and the `together` profile draw
from was thin. A big fresh corpus is the fix.

Sources, all upserted + fully hydrated via the SAME merge/hydrate path
`films.py` uses:

  A. GET /discover/movie across the household's subscribed providers
     (`with_watch_providers=<pipe-joined>&watch_region=GB&include_adult=false`),
     run as SEVERAL strategies so we sweep a genuinely wide slice of the
     GB-streamable universe rather than only the popularity head:
       1. popularity.desc, vote_count.gte=50            (the mainstream head)
       2. vote_average.desc, vote_count.gte=200         (acclaimed / deep cuts,
          the vote-count floor keeps it from surfacing 3-vote flukes)
       3. primary_release_date.desc, vote_count.gte=20  (what's new/recent)
       4. per-genre popularity.desc for the household's most-liked genres
          (vote_count.gte=50, with_genres=<id>) — deepens the genres this
          couple actually reaches for so those shelves aren't shallow.
  B. GET /movie/{id}/recommendations for each user's top-rated films
     (catches low-popularity gems the discover sweeps miss).

CORPUS TARGET (documented, not silent): live TMDB probing (2026-07-04) shows
the provider-constrained GB universe is far bigger than one popularity sweep
reaches — 7,670 popularity-head titles, 3,754 acclaimed (vote_average.desc,
vc>=200), 11,516 by recency (vc>=20), plus thousands per top genre. The
previous pass capped a SINGLE popularity sweep at 12 pages (~240 films). This
pass targets roughly 4–5k TOTAL films, enough to give both users and the
`together` profile a deep eligible pool, while staying inside a single
on-request retrain's budget (a few minutes wall-clock). A first sweep at
40/25/20/8-page caps grew 1,541 -> 2,539 (998 new, ~3.5 min); the caps below
are deepened (60/40/40/20) to push toward the 4–5k target — the marginal
returns fall off (heavy inter-strategy overlap), which is why we stop here
rather than sweeping the full 7k+ head. Re-running retrain is cheap once the
corpus is warm (already-hydrated films are skipped), so depth can grow
incrementally across runs without a single monster request.

The binding cost is HYDRATION, not discovery: every genuinely-new film needs
one `/movie/{id}` call for credits+keywords (discovery is ~20 films/call).
So the discover page caps below are sized to yield ~that many new titles
after dedup, and MAX_NEW_HYDRATIONS is a hard ceiling so a single request
can't run unbounded — anything past the ceiling is left as a thin row for a
later sweep to hydrate (thin rows are excluded from the recommender corpus,
which only reads metadata_json-hydrated films, so this degrades gracefully).
Expect a few minutes wall-clock for a full first sweep; that's the documented
tradeoff, not a silent truncation. The full nightly-refresh job (§3 "pool
refresh: nightly job") remains the real long-term home; every cap here is a
module constant so it's trivially tuned.

Reuses `importers/merge.py:upsert_film` for the DB write and the exact
`/movie/{id}?append_to_response=credits,keywords,release_dates` hydration
call `films.py:_get_or_hydrate_film` uses — no reimplementation.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.tmdb import TMDBClient, TMDBError
from ..importers.merge import upsert_film
from ..models import Film, Like, Rating, Subscription

logger = logging.getLogger(__name__)

# --- Discover strategy page caps (20 films/page). See module docstring for
# why these numbers and the TMDB-probe evidence behind them. ---
# Strategy 1: popularity.desc (mainstream head). Bumped from the old 12.
DISCOVER_PAGES = 60
# Strategy 2: vote_average.desc with a firm vote-count floor (acclaimed pool).
ACCLAIMED_PAGES = 40
ACCLAIMED_VOTE_COUNT_GTE = 200
# Strategy 3: primary_release_date.desc (recent releases). Lower vote floor so
# genuinely-new titles that haven't accrued votes yet still surface.
RECENT_PAGES = 40
RECENT_VOTE_COUNT_GTE = 20
# Strategy 4: per-genre popularity sweeps, seeded by the household's most-liked
# genres. TOP_GENRES_SEED genres × GENRE_PAGES_EACH pages each. Deep enough
# (per-genre) to reach titles the global popularity/acclaimed heads miss — the
# first ~8 pages of a genre overlap those heads almost entirely, so the tail is
# where genre-seeding actually earns its keep (see the per_strategy report:
# early genre pages contribute ~0 new, deep pages add real breadth).
TOP_GENRES_SEED = 6
GENRE_PAGES_EACH = 20
GENRE_VOTE_COUNT_GTE = 50
# Shared default vote-count floor for the popularity + genre sweeps.
DISCOVER_VOTE_COUNT_GTE = 50

# Source B: /movie/{id}/recommendations for each user's top-rated films.
# How many top-rated films per user to pull /recommendations for (§3 point 3
# says top-20; scoped to 10 to keep this a bounded number of extra calls).
TOP_RATED_PER_USER = 10
# Pages of /recommendations to pull per seed film (1 page = 20 films).
RECS_PAGES_PER_SEED = 1

# Hard ceiling on /movie/{id} hydration calls in ONE sweep — the dominant
# time/rate cost. New films beyond this are left as thin rows (no
# metadata_json), which the recommender corpus simply ignores until a later
# sweep hydrates them. Sized for a few-minute worst case at TMDB's rate: the
# first full sweep at these caps hydrated ~1.8k films in ~3.5 min with room to
# spare, so 4000 leaves headroom for a deeper run while still bounding it.
MAX_NEW_HYDRATIONS = 4000


@dataclass
class CandidateGenReport:
    discover_seen: int = 0
    recs_seen: int = 0
    films_before: int = 0
    films_after: int = 0
    newly_inserted: int = 0
    newly_hydrated: int = 0
    hydration_skipped: int = 0  # new thin rows left un-hydrated (hit the cap)
    tmdb_calls: int = 0
    # Per-strategy distinct-id contribution (for the retrain report / docs).
    per_strategy: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _subscribed_provider_ids(session: Session) -> list[int]:
    return list(
        session.scalars(
            select(Subscription.provider_id).where(Subscription.active == 1)
        ).all()
    )


def _top_household_genre_ids(session: Session, limit: int) -> list[int]:
    """The household's most-liked TMDB genre ids, most-frequent first.

    "Most-liked" = union of liked films and films either user rated >=4.0,
    tallied over the genres in each film's hydrated metadata_json. Used to
    seed the per-genre discover sweeps so the shelves this couple actually
    reaches for get deepened. Falls back to an empty list (skips the per-genre
    strategy) if nothing qualifies.
    """
    fids: set[int] = set()
    fids.update(fid for (fid,) in session.execute(select(Like.film_id)).all())
    fids.update(
        fid for (fid,) in session.execute(
            select(Rating.film_id).where(Rating.rating >= 4.0)
        ).all()
    )
    counts: Counter[int] = Counter()
    for fid in fids:
        film = session.get(Film, fid)
        if film is None or not film.metadata_json:
            continue
        try:
            meta = json.loads(film.metadata_json)
        except (json.JSONDecodeError, TypeError):
            continue
        for g in (meta.get("genres") or []):
            gid = g.get("id")
            if gid is not None:
                counts[gid] += 1
    return [gid for gid, _ in counts.most_common(limit)]


def _is_hydrated(film: Film) -> bool:
    """True if the film already carries a full TMDB payload (credits+keywords),
    i.e. it doesn't need a second /movie/{id} hydrate call.

    upsert_film() stores metadata_json=None for "thin" payloads (the shape
    /discover and /recommendations return: id/title/overview/etc. but NO
    credits/keywords). So a null metadata_json, or one missing the credits or
    keywords sub-keys, means "needs hydration".
    """
    if not film.metadata_json:
        return False
    try:
        meta = json.loads(film.metadata_json)
    except (json.JSONDecodeError, TypeError):
        return False
    return "credits" in meta and "keywords" in meta


async def _hydrate_film(
    session: Session, tmdb: TMDBClient, film_id: int, report: CandidateGenReport
) -> None:
    """Fetch full metadata for one film and upsert it (fills credits/keywords).

    Same call shape as films.py:_get_or_hydrate_film. Commits are owned by the
    caller (generate_candidates commits in batches).
    """
    try:
        payload = await tmdb.movie(film_id, append="credits,keywords,release_dates")
        report.tmdb_calls += 1
    except TMDBError as exc:
        report.errors.append(f"hydrate {film_id}: {exc}")
        return
    upsert_film(session, payload)
    report.newly_hydrated += 1


async def generate_candidates(
    session: Session, tmdb: TMDBClient, *, hydrate: bool = True
) -> CandidateGenReport:
    """Run the full §3 candidate-generation sweep against live TMDB.

    Upserts every discovered/recommended film into `films` (thin rows first,
    then a full hydrate pass so features.py has credits+keywords). Returns a
    report with real before/after counts. Commits in batches so a mid-sweep
    failure still persists progress.
    """
    report = CandidateGenReport()
    report.films_before = session.query(Film).count()

    provider_ids = _subscribed_provider_ids(session)
    if not provider_ids:
        report.errors.append("no active subscriptions — candidate pool cannot be built")
        report.films_after = report.films_before
        return report

    with_providers = "|".join(str(pid) for pid in sorted(provider_ids))
    region = tmdb._settings.region  # noqa: SLF001 — deliberate, TMDBClient exposes no getter

    seen_ids: set[int] = set()

    async def _discover_sweep(label: str, pages: int, **discover_kwargs) -> None:
        """Page a single /discover strategy, upserting each NEW id once.

        Dedup within the whole sweep: TMDB returns the same id across pages AND
        across strategies; upserting the same NEW id twice before a flush
        creates two pending Film(id=X) objects (session.get doesn't autoflush
        here), tripping a UNIQUE films.id violation at commit. The thin data is
        identical anyway, so upsert each id at most once per sweep. Records the
        distinct-id contribution of this strategy in report.per_strategy.
        """
        before = len(seen_ids)
        for page in range(1, pages + 1):
            try:
                data = await tmdb.discover_movies(
                    with_watch_providers=with_providers,
                    watch_region=region,
                    page=page,
                    **discover_kwargs,
                )
                report.tmdb_calls += 1
            except TMDBError as exc:
                report.errors.append(f"discover[{label}] page {page}: {exc}")
                break
            results = data.get("results") or []
            if not results:
                break  # ran past the last page of this strategy
            for row in results:
                fid = row.get("id")
                if fid is None:
                    continue
                report.discover_seen += 1
                if fid in seen_ids:
                    continue
                seen_ids.add(fid)
                upsert_film(session, row)  # thin upsert; None metadata if new
            session.commit()
        report.per_strategy[label] = len(seen_ids) - before

    # --- Source A: multi-strategy /discover sweep (see module docstring) ---
    # 1) mainstream head
    await _discover_sweep(
        "popularity", DISCOVER_PAGES,
        sort_by="popularity.desc", vote_count_gte=DISCOVER_VOTE_COUNT_GTE,
    )
    # 2) acclaimed / deep cuts (firm vote-count floor)
    await _discover_sweep(
        "acclaimed", ACCLAIMED_PAGES,
        sort_by="vote_average.desc", vote_count_gte=ACCLAIMED_VOTE_COUNT_GTE,
    )
    # 3) recent releases (lower vote floor so genuinely-new titles surface)
    await _discover_sweep(
        "recent", RECENT_PAGES,
        sort_by="primary_release_date.desc", vote_count_gte=RECENT_VOTE_COUNT_GTE,
    )
    # 4) per-genre deepening, seeded by the household's most-liked genres
    for gid in _top_household_genre_ids(session, TOP_GENRES_SEED):
        await _discover_sweep(
            f"genre:{gid}", GENRE_PAGES_EACH,
            sort_by="popularity.desc", vote_count_gte=GENRE_VOTE_COUNT_GTE,
            with_genres=str(gid),
        )

    # --- Source B: /movie/{id}/recommendations for each user's top-10 rated ---
    user_ids = [uid for (uid,) in session.execute(select(Rating.user_id).distinct()).all()]
    for uid in user_ids:
        top_rated = session.execute(
            select(Rating.film_id)
            .where(Rating.user_id == uid)
            .order_by(Rating.rating.desc(), Rating.updated_at.desc())
            .limit(TOP_RATED_PER_USER)
        ).all()
        for (seed_fid,) in top_rated:
            for page in range(1, RECS_PAGES_PER_SEED + 1):
                try:
                    data = await tmdb.movie_recommendations(seed_fid, page=page)
                    report.tmdb_calls += 1
                except TMDBError as exc:
                    report.errors.append(f"recs seed {seed_fid} p{page}: {exc}")
                    break
                results = data.get("results") or []
                if not results:
                    break
                for row in results:
                    fid = row.get("id")
                    if fid is None:
                        continue
                    report.recs_seen += 1
                    if fid in seen_ids:
                        continue  # already staged this sweep (see discover note)
                    seen_ids.add(fid)
                    upsert_film(session, row)
        session.commit()

    # --- Hydrate pass: fill credits+keywords for anything not yet full ---
    # HYDRATION is the dominant cost (one /movie/{id} call each), so cap it at
    # MAX_NEW_HYDRATIONS per sweep. Anything past the cap stays a thin row
    # (metadata_json=None), which the recommender corpus ignores until a later
    # sweep hydrates it — graceful degradation, not a silent drop. See the
    # module docstring for the tradeoff.
    if hydrate:
        to_hydrate: list[int] = []
        for fid in seen_ids:
            film = session.get(Film, fid)
            if film is not None and not _is_hydrated(film):
                to_hydrate.append(fid)
        capped = to_hydrate[:MAX_NEW_HYDRATIONS]
        report.hydration_skipped = len(to_hydrate) - len(capped)
        logger.info(
            "candidate-gen: hydrating %d of %d un-hydrated films "
            "(%d discovered/recommended total; %d skipped at cap %d)",
            len(capped), len(to_hydrate), len(seen_ids),
            report.hydration_skipped, MAX_NEW_HYDRATIONS,
        )
        if report.hydration_skipped:
            report.errors.append(
                f"hydration cap {MAX_NEW_HYDRATIONS} hit — "
                f"{report.hydration_skipped} thin rows left for a later sweep"
            )
        for i, fid in enumerate(capped, start=1):
            await _hydrate_film(session, tmdb, fid, report)
            if i % 25 == 0:
                session.commit()
        session.commit()

    report.films_after = session.query(Film).count()
    report.newly_inserted = report.films_after - report.films_before
    return report
