"""Header-driven CSV parser for Letterboxd export files.

Per docs/phases/PHASE-2-letterboxd-import.md §6: every file is parsed with
``csv.DictReader`` (UTF-8) and rows are NEVER indexed by column position —
only by header name. Letterboxd publishes no official spec for exact header
order, so this is a structural mitigation against drift.

Every parser in this module returns the same normalised "virtual CSV" shape
(a list of dicts) regardless of which source file it came from:

    {
        "name": str,                 # film title as logged by Letterboxd
        "year": int | None,          # release year as logged by Letterboxd
        "uri": str | None,           # Letterboxd URI for this row (film page
                                      # for watched/ratings/likes; the specific
                                      # diary entry for diary/reviews — see §6
                                      # "store it on the watch row, don't treat
                                      # it as a film key")
        "rating": float | None,      # 0.5-5.0 in 0.5 steps
        "rewatch": bool,             # Rewatch column ("Yes" -> True)
        "tags": list[str],           # parsed from the comma-delimited Tags column
        "watched_date": str | None,  # "Watched Date" column (viewing date)
        "review": str | None,        # Review column (may contain HTML)
        "log_date": str | None,      # "Date" column: when the ROW was created
                                      # on Letterboxd, NOT the watch date
    }

This same shape is what the (later, separate) profile scraper is expected to
emit, so parsing / matching / merging / counting is one code path for every
backfill source (§6 "the scraper emits rows in this same normalised shape").

Resilience contract (§6): if a file's headers don't match what's expected,
log a warning and skip malformed rows rather than crashing. Each parser
function returns ``(rows, skipped_count)`` so callers can surface counts in
the import_runs record.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _empty_row_result() -> tuple[list[dict], int]:
    return [], 0


def _parse_year(raw: str | None) -> int | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("csv_parser: could not parse year %r", raw)
        return None


def _parse_rating(raw: str | None) -> float | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("csv_parser: could not parse rating %r", raw)
        return None


def _parse_rewatch(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() == "yes"


def _parse_tags(raw: str | None) -> list[str]:
    if raw is None:
        return []
    raw = raw.strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def _parse_optional_str(raw: str | None) -> str | None:
    if raw is None:
        return None
    raw = raw.strip()
    return raw or None


def _open_dict_reader(path: str | Path) -> tuple[csv.DictReader | None, list]:
    """Open a Letterboxd export CSV for header-driven reading.

    Returns (reader, rows_list) where rows_list has already been fully read
    into memory (so we can iterate more than once / count easily), or
    (None, []) if the file doesn't exist.
    """
    p = Path(path)
    if not p.exists():
        logger.warning("csv_parser: file not found: %s", p)
        return None, []
    with p.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    return fieldnames, rows


def _read_rows(path: str | Path) -> tuple[list[str], list[dict]]:
    p = Path(path)
    if not p.exists():
        logger.warning("csv_parser: file not found: %s", p)
        return [], []
    with p.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    return fieldnames, rows


def _check_headers(fieldnames: list[str], required: set[str], source_file: str) -> bool:
    missing = required - set(fieldnames)
    if missing:
        logger.warning(
            "csv_parser: %s headers missing expected columns %s (found %s) — "
            "skipping this file's rows",
            source_file,
            sorted(missing),
            fieldnames,
        )
        return False
    return True


def parse_watched(path: str | Path) -> tuple[list[dict], int]:
    """Parse watched.csv: Date,Name,Year,Letterboxd URI -> normalised rows."""
    required = {"Date", "Name", "Year", "Letterboxd URI"}
    fieldnames, raw_rows = _read_rows(path)
    if not fieldnames:
        return [], 0
    if not _check_headers(fieldnames, required, "watched.csv"):
        return [], len(raw_rows)

    rows: list[dict] = []
    skipped = 0
    for raw in raw_rows:
        name = _parse_optional_str(raw.get("Name"))
        if not name:
            skipped += 1
            logger.warning("csv_parser: watched.csv row missing Name, skipping: %r", raw)
            continue
        rows.append(
            {
                "name": name,
                "year": _parse_year(raw.get("Year")),
                "uri": _parse_optional_str(raw.get("Letterboxd URI")),
                "rating": None,
                "rewatch": False,
                "tags": [],
                "watched_date": None,
                "review": None,
                "log_date": _parse_optional_str(raw.get("Date")),
            }
        )
    return rows, skipped


def parse_ratings(path: str | Path) -> tuple[list[dict], int]:
    """Parse ratings.csv: Date,Name,Year,Letterboxd URI,Rating -> normalised rows."""
    required = {"Date", "Name", "Year", "Letterboxd URI", "Rating"}
    fieldnames, raw_rows = _read_rows(path)
    if not fieldnames:
        return [], 0
    if not _check_headers(fieldnames, required, "ratings.csv"):
        return [], len(raw_rows)

    rows: list[dict] = []
    skipped = 0
    for raw in raw_rows:
        name = _parse_optional_str(raw.get("Name"))
        if not name:
            skipped += 1
            logger.warning("csv_parser: ratings.csv row missing Name, skipping: %r", raw)
            continue
        rows.append(
            {
                "name": name,
                "year": _parse_year(raw.get("Year")),
                "uri": _parse_optional_str(raw.get("Letterboxd URI")),
                "rating": _parse_rating(raw.get("Rating")),
                "rewatch": False,
                "tags": [],
                "watched_date": None,
                "review": None,
                "log_date": _parse_optional_str(raw.get("Date")),
            }
        )
    return rows, skipped


def parse_diary(path: str | Path) -> tuple[list[dict], int]:
    """Parse diary.csv: Date,Name,Year,Letterboxd URI,Rating,Rewatch,Tags,Watched Date.

    Per §6: the Letterboxd URI here points at the specific diary entry, not
    the film page — callers must store it on the watch row, not treat it as
    a film key.
    """
    required = {
        "Date",
        "Name",
        "Year",
        "Letterboxd URI",
        "Rating",
        "Rewatch",
        "Tags",
        "Watched Date",
    }
    fieldnames, raw_rows = _read_rows(path)
    if not fieldnames:
        return [], 0
    if not _check_headers(fieldnames, required, "diary.csv"):
        return [], len(raw_rows)

    rows: list[dict] = []
    skipped = 0
    for raw in raw_rows:
        name = _parse_optional_str(raw.get("Name"))
        if not name:
            skipped += 1
            logger.warning("csv_parser: diary.csv row missing Name, skipping: %r", raw)
            continue
        rows.append(
            {
                "name": name,
                "year": _parse_year(raw.get("Year")),
                "uri": _parse_optional_str(raw.get("Letterboxd URI")),
                "rating": _parse_rating(raw.get("Rating")),
                "rewatch": _parse_rewatch(raw.get("Rewatch")),
                "tags": _parse_tags(raw.get("Tags")),
                "watched_date": _parse_optional_str(raw.get("Watched Date")),
                "review": None,
                "log_date": _parse_optional_str(raw.get("Date")),
            }
        )
    return rows, skipped


def parse_reviews(path: str | Path) -> tuple[list[dict], int]:
    """Parse reviews.csv: Date,Name,Year,Letterboxd URI,Rating,Rewatch,Review,Tags,Watched Date."""
    required = {
        "Date",
        "Name",
        "Year",
        "Letterboxd URI",
        "Rating",
        "Rewatch",
        "Review",
        "Tags",
        "Watched Date",
    }
    fieldnames, raw_rows = _read_rows(path)
    if not fieldnames:
        return [], 0
    if not _check_headers(fieldnames, required, "reviews.csv"):
        return [], len(raw_rows)

    rows: list[dict] = []
    skipped = 0
    for raw in raw_rows:
        name = _parse_optional_str(raw.get("Name"))
        if not name:
            skipped += 1
            logger.warning("csv_parser: reviews.csv row missing Name, skipping: %r", raw)
            continue
        rows.append(
            {
                "name": name,
                "year": _parse_year(raw.get("Year")),
                "uri": _parse_optional_str(raw.get("Letterboxd URI")),
                "rating": _parse_rating(raw.get("Rating")),
                "rewatch": _parse_rewatch(raw.get("Rewatch")),
                "tags": _parse_tags(raw.get("Tags")),
                "watched_date": _parse_optional_str(raw.get("Watched Date")),
                "review": _parse_optional_str(raw.get("Review")),
                "log_date": _parse_optional_str(raw.get("Date")),
            }
        )
    return rows, skipped


def parse_likes_films(path: str | Path) -> tuple[list[dict], int]:
    """Parse likes/films.csv: Date,Name,Year,Letterboxd URI -> normalised rows."""
    required = {"Date", "Name", "Year", "Letterboxd URI"}
    fieldnames, raw_rows = _read_rows(path)
    if not fieldnames:
        return [], 0
    if not _check_headers(fieldnames, required, "likes/films.csv"):
        return [], len(raw_rows)

    rows: list[dict] = []
    skipped = 0
    for raw in raw_rows:
        name = _parse_optional_str(raw.get("Name"))
        if not name:
            skipped += 1
            logger.warning("csv_parser: likes/films.csv row missing Name, skipping: %r", raw)
            continue
        rows.append(
            {
                "name": name,
                "year": _parse_year(raw.get("Year")),
                "uri": _parse_optional_str(raw.get("Letterboxd URI")),
                "rating": None,
                "rewatch": False,
                "tags": [],
                "watched_date": None,
                "review": None,
                "log_date": _parse_optional_str(raw.get("Date")),
            }
        )
    return rows, skipped
