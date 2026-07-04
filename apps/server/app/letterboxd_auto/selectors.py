"""The ONE file with Letterboxd DOM selectors.

Per docs/phases/PHASE-2-letterboxd-import.md §11 and PHASE-5's "selectors in
one file" rule: this is the only module to touch when Letterboxd's markup
changes. Everything below was verified live against letterboxd.com on
2026-07-03 (see the phase docs' "Real facts already verified live" notes).

Two groups:
  * SIGN-IN — the login form on /sign-in/ (used by session.py / ensure_session).
  * SCRAPE  — the public /<user>/films/ grid and /<user>/films/diary/ table
              (used by scrape.py; parsed with BeautifulSoup4).
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# URLs
# --------------------------------------------------------------------------
BASE_URL = "https://letterboxd.com"
SIGN_IN_URL = f"{BASE_URL}/sign-in/"
HOME_URL = f"{BASE_URL}/"
EXPORT_URL = f"{BASE_URL}/data/export/"


def films_url(username: str, page: int = 1) -> str:
    if page <= 1:
        return f"{BASE_URL}/{username}/films/"
    return f"{BASE_URL}/{username}/films/page/{page}/"


def diary_url(username: str, page: int = 1) -> str:
    if page <= 1:
        return f"{BASE_URL}/{username}/films/diary/"
    return f"{BASE_URL}/{username}/films/diary/page/{page}/"


def profile_url(username: str) -> str:
    return f"{BASE_URL}/{username}/"


# --------------------------------------------------------------------------
# SIGN-IN form (verified 2026-07-03: form action="/user/login.do" method=post,
# class js-sign-in-form; hidden input name="__csrf" (fresh per page load);
# username field name/id "username"/"field-username"; password field
# name/id "password"/"field-password"; submit button type=submit, starts
# disabled and JS-enables once fields are filled).
# --------------------------------------------------------------------------
SIGN_IN_FORM = "form.js-sign-in-form"
CSRF_INPUT = 'input[type="hidden"][name="__csrf"]'
USERNAME_INPUT = "#field-username"
PASSWORD_INPUT = "#field-password"
SUBMIT_BUTTON = "form.js-sign-in-form button[type='submit']"

# A signed-in session exposes the member's own nav avatar/profile link
# (href="/<username>/") and hides the "Sign in" link. ensure_session checks
# for the profile link to confirm the rehydrated session is still valid.
SIGN_IN_NAV_LINK = "a[href='/sign-in/']"


def profile_nav_link_selector(username: str) -> str:
    """CSS selector matching the signed-in member's own avatar/profile link."""
    return f"a.nav-account[href='/{username}/'], a[href='/{username}/'].avatar"


# Challenge detection.
#
# NB (verified live 2026-07-03): the bare string "captcha" appears in the
# normal signed-out /sign-in/ and homepage HTML (Letterboxd ships an invisible
# captcha widget script), so a raw substring scan of page.content() produces
# false positives. Challenge detection must therefore look at VISIBLE elements
# / interstitial titles, not the raw HTML. session.py does that.

# CSS selectors for a genuinely rendered captcha / verification widget.
CHALLENGE_ELEMENT_SELECTORS = (
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[title*='captcha' i]",
    "div.g-recaptcha",
    "div.h-captcha",
    "#cf-challenge-running",
    "#challenge-form",
)

# Fragments that only appear in a Cloudflare "Just a moment…" interstitial or a
# genuine 2FA/verify screen — matched against the PAGE TITLE (short, reliable)
# and visible body text, never the raw HTML source.
CHALLENGE_TITLE_FRAGMENTS = (
    "just a moment",
    "attention required",
    "verify you are human",
)
CHALLENGE_VISIBLE_TEXT_FRAGMENTS = (
    "verify your identity",
    "verification code",
    "two-factor",
    "confirm it's you",
    "are you a robot",
)

# Visible-text fragments indicating rejected credentials (distinct from a
# challenge — both keep us on /sign-in/).
BAD_CREDENTIALS_TEXT_FRAGMENTS = (
    "incorrect username or password",
    "your username or password",
    "credentials were incorrect",
    "the username or password",
)


# --------------------------------------------------------------------------
# SCRAPE — /<user>/films/ grid (verified 2026-07-03; 72 items/page).
# --------------------------------------------------------------------------
FILMS_GRID_ITEM = "div.poster-grid > ul.grid > li.griditem"
# Fallback grid item selector (older markup uses ul.poster-list > li.poster-container).
FILMS_GRID_ITEM_FALLBACK = "ul.poster-list > li, ul.grid > li"

# The poster's react-component div carries the film metadata attributes.
POSTER_COMPONENT = "div.react-component[data-item-slug], div.react-component[data-film-slug]"
ATTR_ITEM_SLUG = "data-item-slug"
ATTR_ITEM_SLUG_FALLBACK = "data-film-slug"
ATTR_ITEM_NAME = "data-item-name"  # e.g. "Toy Story 4 (2019)"
ATTR_ITEM_FULL_NAME = "data-item-full-display-name"
ATTR_ITEM_LINK = "data-item-link"  # /film/<slug>/

# Rating + like live in the sibling viewing-data paragraph.
RATING_SPAN = "span.rating"  # also carries class rated-<N>, N = stars x 2
LIKE_ICON = "span.icon-liked"

# Pagination: the paginate block; next link lives in .paginate-nextprev.
PAGINATE_BLOCK = "div.paginate-pages"
PAGINATE_NEXT = "div.paginate-nextprev a.next, .paginate-nextprev a.next"


# --------------------------------------------------------------------------
# SCRAPE — /<user>/films/diary/ table (verified 2026-07-03; Cloudflare-gated,
# needs a real browser). table#diary-table; rows tr.diary-entry-row.
# --------------------------------------------------------------------------
DIARY_TABLE = "table#diary-table"
DIARY_ROW = "tr.diary-entry-row"
ATTR_VIEWING_ID = "data-viewing-id"
ATTR_FILM_ID = "data-film-id"

# Cells inside a diary row are addressed by their td class (td.td-*).
DIARY_DAYDATE_CELL = "td.td-day, td.td-daydate"
DIARY_DAYDATE_LINK = "a"  # href .../films/diary/for/YYYY/MM/DD/
DIARY_RATING_CELL = "td.td-rating"
DIARY_LIKE_CELL = "td.td-like"
DIARY_REWATCH_CELL = "td.td-rewatch"
DIARY_REVIEW_CELL = "td.td-review"

# rewatch cell LACKS class icon-status-off when the entry IS a rewatch.
REWATCH_OFF_CLASS = "icon-status-off"


def rated_class_to_stars(class_list: list[str]) -> float | None:
    """Map a ``rated-<N>`` CSS class (N = stars x 2, 1..10) to a 0.5-5.0 float.

    Returns None if no ``rated-<N>`` class is present (unrated).
    """
    for cls in class_list:
        if cls.startswith("rated-"):
            suffix = cls[len("rated-"):]
            try:
                n = int(suffix)
            except ValueError:
                continue
            if 1 <= n <= 10:
                return n / 2.0
    return None
