"""Letterboxd browser automation (Phase 2 source 1 export + source 2 scrape).

This package holds the Playwright-driven pieces of the import cascade
(docs/phases/PHASE-2-letterboxd-import.md §2 export, §3 scrape) plus the
shared ``ensure_session`` login/session-persistence helper reused verbatim by
the Phase 5 write-back (docs/phases/PHASE-2-credentials.md §4).

All DOM selectors live in one place — ``selectors.py`` — following the docs'
"only file to touch when Letterboxd's markup changes" convention.
"""
