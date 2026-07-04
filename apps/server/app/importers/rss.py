"""RSS polling importer — Phase 2 §4 ("Source 3 — RSS polling").

Polls ``https://letterboxd.com/<username>/rss/`` (public, no auth, sanctioned
feed) for each household member, incrementally, keeping the library current
between the weekly export (source 1) / scrape (source 2) backfills.

Key facts from docs/phases/PHASE-2-letterboxd-import.md §4 (verified live
2026-07-03 against both real household accounts):
  - guid formats: ``letterboxd-watch-<id>`` (plain log), ``letterboxd-review-
    <id>`` (log with review text), ``letterboxd-list-<id>`` (list activity —
    skipped entirely, no film/tmdb data attached to these anyway).
  - ``tmdb:movieId`` is handed to us directly for watch/review items — no
    title matching needed for RSS (unlike CSV/scrape).
  - ``letterboxd:memberRating`` is ABSENT (not present as a key at all) when
    the item is unrated. Confirmed live against garfieldsama's feed.
  - ``letterboxd:memberLike`` was observed BOTH absent and present-as-"No" in
    real feeds (garfieldsama's feed included explicit "No" values) — handled
    defensively either way.
  - The feed carries only the ~50 most recent diary/review entries (plus up
    to ~50 list items) — RSS is keep-fresh only, not a backfill source.

feedparser quirk (verified live): it does not expose a raw ``guid`` field —
the ``<guid>`` element value comes back as ``entry.id``. Namespaced elements
like ``<letterboxd:memberRating>`` come back flattened + lowercased as
``entry.letterboxd_memberrating`` (feedparser's standard namespace handling).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Film, SyncState, Watch
from .merge import upsert_film, upsert_like, upsert_rating, upsert_review, upsert_watch

logger = logging.getLogger(__name__)

RSS_USER_AGENT = "Mishka-Hub/0.x private household sync"
FEED_URL_TEMPLATE = "https://letterboxd.com/{username}/rss/"
MAX_CURSOR_GUIDS = 200

_FILM_SLUG_RE = re.compile(r"/film/([^/]+)/?")


def _slug_from_link(link: str | None) -> str | None:
    if not link:
        return None
    match = _FILM_SLUG_RE.search(link)
    return match.group(1) if match else None


def _guid_kind(guid: str) -> str | None:
    """Classify a Letterboxd RSS guid into 'watch' | 'review' | 'list'."""
    if guid.startswith("letterboxd-review-"):
        return "review"
    if guid.startswith("letterboxd-watch-"):
        return "watch"
    if guid.startswith("letterboxd-list-"):
        return "list"
    return None


def _extract_review_html(description: str | None) -> str | None:
    """Strip the leading poster ``<p><img .../></p>`` block, keep the rest.

    Letterboxd's RSS ``description`` is always poster-paragraph + body text
    (review prose for review-kind items, a plain "Watched on ..." sentence
    for watch-kind items). A single regex strip of the first ``<p>...</p>``
    when it contains an ``<img`` is sufficient for v1; a cleaner HTML-diff
    based extraction (e.g. comparing against the known "Watched on <date>."
    template) can be refined later if Letterboxd's markup drifts.
    """
    if not description:
        return None
    stripped = re.sub(r"^\s*<p>\s*<img[^>]*/?>\s*</p>\s*", "", description, count=1, flags=re.IGNORECASE)
    stripped = stripped.strip()
    return stripped or None


def _parse_bool_yes_no(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() == "yes"


def _parse_pub_date(entry: Any) -> str | None:
    parsed_time = entry.get("published_parsed")
    if not parsed_time:
        return None
    try:
        dt = datetime(*parsed_time[:6], tzinfo=timezone.utc)
        return dt.isoformat()
    except (TypeError, ValueError):
        return None


def _normalize_entry(entry: Any) -> dict[str, Any] | None:
    """Turn one feedparser entry into our normalized item dict, or None to skip."""
    guid = entry.get("id") or entry.get("guid")
    if not guid:
        return None
    kind = _guid_kind(guid)
    if kind is None or kind == "list":
        # List activity carries no film/tmdb data and is explicitly out of
        # scope per §4 ("letterboxd-list-<id> (skip — list activity)").
        return None

    tmdb_raw = entry.get("tmdb_movieid")
    tmdb_movie_id: int | None = None
    if tmdb_raw is not None:
        try:
            tmdb_movie_id = int(tmdb_raw)
        except (TypeError, ValueError):
            tmdb_movie_id = None

    member_rating_raw = entry.get("letterboxd_memberrating")
    member_rating: float | None = None
    if member_rating_raw is not None:
        try:
            member_rating = float(member_rating_raw)
        except (TypeError, ValueError):
            member_rating = None

    description = entry.get("summary")

    return {
        "guid": guid,
        "kind": kind,
        "link": entry.get("link"),
        "letterboxd_slug": _slug_from_link(entry.get("link")),
        "tmdb_movie_id": tmdb_movie_id,
        "watched_date": entry.get("letterboxd_watcheddate"),
        "rewatch": _parse_bool_yes_no(entry.get("letterboxd_rewatch")),
        "member_rating": member_rating,
        "member_liked": _parse_bool_yes_no(entry.get("letterboxd_memberlike")),
        "review_html": _extract_review_html(description) if kind == "review" else None,
        "pub_date": _parse_pub_date(entry),
    }


def fetch_feed(username: str) -> list[dict[str, Any]] | None:
    """Fetch + parse a member's Letterboxd RSS feed.

    Returns a list of normalized item dicts (newest first, matching feed
    order), or None on a non-200 response (politeness rule: skip the cycle,
    don't raise — caller/poll loop just tries again next cycle).
    """
    url = FEED_URL_TEMPLATE.format(username=username)
    headers = {"User-Agent": RSS_USER_AGENT}
    try:
        resp = httpx.get(url, headers=headers, timeout=15.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        logger.warning("RSS fetch failed for %s: %s", username, exc)
        return None

    if resp.status_code != 200:
        logger.warning("RSS fetch for %s returned non-200: %s", username, resp.status_code)
        return None

    parsed = feedparser.parse(resp.text)
    items: list[dict[str, Any]] = []
    for entry in parsed.entries:
        normalized = _normalize_entry(entry)
        if normalized is not None:
            items.append(normalized)
    return items


def _get_or_create_sync_state(session: Session, user_id: int) -> SyncState:
    stmt = select(SyncState).where(SyncState.kind == "rss", SyncState.user_id == user_id)
    state = session.execute(stmt).scalar_one_or_none()
    if state is None:
        state = SyncState(kind="rss", user_id=user_id, cursor=None, status=None)
        session.add(state)
        session.flush()
    return state


def _load_cursor_guids(state: SyncState) -> list[str]:
    if not state.cursor:
        return []
    try:
        data = json.loads(state.cursor)
    except (TypeError, ValueError):
        return []
    if isinstance(data, list):
        return [g for g in data if isinstance(g, str)]
    return []


async def _ensure_film(session: Session, tmdb_client: Any, tmdb_movie_id: int, letterboxd_slug: str | None) -> Film:
    """Upsert a Film row for a newly-seen TMDB id via the shared merge helper.

    RSS only ever gives us the id (no title matching needed, per §4/§7). If
    the film is already in the table, ``upsert_film`` is given a thin payload
    (id/slug only) — merge.py's "thin payload" guard means this won't clobber
    richer metadata_json hydrated by another source. If it's new, we fetch
    full metadata (poster, overview, credits/keywords/release_dates) from
    TMDB first so the poster wall has something to render immediately.
    """
    existing = session.get(Film, tmdb_movie_id)
    if existing is not None:
        thin_payload: dict[str, Any] = {"id": tmdb_movie_id}
        film = upsert_film(session, thin_payload)
        if letterboxd_slug and not film.letterboxd_slug:
            film.letterboxd_slug = letterboxd_slug
        return film

    payload: dict[str, Any] = {"id": tmdb_movie_id}
    try:
        payload = await tmdb_client.movie(tmdb_movie_id)
    except Exception as exc:  # noqa: BLE001 - TMDBError or transient httpx errors
        logger.warning("TMDB hydration failed for movie_id=%s: %s", tmdb_movie_id, exc)
        payload = {"id": tmdb_movie_id}

    film = upsert_film(session, payload)
    if letterboxd_slug and not film.letterboxd_slug:
        film.letterboxd_slug = letterboxd_slug
    session.flush()
    return film


async def poll_user(session: Session, tmdb_client: Any, user_id: int, username: str) -> dict[str, Any]:
    """Incrementally poll one user's Letterboxd RSS feed.

    Loads the (kind='rss', user_id) sync_state cursor (last <=200 seen
    guids), fetches the feed, processes only genuinely-new guids, upserts
    films/watches/ratings/likes/reviews with source='letterboxd-rss', then
    advances the cursor (most-recent-first, capped at 200) and updates
    last_run_at / last_ok_at / status. Returns a summary dict.
    """
    now = datetime.now(timezone.utc).isoformat()
    state = _get_or_create_sync_state(session, user_id)
    state.last_run_at = now

    items = fetch_feed(username)
    if items is None:
        state.status = "error"
        state.detail_json = json.dumps({"error": "non-200 or fetch failure"})
        session.commit()
        return {"ok": False, "new_items": 0, "guids_added": [], "error": "fetch_failed"}

    seen_guids = _load_cursor_guids(state)
    seen_set = set(seen_guids)

    new_items = [item for item in items if item["guid"] not in seen_set]

    guids_added: list[str] = []
    new_watch_count = 0
    errors: list[str] = []

    for item in new_items:
        guids_added.append(item["guid"])
        tmdb_movie_id = item.get("tmdb_movie_id")
        if tmdb_movie_id is None:
            # Per §4/§7, RSS items should always carry tmdb:movieId; if one
            # somehow doesn't, we can't place it without title matching
            # (out of scope for RSS) — record and skip.
            errors.append(f"{item['guid']}: missing tmdb_movie_id")
            continue

        try:
            film = await _ensure_film(session, tmdb_client, tmdb_movie_id, item.get("letterboxd_slug"))

            watch_existed_before = (
                session.execute(
                    select(Watch.id).where(Watch.letterboxd_guid == item["guid"])
                ).scalar_one_or_none()
                is not None
            )
            upsert_watch(
                session,
                user_id=user_id,
                film_id=film.id,
                watched_date=item.get("watched_date"),
                rewatch=item.get("rewatch", False),
                tags=None,
                source="letterboxd-rss",
                letterboxd_guid=item["guid"],
                letterboxd_uri=item.get("link"),
            )
            if not watch_existed_before:
                new_watch_count += 1

            if item.get("member_rating") is not None:
                upsert_rating(
                    session,
                    user_id=user_id,
                    film_id=film.id,
                    rating=item["member_rating"],
                    source="letterboxd-rss",
                    rated_at=item.get("watched_date"),
                )
            if item.get("member_liked"):
                upsert_like(session, user_id=user_id, film_id=film.id, source="letterboxd-rss")
            if item["kind"] == "review" and item.get("review_html"):
                upsert_review(
                    session,
                    user_id=user_id,
                    film_id=film.id,
                    review_text=item["review_html"],
                    source="letterboxd-rss",
                    watched_date=item.get("watched_date"),
                )
            session.flush()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            errors.append(f"{item['guid']}: {exc}")
            continue

    # Advance the cursor: most-recent-first union of new + previously-seen
    # guids, capped at MAX_CURSOR_GUIDS. `items` is already newest-first
    # (feed order), so this naturally keeps the most recent guids on top.
    all_guids_newest_first = [it["guid"] for it in items] + [g for g in seen_guids if g not in {it["guid"] for it in items}]
    state.cursor = json.dumps(all_guids_newest_first[:MAX_CURSOR_GUIDS])
    state.status = "ok"
    state.last_ok_at = now
    state.detail_json = json.dumps({"errors": errors}) if errors else None

    session.commit()

    return {
        "ok": True,
        "new_items": len(new_items),
        "new_watches": new_watch_count,
        "guids_added": guids_added,
        "errors": errors,
    }
