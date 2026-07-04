"""Shared row/ZIP processing + the background import-run cascade.

This module closes the gap between ``app/letterboxd_auto/{export,scrape}.py``
(complete, working automation that RETURNS data) and actually persisting that
data — see docs/phases/PHASE-2-letterboxd-import.md §3b "Scrape flow" and §8
"Dedup & cross-source merge rules".

Two things live here:

1. ``process_rows`` / ``process_zip_bytes`` — the TMDB-match + merge.py-upsert
   pipeline, factored out of the original inline logic in
   ``routers/import_.upload_letterboxd_export`` so the manual-ZIP-upload
   endpoint, the automated export cascade, and the scrape cascade all share
   ONE code path instead of three copies of the same loop.
2. ``run_cascade_background`` — the actual auto/export/scrape/rss cascade,
   run inside ``asyncio.create_task`` so ``POST /api/import/letterboxd/run``
   can return 202 immediately (per PHASE-2 §11 "background work" convention)
   instead of blocking the HTTP response for the minutes a full 100-page
   scrape can take. Uses its own ``SessionLocal()`` since it outlives the
   request's dependency-injected session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from ..db import SessionLocal
from ..models import ImportRun, User
from . import csv_parser
from .merge import queue_unmatched, upsert_film, upsert_like, upsert_rating, upsert_review, upsert_watch
from .tmdb_match import match_row

logger = logging.getLogger(__name__)

EXPORT_DIR = DATA_DIR / "letterboxd_exports"

EMPTY_COUNTS: dict[str, int] = {
    "watched": 0,
    "diary": 0,
    "ratings": 0,
    "likes": 0,
    "reviews": 0,
    "matched": 0,
    "unmatched": 0,
    "skipped_duplicates": 0,
}


async def process_rows(
    session,
    tmdb,
    user_id: int,
    rows: list[dict],
    row_kind: str,
    source: str,
    counts: dict[str, int],
) -> None:
    """Match + upsert one list of normalised rows (csv_parser/scrape shape).

    ``row_kind`` drives which upserts apply (mirrors the original inline
    logic in the upload endpoint):
      - "diary" / "watched" / "reviews" / "films_grid" -> upsert_watch
      - any row with rating is not None -> upsert_rating
      - row_kind == "reviews" and row has review text -> upsert_review
      - row_kind == "likes" -> upsert_like only (no watch/rating)

    ``source`` is stamped on every row written (e.g. "letterboxd-import",
    "letterboxd-scrape") per §8's per-row source column.
    ``counts`` is mutated in place (matched/unmatched/watched/diary/etc).
    """
    for row in rows:
        slug = row.get("slug")
        match = await match_row(row, tmdb, session, slug=slug)
        if match["status"] == "unmatched":
            queue_unmatched(
                session,
                user_id=user_id,
                source_file=row_kind,
                raw_row=row,
                name=row.get("name"),
                year=row.get("year"),
                letterboxd_uri=row.get("uri"),
            )
            counts["unmatched"] += 1
            continue

        film = upsert_film(session, match["payload"])
        counts["matched"] += 1

        if row_kind == "likes":
            if row.get("liked", True):
                upsert_like(session, user_id=user_id, film_id=film.id, source=source)
                counts["likes"] += 1
            session.flush()
            continue

        if row_kind in ("diary", "watched", "reviews", "films_grid"):
            upsert_watch(
                session,
                user_id=user_id,
                film_id=film.id,
                watched_date=row.get("watched_date"),
                rewatch=row.get("rewatch", False),
                tags=row.get("tags"),
                source=source,
                letterboxd_uri=row.get("uri"),
            )
            counts["watched" if row_kind != "diary" else "diary"] += 1

        if row.get("rating") is not None:
            upsert_rating(
                session,
                user_id=user_id,
                film_id=film.id,
                rating=row["rating"],
                source=source,
                rated_at=row.get("watched_date") or row.get("log_date"),
            )
            counts["ratings"] += 1

        if row_kind == "reviews" and row.get("review"):
            upsert_review(
                session,
                user_id=user_id,
                film_id=film.id,
                review_text=row["review"],
                source=source,
                watched_date=row.get("watched_date"),
            )
            counts["reviews"] += 1

        # Scrape rows carry "liked" alongside films_grid/diary kinds too
        # (§3b: the films grid and diary both report current like state).
        if row_kind in ("films_grid", "diary") and row.get("liked"):
            upsert_like(session, user_id=user_id, film_id=film.id, source=source)
            counts["likes"] += 1

        session.flush()


async def process_zip_bytes(
    session,
    tmdb,
    user_id: int,
    raw_bytes: bytes,
    run_id: int,
) -> dict[str, int]:
    """Unzip a Letterboxd export ZIP and run every CSV through process_rows.

    Mirrors exactly what ``routers/import_.upload_letterboxd_export`` did
    inline; factored out so the automated export cascade can call the same
    code instead of duplicating it.
    """
    counts = dict(EMPTY_COUNTS)

    with zipfile.ZipFile(BytesIO(raw_bytes)) as zf:
        names = zf.namelist()

        def _extract_and_parse(candidates: list[str], parser_fn):
            for cand in candidates:
                matches = [n for n in names if n.endswith(cand)]
                if matches:
                    with zf.open(matches[0]) as fh:
                        tmp_path = EXPORT_DIR / f"_tmp_{run_id}_{Path(cand).name}"
                        tmp_path.write_bytes(fh.read())
                    rows, skipped = parser_fn(tmp_path)
                    tmp_path.unlink(missing_ok=True)
                    return rows, skipped
            return [], 0

        EXPORT_DIR.mkdir(parents=True, exist_ok=True)

        diary_rows, diary_skipped = _extract_and_parse(["diary.csv"], csv_parser.parse_diary)
        ratings_rows, ratings_skipped = _extract_and_parse(["ratings.csv"], csv_parser.parse_ratings)
        watched_rows, watched_skipped = _extract_and_parse(["watched.csv"], csv_parser.parse_watched)
        reviews_rows, reviews_skipped = _extract_and_parse(["reviews.csv"], csv_parser.parse_reviews)
        likes_rows, likes_skipped = _extract_and_parse(["likes/films.csv"], csv_parser.parse_likes_films)

        counts["skipped_duplicates"] = (
            diary_skipped + ratings_skipped + watched_skipped + reviews_skipped + likes_skipped
        )

        await process_rows(session, tmdb, user_id, diary_rows, "diary", "letterboxd-import", counts)
        await process_rows(session, tmdb, user_id, watched_rows, "watched", "letterboxd-import", counts)
        await process_rows(session, tmdb, user_id, ratings_rows, "ratings", "letterboxd-import", counts)
        await process_rows(session, tmdb, user_id, reviews_rows, "reviews", "letterboxd-import", counts)
        for row in likes_rows:
            row.setdefault("liked", True)
        await process_rows(session, tmdb, user_id, likes_rows, "likes", "letterboxd-import", counts)

    return counts


async def _jitter_short() -> None:
    await asyncio.sleep(0.2 + random.uniform(0, 0.3))


async def run_cascade_background(run_id: int, user_id: int, source_requested: str) -> None:
    """The actual auto/export/scrape/rss cascade, run out-of-band.

    Opens its OWN session (the request's dependency-injected session is
    closed by the time this task runs, since the endpoint already returned
    202). Every exception is caught and recorded on the ImportRun row rather
    than propagating — an unhandled exception here would just vanish into
    ``asyncio`` task-exception limbo since nothing awaits this task.
    """
    from ..clients.tmdb import TMDBClient  # local import: avoid cycles at module load
    from ..config import get_settings

    session = SessionLocal()
    try:
        run = session.get(ImportRun, run_id)
        user = session.get(User, user_id)
        if run is None or user is None:
            logger.error("run_cascade_background: run=%s user=%s missing", run_id, user_id)
            return

        settings = get_settings()
        tmdb = TMDBClient(settings)
        try:
            await _execute_cascade(session, tmdb, run, user, source_requested)
        finally:
            await tmdb.aclose()
    except Exception as exc:  # noqa: BLE001 — must never crash the loop/server
        logger.exception("run_cascade_background: run %s crashed: %s", run_id, exc)
        try:
            session.rollback()
            run = session.get(ImportRun, run_id)
            if run is not None:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"
                run.finished_at = datetime.now(timezone.utc).isoformat()
                session.commit()
        except Exception:
            logger.exception("run_cascade_background: failed to record failure for run %s", run_id)
    finally:
        session.close()


async def _execute_cascade(session, tmdb, run: ImportRun, user: User, source_requested: str) -> None:
    from ..importers.rss import poll_user
    from ..letterboxd_auto.export import run_export
    from ..letterboxd_auto.scrape import run_scrape

    cascade: list[dict[str, Any]] = []

    async def _finish(source_used: str | None, status: str, counts: dict | None, error: str | None, stage: str | None = None) -> None:
        session.refresh(run)
        run.source_used = source_used
        run.status = status
        run.stage = stage
        run.cascade_json = json.dumps(cascade)
        if counts is not None:
            run.counts_json = json.dumps(counts)
        if error is not None:
            run.error = error
        run.finished_at = datetime.now(timezone.utc).isoformat()
        session.commit()

    async def _do_rss() -> None:
        if not user.letterboxd_username:
            cascade.append({"source": "rss", "outcome": "failed", "code": "no_username"})
            await _finish(None, "failed", None, "No letterboxd_username configured for this user")
            return
        result = await poll_user(session, tmdb, user.id, user.letterboxd_username)
        cascade.append(
            {"source": "rss", "outcome": "ok" if result["ok"] else "failed", "code": None if result["ok"] else "fetch_failed"}
        )
        counts = {
            **EMPTY_COUNTS,
            "watched": result.get("new_watches", 0),
            "diary": result.get("new_items", 0),
            "unmatched": len(result.get("errors", [])),
        }
        await _finish(
            "rss",
            "done" if result["ok"] else "failed",
            counts,
            None if result["ok"] else result.get("error"),
        )

    async def _do_scrape() -> bool:
        """Run the real scraper + match/merge pipeline. Returns True on a
        usable (ok/partial) outcome, False if it should fall through."""
        if not user.letterboxd_username:
            cascade.append({"source": "scrape", "outcome": "failed", "code": "no_username"})
            return False

        run.stage = "scrape:starting"
        session.commit()
        result = await run_scrape(user.id, user.letterboxd_username)
        outcome = result.get("outcome")

        if outcome not in ("ok",):
            cascade.append({"source": "scrape", "outcome": outcome, "code": result.get("detail")})
            return False

        counts = dict(EMPTY_COUNTS)
        films_rows = result.get("films") or []
        diary_rows = result.get("diary") or []

        run.stage = f"scrape:matching ({len(films_rows)} films, {len(diary_rows)} diary)"
        session.commit()

        # films-grid rows are undated watches; process them first, then dated
        # diary rows so upsert_watch's dateless-row-upgrade path (§8) fires.
        await process_rows(session, tmdb, user.id, films_rows, "films_grid", "letterboxd-scrape", counts)
        await process_rows(session, tmdb, user.id, diary_rows, "diary", "letterboxd-scrape", counts)

        cascade.append(
            {
                "source": "scrape",
                "outcome": "ok" if not result.get("partial") else "ok_partial",
                "code": result.get("detail") if result.get("partial") else None,
            }
        )
        await _finish("scrape", "done", counts, None, stage=None)
        return True

    async def _do_export() -> bool:
        if not user.letterboxd_username:
            cascade.append({"source": "export", "outcome": "failed", "code": "no_username"})
            return False

        run.stage = "export:starting"
        session.commit()
        result = await run_export(user.id, user.letterboxd_username)
        outcome = result.get("outcome")

        if outcome != "ok":
            cascade.append({"source": "export", "outcome": outcome, "code": result.get("detail")})
            return False

        zip_path = Path(result["path"])
        raw_bytes = zip_path.read_bytes()
        run.export_zip_path = str(zip_path)
        run.export_sha256 = result.get("sha256")
        run.stage = "export:matching"
        session.commit()

        counts = await process_zip_bytes(session, tmdb, user.id, raw_bytes, run.id)
        cascade.append({"source": "export", "outcome": "ok", "code": None})
        await _finish("export", "done", counts, None, stage=None)
        return True

    if source_requested == "rss":
        await _do_rss()
        return

    if source_requested == "export":
        ok = await _do_export()
        if not ok:
            await _finish(None, "failed", None, f"export automation did not succeed: {cascade[-1].get('code')}")
        return

    if source_requested == "scrape":
        ok = await _do_scrape()
        if not ok:
            await _finish(None, "failed", None, f"scrape automation did not succeed: {cascade[-1].get('code')}")
        return

    # source_requested == "auto": export -> scrape -> rss, first success wins.
    if await _do_export():
        return
    if await _do_scrape():
        return
    await _do_rss()
