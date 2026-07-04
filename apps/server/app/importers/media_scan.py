"""Local media library scanner — docs/phases/PHASE-7-local-media-tv.md §2.

Walks the configured media roots for video files, upserts `media_files` rows,
and attempts to match each file to a TMDB film id so owned-but-unwatched
titles can show up in the Cat-alogue/recommendations even with zero
streaming availability.

Matching order (§2): Jellyfin's own library join (it already scraped TMDB
metadata for us) when a Jellyfin server is configured, otherwise a best-effort
filename parse + TMDB search, falling back to leaving the file unmatched for
manual resolution (same UX pattern as the Letterboxd unmatched-imports queue).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.jellyfin import JellyfinClient, JellyfinError
from ..clients.tmdb import TMDBClient, TMDBError
from ..models import MediaFile

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".ts"}

# Strips common scene-release noise (resolution, codec, source tags, and
# anything in brackets/parens) so a filename like
# "The.Fall.2006.1080p.BluRay.x264-GROUP.mkv" reduces to a clean search
# query "The Fall" + year 2006.
_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_NOISE_RE = re.compile(
    r"\b(1080p|720p|2160p|4k|bluray|brrip|webrip|web-dl|webdl|hdtv|dvdrip|x264|x265|"
    r"h264|h265|hevc|aac|ac3|dts|remux|proper|extended|unrated)\b",
    re.IGNORECASE,
)
_BRACKETED_RE = re.compile(r"[\[\(].*?[\]\)]")


def parse_filename(stem: str) -> tuple[str, int | None]:
    """Best-effort (title, year) guess from a filename stem (no extension)."""
    year_match = _YEAR_RE.search(stem)
    year = int(year_match.group(0)) if year_match else None

    cleaned = stem.replace(".", " ").replace("_", " ")
    cleaned = _BRACKETED_RE.sub(" ", cleaned)
    if year_match:
        cleaned = cleaned[: year_match.start()]
    cleaned = _NOISE_RE.sub(" ", cleaned)
    title = re.sub(r"\s+", " ", cleaned).strip(" -")
    return title, year


@dataclass
class MediaScanReport:
    roots_scanned: list[str]
    files_found: int = 0
    files_new: int = 0
    files_removed: int = 0
    auto_matched: int = 0
    unmatched: int = 0
    errors: list[str] = field(default_factory=list)


async def scan_media_roots(
    session: Session,
    tmdb: TMDBClient,
    jellyfin: JellyfinClient,
    roots: list[str],
) -> MediaScanReport:
    report = MediaScanReport(roots_scanned=roots)

    found_paths: set[str] = set()
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            report.errors.append(f"{root}: not a directory or not accessible")
            continue
        for path in root_path.rglob("*"):
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                found_paths.add(str(path))

    report.files_found = len(found_paths)

    # Drop rows whose file no longer exists on disk.
    existing = {m.path: m for m in session.scalars(select(MediaFile)).all()}
    for path, row in existing.items():
        if path not in found_paths:
            session.delete(row)
            report.files_removed += 1

    # Jellyfin join (primary matching strategy, §2.1): path -> tmdb id.
    jellyfin_by_path: dict[str, tuple[str, int]] = {}
    if jellyfin.configured:
        try:
            items = await jellyfin.library_items_with_tmdb_ids()
            for item in items:
                item_path = item.get("Path")
                tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
                if item_path and tmdb_id:
                    jellyfin_by_path[item_path] = (item["Id"], int(tmdb_id))
        except JellyfinError:
            logger.warning("media_scan: Jellyfin library lookup failed", exc_info=True)

    for path in found_paths:
        row = existing.get(path)
        is_new = row is None
        if row is None:
            row = MediaFile(path=path)
            session.add(row)
            report.files_new += 1

        try:
            row.size_bytes = Path(path).stat().st_size
        except OSError:
            pass

        if row.film_id is not None:
            continue  # already matched (manually or a previous scan)

        jf_match = jellyfin_by_path.get(path)
        if jf_match:
            jellyfin_item_id, tmdb_id = jf_match
            row.jellyfin_item_id = jellyfin_item_id
            await _try_match(session, tmdb, row, tmdb_id, report)
            continue

        if is_new:
            title, year = parse_filename(Path(path).stem)
            if title:
                await _try_filename_match(session, tmdb, row, title, year, report)

    session.commit()
    return report


async def _try_match(
    session: Session, tmdb: TMDBClient, row: MediaFile, tmdb_id: int, report: MediaScanReport
) -> None:
    from ..routers.films import _get_or_hydrate_film_by_id

    try:
        film = await _get_or_hydrate_film_by_id(tmdb_id, tmdb, session)
        row.film_id = film.id
        report.auto_matched += 1
    except TMDBError:
        logger.warning("media_scan: hydrate failed for tmdb id %s", tmdb_id, exc_info=True)
        report.unmatched += 1


async def _try_filename_match(
    session: Session,
    tmdb: TMDBClient,
    row: MediaFile,
    title: str,
    year: int | None,
    report: MediaScanReport,
) -> None:
    if not tmdb.configured:
        report.unmatched += 1
        return
    try:
        results = await tmdb.search_movie(title)
    except TMDBError:
        report.unmatched += 1
        return

    candidates = results.get("results", [])
    if year is not None:
        year_matches = [
            c for c in candidates
            if (c.get("release_date") or "")[:4] == str(year)
        ]
        if year_matches:
            candidates = year_matches

    # Only auto-match when the top hit is an unambiguous single strong
    # candidate — anything murkier is left for the manual-match UI rather
    # than risk silently attaching the wrong film (same caution as the
    # Letterboxd CSV matcher, PHASE-2 §2).
    if len(candidates) == 1 or (
        candidates and (len(candidates) < 2 or candidates[0].get("popularity", 0) > 2 * candidates[1].get("popularity", 1))
    ):
        await _try_match(session, tmdb, row, candidates[0]["id"], report)
    else:
        report.unmatched += 1
