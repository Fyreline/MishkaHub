"""Mishka Hub FastAPI application entrypoint.

Run locally with:
    uvicorn app.main:app --reload
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from .auth import current_user
from .clients.jellyfin import JellyfinClient
from .clients.tmdb import TMDBClient
from .config import get_settings
from .db import SessionLocal
from .errors import register_error_handlers
from .importers.cascade import run_cascade_background
from .models import ImportRun, User
from .routers import (
    auth,
    credentials,
    feedback,
    films,
    health,
    import_,
    media,
    recommendations,
    settings,
    tmdb,
    upcoming,
)

# Ensure INFO-level logs (scheduler start/finish, per-cycle row deltas, etc.)
# actually reach stdout/uvicorn's log capture — uvicorn only configures its
# OWN "uvicorn"/"uvicorn.access" loggers, not the root logger, so without
# this our app.* loggers stay at the default WARNING level and every
# logger.info(...) call below is silently dropped.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)


async def _sync_scheduler_loop(app: FastAPI) -> None:
    """Background loop (PHASE-2 §11 "background work" convention): every
    ``MISHKA_SYNC_INTERVAL_HOURS`` hours, run the full "auto" cascade
    (export -> scrape -> rss, whichever succeeds first — see
    importers/cascade.py) for every user that has a letterboxd_username.

    Per-iteration exceptions are caught and logged so a crash in one cycle
    (or for one user) never kills the loop or the server; the loop always
    sleeps and retries next cycle.
    """
    settings = app.state.settings
    interval_seconds = max(60.0, settings.sync_interval_hours * 3600)
    logger.info(
        "sync_scheduler: started (interval=%.2fh / %.0fs)",
        settings.sync_interval_hours, interval_seconds,
    )

    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await _run_scheduled_sync_once()
        except asyncio.CancelledError:
            logger.info("sync_scheduler: cancelled, exiting loop")
            raise
        except Exception:  # noqa: BLE001 — a crashed cycle must not kill the loop
            logger.exception("sync_scheduler: uncaught error in scheduler loop iteration")


async def _run_scheduled_sync_once() -> None:
    session = SessionLocal()
    try:
        users = session.scalars(
            select(User).where(User.letterboxd_username.is_not(None))
        ).all()
        logger.info("sync_scheduler: cycle starting for %d user(s)", len(users))

        for user in users:
            try:
                before = _row_counts(session)
                run = ImportRun(
                    user_id=user.id,
                    source_requested="auto",
                    trigger="schedule",
                    status="running",
                )
                session.add(run)
                session.commit()
                session.refresh(run)

                await run_cascade_background(run.id, user.id, "auto")

                after = _row_counts(session)
                delta = {k: after[k] - before[k] for k in before}
                logger.info(
                    "sync_scheduler: user %s (%s) run %s finished — row deltas: %s",
                    user.id, user.letterboxd_username, run.id, delta,
                )
            except Exception:  # noqa: BLE001 — one user's failure must not skip the rest
                logger.exception(
                    "sync_scheduler: sync failed for user %s (%s)",
                    user.id, user.letterboxd_username,
                )
        logger.info("sync_scheduler: cycle finished")
    finally:
        session.close()


def _row_counts(session) -> dict[str, int]:
    from .models import Film, Like, Rating, Watch

    return {
        "films": session.query(Film).count(),
        "watches": session.query(Watch).count(),
        "ratings": session.query(Rating).count(),
        "likes": session.query(Like).count(),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.tmdb = TMDBClient(settings)
    app.state.jellyfin = JellyfinClient(settings)
    app.state.sync_scheduler_task = asyncio.create_task(_sync_scheduler_loop(app))
    logger.info("lifespan: sync scheduler task started")
    try:
        yield
    finally:
        task = app.state.sync_scheduler_task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await app.state.tmdb.aclose()
        await app.state.jellyfin.aclose()


def create_app() -> FastAPI:
    app_settings = get_settings()
    app = FastAPI(title="Mishka Hub", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_error_handlers(app)

    # /api/health and /api/auth/(login|refresh|logout) stay public — everything
    # else requires a valid per-user JWT (docs/phases/PHASE-4-accounts-feedback.md).
    # /api/auth/me enforces auth itself via its own Depends(current_user).
    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(tmdb.router, prefix="/api", dependencies=[Depends(current_user)])
    app.include_router(films.router, prefix="/api", dependencies=[Depends(current_user)])
    app.include_router(
        recommendations.router, prefix="/api", dependencies=[Depends(current_user)]
    )
    app.include_router(feedback.router, prefix="/api", dependencies=[Depends(current_user)])
    app.include_router(feedback.generic_router, prefix="/api", dependencies=[Depends(current_user)])
    app.include_router(import_.router, prefix="/api", dependencies=[Depends(current_user)])
    app.include_router(credentials.router, prefix="/api", dependencies=[Depends(current_user)])
    app.include_router(settings.router, prefix="/api", dependencies=[Depends(current_user)])
    app.include_router(media.router, prefix="/api", dependencies=[Depends(current_user)])
    app.include_router(upcoming.router, prefix="/api", dependencies=[Depends(current_user)])
    return app


app = create_app()
