"""Import & sync endpoints — docs/API.md §Phase 2 "Import & sync".

POST /api/import/letterboxd/run          trigger an import run (auto/export/scrape/rss cascade)
POST /api/import/letterboxd              multipart ZIP upload (export variant)
GET  /api/import/jobs/{job_id}           progress/result of an import run
GET  /api/import/runs                    run history
GET  /api/import/unmatched               manual-resolution queue
POST /api/import/unmatched/{id}/resolve  resolve one unmatched row
POST /api/sync/rss/run                   trigger RSS poll now (both users)
GET  /api/sync/state                     cursors + last-run status of background jobs

Automation note: `POST /api/import/letterboxd/run` creates a real `ImportRun`
row, returns 202 IMMEDIATELY, and runs the requested export/scrape/rss
cascade in a background `asyncio` task (`app/importers/cascade.py`) so a full
100-page scrape never blocks the HTTP response — the frontend polls
`GET /api/import/jobs/{id}` for progress/result. `app/letterboxd_auto/` has
real, working `run_export`/`run_scrape` entrypoints; cascade.py runs their
output through the same TMDB-match + merge.py-upsert pipeline the manual
ZIP-upload endpoint uses (`process_zip_bytes`/`process_rows`), so all sources
persist real films/watches/ratings/likes rows, not just placeholders.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import DATA_DIR
from ..db import get_session
from ..errors import MishkaHTTPException
from ..importers.cascade import process_zip_bytes, run_cascade_background
from ..importers.merge import upsert_film, upsert_like, upsert_rating, upsert_watch
from ..importers.rss import poll_user
from ..models import ImportRun, SyncState, UnmatchedImport, User

router = APIRouter(tags=["import"])

EXPORT_DIR = DATA_DIR / "letterboxd_exports"

# Background asyncio tasks for in-flight cascades, keyed by ImportRun.id, so
# they aren't garbage-collected mid-flight (asyncio only holds a weak
# reference via the event loop otherwise) — see PHASE-2 §11 "background
# work" convention referenced in main.py's lifespan.
_background_tasks: dict[int, asyncio.Task] = {}


def _spawn_cascade(run_id: int, user_id: int, source_requested: str) -> None:
    task = asyncio.create_task(run_cascade_background(run_id, user_id, source_requested))
    _background_tasks[run_id] = task

    def _cleanup(_: asyncio.Task, rid: int = run_id) -> None:
        _background_tasks.pop(rid, None)

    task.add_done_callback(_cleanup)


def _job_id(run_id: int) -> str:
    return f"imp_{run_id}"


def _run_id_from_job_id(job_id: str) -> int | None:
    if not job_id.startswith("imp_"):
        return None
    try:
        return int(job_id.removeprefix("imp_"))
    except ValueError:
        return None


def _serialize_run(run: ImportRun) -> dict:
    return {
        "job_id": _job_id(run.id),
        "user_id": run.user_id,
        "source_requested": run.source_requested,
        "source_used": run.source_used,
        "status": run.status,
        "stage": run.stage,
        "cascade": json.loads(run.cascade_json) if run.cascade_json else [],
        "counts": json.loads(run.counts_json) if run.counts_json else None,
        "error": run.error,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


class ImportRunBody(BaseModel):
    user: int
    source: str = "auto"


@router.post("/import/letterboxd/run", status_code=202)
async def trigger_import_run(
    body: ImportRunBody,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Trigger an import run and return 202 IMMEDIATELY (job id + status
    'running'); the actual export/scrape/rss cascade runs in a background
    asyncio task (see importers/cascade.py) so a full 100-page scrape (which
    can take minutes) never blocks this HTTP response. The frontend already
    polls GET /api/import/jobs/{id} for progress/completion.
    """
    if body.source not in ("auto", "export", "scrape", "rss"):
        raise MishkaHTTPException(
            status_code=422,
            detail=f"Unknown source {body.source!r}",
            code="invalid_source",
        )

    user = session.get(User, body.user)
    if user is None:
        raise MishkaHTTPException(
            status_code=404, detail=f"User {body.user} not found", code="not_found"
        )

    existing_running = session.scalars(
        select(ImportRun).where(
            ImportRun.user_id == body.user, ImportRun.status == "running"
        )
    ).first()
    if existing_running is not None:
        raise MishkaHTTPException(
            status_code=409,
            detail="An import for this user is already running",
            code="duplicate_job",
        )

    run = ImportRun(
        user_id=body.user,
        source_requested=body.source,
        trigger="api",
        status="running",
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    _spawn_cascade(run.id, body.user, body.source)

    return _serialize_run(run)


@router.post("/import/letterboxd", status_code=202)
async def upload_letterboxd_export(
    request: Request,
    user: int = Query(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> dict:
    db_user = session.get(User, user)
    if db_user is None:
        raise MishkaHTTPException(status_code=404, detail=f"User {user} not found", code="not_found")

    existing_running = session.scalars(
        select(ImportRun).where(ImportRun.user_id == user, ImportRun.status == "running")
    ).first()
    if existing_running is not None:
        raise MishkaHTTPException(
            status_code=409,
            detail="An import for this user is already running",
            code="duplicate_job",
        )

    raw_bytes = await file.read()
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    run = ImportRun(
        user_id=user,
        source_requested="export-upload",
        source_used="export-upload",
        trigger="upload",
        status="running",
        export_sha256=sha256,
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    # Duplicate-export short-circuit: same SHA-256 as the last successful run.
    prior = session.scalars(
        select(ImportRun)
        .where(
            ImportRun.user_id == user,
            ImportRun.export_sha256 == sha256,
            ImportRun.id != run.id,
            ImportRun.status.in_(("done", "done_unchanged")),
        )
        .order_by(ImportRun.started_at.desc())
    ).first()
    if prior is not None:
        run.status = "done_unchanged"
        run.counts_json = json.dumps(
            {
                "watched": 0, "diary": 0, "ratings": 0, "likes": 0, "reviews": 0,
                "matched": 0, "unmatched": 0, "skipped_duplicates": 0,
            }
        )
        run.finished_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return _serialize_run(run)

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = EXPORT_DIR / f"user{user}_{run.id}.zip"
    zip_path.write_bytes(raw_bytes)
    run.export_zip_path = str(zip_path)
    session.commit()

    tmdb = request.app.state.tmdb

    try:
        # Shared with the automated export cascade (importers/cascade.py) so
        # manual ZIP upload and the "export" automation source persist rows
        # through exactly the same match/merge pipeline.
        counts = await process_zip_bytes(session, tmdb, user, raw_bytes, run.id)

        run.status = "done"
        run.counts_json = json.dumps(counts)
        run.finished_at = datetime.now(timezone.utc).isoformat()
        session.commit()
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        session.refresh(run)
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = datetime.now(timezone.utc).isoformat()
        session.commit()

    return _serialize_run(run)


@router.get("/import/jobs/{job_id}")
async def get_import_job(job_id: str, session: Session = Depends(get_session)) -> dict:
    run_id = _run_id_from_job_id(job_id)
    if run_id is None:
        raise MishkaHTTPException(status_code=404, detail=f"Unknown job id {job_id}", code="not_found")

    run = session.get(ImportRun, run_id)
    if run is None:
        raise MishkaHTTPException(status_code=404, detail=f"Unknown job id {job_id}", code="not_found")

    return _serialize_run(run)


@router.get("/import/runs")
async def list_import_runs(
    user: int | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    stmt = select(ImportRun)
    if user is not None:
        stmt = stmt.where(ImportRun.user_id == user)
    stmt = stmt.order_by(ImportRun.started_at.desc()).limit(limit)
    runs = session.scalars(stmt).all()
    return {"total": len(runs), "items": [_serialize_run(r) for r in runs]}


@router.get("/import/unmatched")
async def list_unmatched(
    status: str = Query(default="pending"),
    session: Session = Depends(get_session),
) -> dict:
    stmt = select(UnmatchedImport).where(UnmatchedImport.status == status)
    rows = session.scalars(stmt.order_by(UnmatchedImport.created_at.desc())).all()
    items = [
        {
            "id": r.id,
            "user_id": r.user_id,
            "source_file": r.source_file,
            "name": r.name,
            "year": r.year,
            "letterboxd_uri": r.letterboxd_uri,
            "status": r.status,
            "matched_film_id": r.matched_film_id,
            "created_at": r.created_at,
        }
        for r in rows
    ]
    return {"total": len(items), "items": items}


class ResolveUnmatchedBody(BaseModel):
    tmdb_id: int | None = None
    action: str | None = None


async def resolve_unmatched_row(session: Session, tmdb, row: UnmatchedImport, tmdb_id: int) -> dict:
    """Core "resolve one unmatched row to a given TMDB id" logic — the exact
    path `POST /import/unmatched/{id}/resolve` uses for a manual `tmdb_id`
    resolution, factored out so any other caller (e.g. a batch rematch pass
    reusing the tmdb_match.py scorer) resolves rows identically: same
    upsert_film/upsert_watch/upsert_rating/upsert_like calls, same
    status/matched_film_id bookkeeping. Caller is responsible for checking
    row.status == "pending" beforehand (this function doesn't re-check so it
    can be reused mid-batch without re-fetching).
    """
    payload = await tmdb.movie(tmdb_id, append="credits,keywords,release_dates")
    film = upsert_film(session, payload)

    raw_row = json.loads(row.raw_row_json)
    if raw_row.get("watched_date") or row.source_file in ("diary", "watched", "reviews"):
        upsert_watch(
            session,
            user_id=row.user_id,
            film_id=film.id,
            watched_date=raw_row.get("watched_date"),
            rewatch=raw_row.get("rewatch", False),
            tags=raw_row.get("tags"),
            source="letterboxd-import",
            letterboxd_uri=raw_row.get("uri"),
        )
    if raw_row.get("rating") is not None:
        upsert_rating(
            session,
            user_id=row.user_id,
            film_id=film.id,
            rating=raw_row["rating"],
            source="letterboxd-import",
        )
    if row.source_file == "likes":
        upsert_like(session, user_id=row.user_id, film_id=film.id, source="letterboxd-import")

    row.status = "matched"
    row.matched_film_id = film.id
    session.commit()

    return {"id": row.id, "status": "matched", "film_id": film.id}


@router.post("/import/unmatched/{unmatched_id}/resolve")
async def resolve_unmatched(
    unmatched_id: int,
    body: ResolveUnmatchedBody,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    row = session.get(UnmatchedImport, unmatched_id)
    if row is None:
        raise MishkaHTTPException(status_code=404, detail=f"Unmatched row {unmatched_id} not found", code="not_found")

    if row.status != "pending":
        raise MishkaHTTPException(status_code=409, detail="Row already resolved", code="already_resolved")

    if body.action == "ignore":
        row.status = "ignored"
        session.commit()
        return {"id": row.id, "status": "ignored", "film_id": None}

    if body.tmdb_id is None:
        raise MishkaHTTPException(
            status_code=422, detail="Provide tmdb_id or action=ignore", code="invalid_body"
        )

    tmdb = request.app.state.tmdb
    return await resolve_unmatched_row(session, tmdb, row, body.tmdb_id)


@router.post("/sync/rss/run")
async def trigger_rss_sync(
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    tmdb = request.app.state.tmdb
    users = session.scalars(select(User).where(User.letterboxd_username.is_not(None))).all()

    results = []
    for user in users:
        result = await poll_user(session, tmdb, user.id, user.letterboxd_username)
        results.append(
            {
                "user_id": user.id,
                "letterboxd_username": user.letterboxd_username,
                "ok": result["ok"],
                "new_items": result.get("new_items", 0),
                "new_watches": result.get("new_watches", 0),
                "errors": result.get("errors", []),
            }
        )

    return {"users": results}


@router.get("/sync/state")
async def get_sync_state(session: Session = Depends(get_session)) -> dict:
    rows = session.scalars(select(SyncState)).all()
    items = [
        {
            "kind": r.kind,
            "user_id": r.user_id,
            "last_run_at": r.last_run_at,
            "last_ok_at": r.last_ok_at,
            "status": r.status,
            "detail": json.loads(r.detail_json) if r.detail_json else None,
        }
        for r in rows
    ]
    return {"items": items}
