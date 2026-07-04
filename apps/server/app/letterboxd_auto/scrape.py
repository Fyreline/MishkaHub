"""Source 2 — public profile scrape (docs/phases/PHASE-2-letterboxd-import.md §3).

``run_scrape`` runs in a FRESH, SIGNED-OUT Playwright context (no credentials
at risk) and walks:

  * ``/<user>/films/``        — every watched film + current rating/like
                                (undated),
  * ``/<user>/films/diary/``  — dated entries (adds watched_date, rewatch).

The films grid is public to plain HTTP, but the diary page sits behind a
Cloudflare challenge for plain clients (verified live 2026-07-03: 403 with
``cf-mitigated: challenge``), so BOTH pages are fetched with the real browser
(``page.goto`` + ``page.content()``) and parsed with BeautifulSoup4.

Every row is normalised to the SAME "virtual CSV" dict shape emitted by
``app/importers/csv_parser.py`` (name/year/uri/rating/rewatch/tags/
watched_date/review/log_date) so parsing/matching/merging is one code path
for all backfill sources — plus a few scrape-only extras
(``slug``, ``liked``, ``viewing_id``) the docs call out (§3a, §3b).

Politeness (§3c): sequential fetches with 2.5s ± 1s jitter between pages,
hard cap of 100 pages per list per run.
"""
from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from . import selectors
from .session import USER_AGENT

if TYPE_CHECKING:  # pragma: no cover
    from playwright.async_api import Page

logger = logging.getLogger(__name__)

PAGE_CAP = 100  # hard cap per list per run (§3c)
JITTER_BASE = 2.5  # seconds
JITTER_SPREAD = 1.0  # +/- seconds


def _normalised_row(
    *,
    name: str | None,
    year: int | None,
    uri: str | None,
    rating: float | None,
    rewatch: bool,
    watched_date: str | None,
    slug: str | None,
    liked: bool,
    viewing_id: str | None,
) -> dict:
    """Build a row matching csv_parser's virtual-CSV shape, plus scrape extras."""
    return {
        # ---- csv_parser.py virtual-CSV shape ----
        "name": name,
        "year": year,
        "uri": uri,
        "rating": rating,
        "rewatch": rewatch,
        "tags": [],
        "watched_date": watched_date,
        "review": None,
        "log_date": None,
        # ---- scrape-only extras (§3a/§3b: slug + liked + viewing id) ----
        "slug": slug,
        "liked": liked,
        "viewing_id": viewing_id,
    }


def _split_name_year(display: str | None) -> tuple[str | None, int | None]:
    """Split "Toy Story 4 (2019)" -> ("Toy Story 4", 2019). Year optional."""
    if not display:
        return None, None
    display = display.strip()
    if display.endswith(")") and " (" in display:
        head, _, tail = display.rpartition(" (")
        year_str = tail[:-1].strip()
        if year_str.isdigit():
            return head.strip(), int(year_str)
    return display, None


async def _jitter() -> None:
    delay = JITTER_BASE + random.uniform(-JITTER_SPREAD, JITTER_SPREAD)
    delay = max(0.5, delay)
    await asyncio.sleep(delay)


def parse_films_page(html: str) -> list[dict]:
    """Parse one /<user>/films/ grid page into normalised rows."""
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(selectors.FILMS_GRID_ITEM)
    if not items:
        items = soup.select(selectors.FILMS_GRID_ITEM_FALLBACK)

    rows: list[dict] = []
    for li in items:
        comp = li.select_one(selectors.POSTER_COMPONENT)
        if comp is None:
            # Fallback: any element carrying a slug attribute.
            comp = li.find(attrs={selectors.ATTR_ITEM_SLUG: True}) or li.find(
                attrs={selectors.ATTR_ITEM_SLUG_FALLBACK: True}
            )
        if comp is None:
            continue

        slug = comp.get(selectors.ATTR_ITEM_SLUG) or comp.get(
            selectors.ATTR_ITEM_SLUG_FALLBACK
        )
        display = comp.get(selectors.ATTR_ITEM_NAME) or comp.get(
            selectors.ATTR_ITEM_FULL_NAME
        )
        name, year = _split_name_year(display)
        item_link = comp.get(selectors.ATTR_ITEM_LINK)
        uri = f"{selectors.BASE_URL}{item_link}" if item_link else (
            f"{selectors.BASE_URL}/film/{slug}/" if slug else None
        )

        rating_span = li.select_one(selectors.RATING_SPAN)
        rating = None
        if rating_span is not None:
            rating = selectors.rated_class_to_stars(rating_span.get("class", []))

        liked = li.select_one(selectors.LIKE_ICON) is not None

        rows.append(
            _normalised_row(
                name=name,
                year=year,
                uri=uri,
                rating=rating,
                rewatch=False,
                watched_date=None,
                slug=slug,
                liked=liked,
                viewing_id=None,
            )
        )
    return rows


def _diary_has_next(html: str, current_page: int) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return _grid_has_next(soup, current_page)


def _grid_has_next(soup: BeautifulSoup, current_page: int) -> bool:
    """True if a 'next page' link exists in the paginate block."""
    nxt = soup.select_one(selectors.PAGINATE_NEXT)
    if nxt is not None:
        return True
    # Fallback: any paginate anchor to page current+1.
    for a in soup.select(f"{selectors.PAGINATE_BLOCK} a"):
        href = a.get("href", "")
        if f"/page/{current_page + 1}/" in href:
            return True
    return False


def parse_diary_page(html: str) -> list[dict]:
    """Parse one /<user>/films/diary/ page into normalised rows."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one(selectors.DIARY_TABLE)
    if table is None:
        return []

    rows: list[dict] = []
    for tr in table.select(selectors.DIARY_ROW):
        viewing_id = tr.get(selectors.ATTR_VIEWING_ID)

        comp = tr.find(attrs={selectors.ATTR_ITEM_SLUG: True}) or tr.find(
            attrs={selectors.ATTR_ITEM_SLUG_FALLBACK: True}
        )
        slug = None
        display = None
        if comp is not None:
            slug = comp.get(selectors.ATTR_ITEM_SLUG) or comp.get(
                selectors.ATTR_ITEM_SLUG_FALLBACK
            )
            display = comp.get(selectors.ATTR_ITEM_NAME) or comp.get(
                selectors.ATTR_ITEM_FULL_NAME
            )
        name, year = _split_name_year(display)

        # Watched date from the daydate cell link (…/for/YYYY/MM/DD/).
        watched_date = None
        daydate_cell = tr.select_one(selectors.DIARY_DAYDATE_CELL)
        if daydate_cell is not None:
            link = daydate_cell.select_one(selectors.DIARY_DAYDATE_LINK)
            if link is not None:
                href = link.get("href", "")
                # …/films/diary/for/2026/06/25/ -> 2026-06-25
                parts = [p for p in href.split("/") if p]
                if "for" in parts:
                    i = parts.index("for")
                    ymd = parts[i + 1 : i + 4]
                    if len(ymd) == 3 and all(p.isdigit() for p in ymd):
                        watched_date = "-".join(ymd)

        # Rating.
        rating = None
        rating_cell = tr.select_one(selectors.DIARY_RATING_CELL)
        if rating_cell is not None:
            span = rating_cell.select_one(selectors.RATING_SPAN)
            if span is not None:
                rating = selectors.rated_class_to_stars(span.get("class", []))

        # Like.
        like_cell = tr.select_one(selectors.DIARY_LIKE_CELL)
        liked = like_cell is not None and (
            like_cell.select_one(selectors.LIKE_ICON) is not None
        )

        # Rewatch: the rewatch cell LACKS icon-status-off when it IS a rewatch.
        rewatch = False
        rewatch_cell = tr.select_one(selectors.DIARY_REWATCH_CELL)
        if rewatch_cell is not None:
            inner = rewatch_cell.find(class_=True)
            classes = inner.get("class", []) if inner is not None else []
            rewatch = selectors.REWATCH_OFF_CLASS not in classes

        rows.append(
            _normalised_row(
                name=name,
                year=year,
                uri=(f"{selectors.BASE_URL}/film/{slug}/" if slug else None),
                rating=rating,
                rewatch=rewatch,
                watched_date=watched_date,
                slug=slug,
                liked=liked,
                viewing_id=viewing_id,
            )
        )
    return rows


async def _fetch(page: "Page", url: str) -> tuple[str, str]:
    """Navigate to ``url`` and return (html, lowercased_page_title).

    The title is the reliable Cloudflare-interstitial signal ("Just a
    moment…" / "Attention Required"); the raw HTML is what BeautifulSoup
    parses. We check the title rather than substring-scanning content, which
    would false-positive on incidental strings in the page source.
    """
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    # Give lazy-loaded posters / a managed Cloudflare challenge a beat to settle.
    await page.wait_for_timeout(800)
    try:
        title = (await page.title()).lower()
    except Exception:  # pragma: no cover
        title = ""
    return await page.content(), title


def _is_cloudflare_title(title: str) -> bool:
    return any(
        frag in title
        for frag in ("just a moment", "attention required", "verify you are human")
    )


async def _fetch_with_cf_retry(page: "Page", url: str) -> tuple[str, str]:
    """Fetch ``url``; if a Cloudflare interstitial is seen, wait politely for
    the managed challenge to clear (it often self-resolves in a real browser)
    and retry ONCE. Returns (html, lowercased_title) from the final attempt.

    Verified live (2026-07-03): even the "public" films grid intermittently
    returns the "Just a moment…" challenge to headless Chromium, so a single
    polite retry meaningfully improves completeness without hammering.
    """
    html, title = await _fetch(page, url)
    if _is_cloudflare_title(title):
        # Give Cloudflare's managed challenge a chance to auto-clear in-place,
        # then re-load once.
        await page.wait_for_timeout(5000)
        await _jitter()
        html, title = await _fetch(page, url)
    return html, title


async def run_scrape(user_id: int, username: str) -> dict:
    """Scrape the public films grid + diary for ``username`` (signed-out).

    Returns a dict: {outcome, films: [...], diary: [...], counts, detail?}.
    ``outcome`` is 'ok', 'cloudflare_challenge', or 'error'.
    """
    from playwright.async_api import async_playwright

    films_rows: list[dict] = []
    diary_rows: list[dict] = []

    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    try:
        # Fresh, signed-out context (§3b) — no storage_state.
        context = await browser.new_context(user_agent=USER_AGENT, locale="en-GB")
        page = await context.new_page()

        # ---- A. Films grid ----
        films_cf_blocked = False
        for page_no in range(1, PAGE_CAP + 1):
            url = selectors.films_url(username, page_no)
            try:
                html, title = await _fetch_with_cf_retry(page, url)
            except Exception as exc:
                if page_no == 1:
                    return {
                        "outcome": "error",
                        "detail": f"films page 1 fetch failed: {type(exc).__name__}: {exc}",
                        "films": films_rows,
                        "diary": diary_rows,
                    }
                logger.warning("run_scrape: films page %d fetch failed: %s", page_no, exc)
                break

            if _is_cloudflare_title(title):
                # Challenge persisted through the retry.
                if page_no == 1:
                    return {
                        "outcome": "cloudflare_challenge",
                        "detail": f"Cloudflare challenge on films page 1 (title={title!r})",
                        "films": films_rows,
                        "diary": diary_rows,
                    }
                # Mid-pagination: stop cleanly, remember it was a challenge (not
                # a genuine end-of-list), so the caller can tell them apart.
                logger.warning(
                    "run_scrape: Cloudflare challenge on films page %d — "
                    "stopping films pagination with %d rows so far",
                    page_no, len(films_rows),
                )
                films_cf_blocked = True
                break

            page_rows = parse_films_page(html)
            films_rows.extend(page_rows)

            soup = BeautifulSoup(html, "html.parser")
            if not page_rows or not _grid_has_next(soup, page_no):
                break
            await _jitter()

        # ---- B. Diary ----
        diary_cf_blocked = False
        for page_no in range(1, PAGE_CAP + 1):
            url = selectors.diary_url(username, page_no)
            try:
                html, title = await _fetch_with_cf_retry(page, url)
            except Exception as exc:
                if page_no == 1:
                    # Could not even load diary page 1 — likely the Cloudflare
                    # challenge the docs warn about.
                    return {
                        "outcome": "cloudflare_challenge",
                        "detail": f"diary page 1 fetch failed: {type(exc).__name__}: {exc}",
                        "films": films_rows,
                        "diary": diary_rows,
                    }
                logger.warning("run_scrape: diary page %d fetch failed: %s", page_no, exc)
                break

            if _is_cloudflare_title(title) and "diary-table" not in html.lower():
                if page_no == 1:
                    return {
                        "outcome": "cloudflare_challenge",
                        "detail": (
                            f"Cloudflare challenge persisted on diary page 1 "
                            f"(title={title!r})"
                        ),
                        "films": films_rows,
                        "diary": diary_rows,
                    }
                logger.warning(
                    "run_scrape: Cloudflare challenge on diary page %d — "
                    "stopping diary pagination with %d rows so far",
                    page_no, len(diary_rows),
                )
                diary_cf_blocked = True
                break

            page_rows = parse_diary_page(html)
            diary_rows.extend(page_rows)

            if not page_rows or not _diary_has_next(html, page_no):
                break
            await _jitter()

        # 'ok' if we got here; note partial coverage if a mid-run challenge
        # truncated either list (the caller can decide whether that's good
        # enough or should be retried later).
        result = {
            "outcome": "ok",
            "films": films_rows,
            "diary": diary_rows,
            "counts": {"films": len(films_rows), "diary": len(diary_rows)},
        }
        if films_cf_blocked or diary_cf_blocked:
            result["partial"] = True
            result["detail"] = (
                "completed with partial coverage — a Cloudflare challenge "
                "truncated pagination "
                f"(films_blocked={films_cf_blocked}, diary_blocked={diary_cf_blocked})"
            )
        return result
    finally:
        try:
            await browser.close()
        except Exception:
            pass
        await pw.stop()
