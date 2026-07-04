"""Playwright session management: ``ensure_session`` (login + persistence).

Implements docs/phases/PHASE-2-letterboxd-import.md §2b step 2 and
docs/phases/PHASE-2-credentials.md §4:

  1. Try to rehydrate a Fernet-encrypted ``storage_state`` blob (via
     ``load_session_blob``) into a fresh browser context and verify it is
     still signed in (the member's own profile/avatar link is present in the
     nav on the homepage).
  2. If there is no blob, or it is stale, perform a REAL login against
     ``/sign-in/``: read the fresh ``__csrf`` token from the DOM, fill the
     username/password fields, submit, and detect challenge / bad-credential
     pages.
  3. On success, export ``storage_state()`` and persist it via
     ``save_session_blob`` so subsequent runs skip the login form entirely.

This helper is built here in Phase 2 and reused verbatim by the Phase 5
write-back. The password only transits memory during an actual (re)login.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from ..config import get_settings
from ..secretstore import (
    LETTERBOXD_SERVICE,
    get_secret_store,
    load_session_blob,
    save_session_blob,
)
from . import selectors

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

# A realistic desktop User-Agent (politeness, PHASE-5 §2). Chromium's default
# headless UA advertises "HeadlessChrome"; override it.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# Outcome codes (mirror PHASE-2 §2c failure classes).
OUTCOME_OK = "ok"
OUTCOME_NO_CREDENTIALS = "no_credentials"
OUTCOME_LOGIN_CHALLENGE = "login_challenge"
OUTCOME_SELECTOR_BROKEN = "selector_broken"
OUTCOME_BAD_CREDENTIALS = "bad_credentials"
OUTCOME_ERROR = "error"


@dataclass
class SessionResult:
    """Result of ``ensure_session``.

    ``context`` is a live, signed-in Playwright ``BrowserContext`` when
    ``outcome == 'ok'`` and ``None`` otherwise. The caller owns closing the
    context (and the owning browser) — see ``export.py`` / ``scrape.py``.
    """

    outcome: str
    context: Optional["BrowserContext"] = None
    browser: Optional["Browser"] = None
    detail: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.outcome == OUTCOME_OK


async def _looks_signed_in(page: "Page", username: str) -> bool:
    """True if the current page shows the member's own signed-in nav link."""
    try:
        # Presence of the member's own profile/avatar link => signed in.
        sel = selectors.profile_nav_link_selector(username)
        loc = page.locator(sel)
        if await loc.count() > 0:
            return True
        # Fallback: any nav link to /<username>/ that isn't the sign-in link.
        generic = page.locator(f"nav a[href='/{username}/']")
        if await generic.count() > 0:
            return True
    except Exception:  # pragma: no cover - defensive
        return False
    return False


async def _visible_text(page: "Page") -> str:
    """Lowercased VISIBLE body text (not the raw HTML source).

    Using inner_text avoids false positives from strings that only live in
    <script>/<noscript>/hidden markup — e.g. Letterboxd ships an invisible
    captcha widget whose "captcha" string is in the source of every page but
    never rendered (verified live 2026-07-03).
    """
    try:
        return (await page.locator("body").inner_text(timeout=5000)).lower()
    except Exception:  # pragma: no cover
        return ""


async def _page_has_challenge(page: "Page") -> bool:
    """Detect a genuinely rendered captcha / 2FA / verification interstitial.

    Looks at (1) actually-present challenge WIDGET elements, (2) the page
    title, and (3) visible body text — never a raw substring of page.content(),
    which false-positives on Letterboxd's invisible captcha script.
    """
    # (1) A rendered challenge widget element.
    for sel in selectors.CHALLENGE_ELEMENT_SELECTORS:
        try:
            if await page.locator(sel).count() > 0:
                return True
        except Exception:  # pragma: no cover
            continue

    # (2) Interstitial page title (short + reliable, e.g. Cloudflare's).
    try:
        title = (await page.title()).lower()
    except Exception:  # pragma: no cover
        title = ""
    if any(frag in title for frag in selectors.CHALLENGE_TITLE_FRAGMENTS):
        return True

    # (3) Visible body text mentioning verification / 2FA.
    text = await _visible_text(page)
    return any(frag in text for frag in selectors.CHALLENGE_VISIBLE_TEXT_FRAGMENTS)


async def _page_has_bad_credentials(page: "Page") -> bool:
    text = await _visible_text(page)
    return any(frag in text for frag in selectors.BAD_CREDENTIALS_TEXT_FRAGMENTS)


async def _dump_failure(page: "Page", user_id: int, label: str) -> None:
    """Best-effort screenshot + DOM dump to data/playwright/failures/.

    Convention shared with PHASE-5 §5 / PHASE-2 §2c (selector_broken,
    login_challenge). Never raises — diagnostics must not break the caller.
    """
    try:
        from datetime import datetime, timezone

        from ..config import DATA_DIR

        out_dir = DATA_DIR / "playwright" / "failures"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        stem = out_dir / f"{label}_user{user_id}_{ts}"
        try:
            await page.screenshot(path=str(stem.with_suffix(".png")), full_page=True)
        except Exception:
            pass
        try:
            html = await page.content()
            stem.with_suffix(".html").write_text(html, encoding="utf-8")
        except Exception:
            pass
        logger.warning("ensure_session: %s — dumped diagnostics to %s.*", label, stem)
    except Exception:  # pragma: no cover
        pass


async def _new_context(browser: "Browser", storage_state: str | None = None):
    kwargs: dict = {"user_agent": USER_AGENT, "locale": "en-GB"}
    if storage_state is not None:
        # Playwright accepts storage_state as a JSON string path OR a dict; the
        # async API wants a dict or a file path, so parse the JSON here.
        import json

        kwargs["storage_state"] = json.loads(storage_state)
    return await browser.new_context(**kwargs)


async def _try_rehydrate(browser: "Browser", user_id: int, username: str):
    """Return a signed-in context rehydrated from the stored blob, or None."""
    blob = load_session_blob(user_id)
    if not blob:
        return None
    try:
        context = await _new_context(browser, storage_state=blob)
    except Exception as exc:  # malformed/old blob
        logger.warning("ensure_session: could not rehydrate blob for user %s: %s", user_id, exc)
        return None

    page = await context.new_page()
    try:
        await page.goto(selectors.HOME_URL, wait_until="domcontentloaded", timeout=30000)
        if await _looks_signed_in(page, username):
            await page.close()
            return context
    except Exception as exc:
        logger.warning("ensure_session: rehydrated session check failed: %s", exc)
    # Stale — discard.
    await page.close()
    await context.close()
    return None


async def _login(browser: "Browser", user_id: int, username: str, password: str) -> SessionResult:
    """Perform a real /sign-in login; persist the session on success."""
    context = await _new_context(browser)
    page = await context.new_page()
    try:
        await page.goto(selectors.SIGN_IN_URL, wait_until="domcontentloaded", timeout=30000)

        # Locate the form fields (selector_broken if the form is gone).
        try:
            await page.wait_for_selector(selectors.USERNAME_INPUT, timeout=15000)
            await page.wait_for_selector(selectors.PASSWORD_INPUT, timeout=15000)
        except Exception:
            await context.close()
            return SessionResult(
                outcome=OUTCOME_SELECTOR_BROKEN,
                detail="username/password fields not found on /sign-in/",
            )

        # Read the fresh CSRF token from the DOM (do not hardcode).
        csrf_el = await page.query_selector(selectors.CSRF_INPUT)
        if csrf_el is None:
            await context.close()
            return SessionResult(
                outcome=OUTCOME_SELECTOR_BROKEN,
                detail="__csrf hidden input not found on /sign-in/",
            )
        # (The token also rides in a cookie; filling+submitting the real form
        # sends both, so we don't need to read the value explicitly — but we
        # confirm it exists so a markup change surfaces as selector_broken.)

        await page.fill(selectors.USERNAME_INPUT, username)
        await page.fill(selectors.PASSWORD_INPUT, password)

        # Submit the form and wait for navigation / the AJAX response to settle.
        try:
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=20000):
                # The submit button JS-enables once fields are filled; click it.
                # If it is still disabled, fall back to submitting the form.
                btn = await page.query_selector(selectors.SUBMIT_BUTTON)
                if btn is not None and await btn.is_enabled():
                    await btn.click()
                else:
                    await page.eval_on_selector(
                        selectors.SIGN_IN_FORM, "form => form.submit()"
                    )
        except Exception:
            # No full navigation (Letterboxd signs in via AJAX then reloads);
            # give the page a moment to update in place.
            await page.wait_for_timeout(3000)

        # Give any post-login redirect a moment. Classify the LANDING page
        # first (that's where a challenge interstitial appears), THEN try the
        # homepage as a signed-in confirmation.
        await page.wait_for_timeout(1500)

        # Classify the immediate post-submit page BEFORE navigating away, so a
        # challenge interstitial isn't lost by a homepage redirect.
        landed_challenge = await _page_has_challenge(page)
        landed_bad_creds = await _page_has_bad_credentials(page)

        try:
            await page.goto(selectors.HOME_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass

        if await _looks_signed_in(page, username):
            storage_state = await context.storage_state()
            import json

            save_session_blob(user_id, json.dumps(storage_state))
            await page.close()
            return SessionResult(outcome=OUTCOME_OK, context=context, browser=browser)

        # Not signed in — classify why, using the landing page evidence plus a
        # re-check of the current page.
        if landed_challenge or await _page_has_challenge(page):
            await _dump_failure(page, user_id, "login_challenge")
            await context.close()
            return SessionResult(
                outcome=OUTCOME_LOGIN_CHALLENGE,
                detail="captcha/2FA/verification interstitial detected after login submit",
            )
        if landed_bad_creds or await _page_has_bad_credentials(page):
            await context.close()
            return SessionResult(
                outcome=OUTCOME_BAD_CREDENTIALS,
                detail="login rejected: incorrect username or password",
            )
        await _dump_failure(page, user_id, "login_incomplete")
        await context.close()
        return SessionResult(
            outcome=OUTCOME_LOGIN_CHALLENGE,
            detail="login did not complete and no clear reason was found "
            "(treating as challenge; human review needed — see data/playwright/failures/)",
        )
    except Exception as exc:  # pragma: no cover - network/nav errors
        try:
            await context.close()
        except Exception:
            pass
        return SessionResult(outcome=OUTCOME_ERROR, detail=f"{type(exc).__name__}: {exc}")


async def ensure_session(
    user_id: int,
    username: str,
    *,
    browser: "Browser | None" = None,
    headless: bool = True,
) -> SessionResult:
    """Return a signed-in Playwright ``BrowserContext`` for ``username``.

    Order: rehydrate the stored session blob and verify it; else read the
    Keychain password and log in. Returns a ``SessionResult`` whose
    ``outcome`` is one of the module's ``OUTCOME_*`` constants.

    If ``browser`` is provided the caller owns its lifecycle; otherwise this
    function launches (and, on the OK path, returns) a chromium browser inside
    ``SessionResult.browser`` for the caller to close.
    """
    from playwright.async_api import async_playwright

    settings = get_settings()
    store = get_secret_store(settings)
    password = store.get(LETTERBOXD_SERVICE, username)
    # Note: we read credentials BEFORE launching a browser only to fail fast on
    # no_credentials; the rehydrate path below can still succeed without a
    # password, so only bail if BOTH no blob AND no password.
    have_blob = load_session_blob(user_id) is not None
    if password is None and not have_blob:
        return SessionResult(
            outcome=OUTCOME_NO_CREDENTIALS,
            detail="no Keychain password and no stored session blob",
        )

    owns_browser = browser is None
    playwright_cm = None
    if owns_browser:
        playwright_cm = await async_playwright().start()
        browser = await playwright_cm.chromium.launch(headless=headless)

    async def _finish(result: SessionResult) -> SessionResult:
        # On success we keep the browser alive (returned to caller). On any
        # failure, if we own the browser, tear it down.
        if not result.ok and owns_browser:
            try:
                await browser.close()  # type: ignore[union-attr]
            except Exception:
                pass
            if playwright_cm is not None:
                await playwright_cm.stop()
        elif result.ok and owns_browser:
            result.browser = browser
            # stash the playwright context manager on the result so the caller
            # can fully stop it; expose via attribute for export/scrape close.
            setattr(result, "_playwright", playwright_cm)
        return result

    # 1. Rehydrate.
    context = await _try_rehydrate(browser, user_id, username)  # type: ignore[arg-type]
    if context is not None:
        logger.info("ensure_session: reused stored session for user %s", user_id)
        return await _finish(
            SessionResult(outcome=OUTCOME_OK, context=context, browser=browser)
        )

    # 2. Fresh login (needs a password).
    if password is None:
        return await _finish(
            SessionResult(
                outcome=OUTCOME_NO_CREDENTIALS,
                detail="stored session was stale and no Keychain password is set",
            )
        )

    logger.info("ensure_session: performing fresh login for user %s", user_id)
    result = await _login(browser, user_id, username, password)  # type: ignore[arg-type]
    return await _finish(result)


async def close_session(result: SessionResult) -> None:
    """Fully tear down a successful ``ensure_session`` result."""
    try:
        if result.context is not None:
            await result.context.close()
    except Exception:
        pass
    try:
        if result.browser is not None:
            await result.browser.close()
    except Exception:
        pass
    pw = getattr(result, "_playwright", None)
    if pw is not None:
        try:
            await pw.stop()
        except Exception:
            pass
