"""Owned local media — docs/API.md §Phase 7, docs/phases/PHASE-7-local-media-tv.md.

GET     /api/media                 owned films (media_files joined to films)
GET/PUT /api/settings/media         configured media root folders
POST    /api/media/scan             walk the configured roots, upsert/match files
POST    /api/media/{file_id}/match  manually point an unmatched file at a TMDB id
DELETE  /api/media/{file_id}        drop a media_files row (e.g. file removed on disk)
POST    /api/media/play             "Play on TV" — command the Jellyfin webOS session
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.jellyfin import JellyfinClient, JellyfinError
from ..clients.tmdb import TMDBClient
from ..db import get_session
from ..errors import MishkaHTTPException
from ..importers.media_scan import scan_media_roots
from ..models import AppSetting, Film, MediaFile
from .films import _get_or_hydrate_film_by_id

router = APIRouter(tags=["media"])

_MEDIA_ROOTS_KEY = "media_roots"


def _get_media_roots(session: Session) -> list[str]:
    row = session.get(AppSetting, _MEDIA_ROOTS_KEY)
    if row is None:
        return []
    try:
        roots = json.loads(row.value_json)
    except json.JSONDecodeError:
        return []
    return roots if isinstance(roots, list) else []


def _serialize_media_file(m: MediaFile, film: Film | None) -> dict:
    return {
        "id": m.id,
        "path": m.path,
        "size_bytes": m.size_bytes,
        "jellyfin_item_id": m.jellyfin_item_id,
        "scanned_at": m.scanned_at,
        "film": {
            "id": film.id,
            "title": film.title,
            "year": film.release_year,
            "poster": TMDBClient.poster_url(film.poster_path),
        }
        if film is not None
        else None,
    }


@router.get("/media")
async def list_media(session: Session = Depends(get_session)) -> dict:
    rows = session.scalars(select(MediaFile)).all()
    film_ids = {m.film_id for m in rows if m.film_id is not None}
    films_by_id = {
        f.id: f for f in session.scalars(select(Film).where(Film.id.in_(film_ids))).all()
    } if film_ids else {}
    items = [_serialize_media_file(m, films_by_id.get(m.film_id)) for m in rows]
    return {
        "total": len(items),
        "matched": sum(1 for i in items if i["film"] is not None),
        "unmatched": sum(1 for i in items if i["film"] is None),
        "items": items,
    }


class MediaRootsBody(BaseModel):
    roots: list[str]


@router.get("/settings/media")
async def get_media_settings(session: Session = Depends(get_session)) -> dict:
    return {"roots": _get_media_roots(session)}


@router.put("/settings/media")
async def put_media_settings(
    body: MediaRootsBody, session: Session = Depends(get_session)
) -> dict:
    row = session.get(AppSetting, _MEDIA_ROOTS_KEY)
    value = json.dumps(body.roots)
    if row is None:
        session.add(AppSetting(key=_MEDIA_ROOTS_KEY, value_json=value))
    else:
        row.value_json = value
    session.commit()
    return {"roots": body.roots}


@router.post("/media/scan")
async def post_media_scan(request: Request, session: Session = Depends(get_session)) -> dict:
    roots = _get_media_roots(session)
    if not roots:
        raise MishkaHTTPException(
            status_code=400,
            detail="No media roots configured — set them via PUT /api/settings/media first.",
            code="no_media_roots",
        )
    tmdb: TMDBClient = request.app.state.tmdb
    jellyfin: JellyfinClient = request.app.state.jellyfin
    report = await scan_media_roots(session, tmdb, jellyfin, roots)
    return {
        "roots_scanned": report.roots_scanned,
        "files_found": report.files_found,
        "files_new": report.files_new,
        "files_removed": report.files_removed,
        "auto_matched": report.auto_matched,
        "unmatched": report.unmatched,
        "errors": report.errors,
    }


class MatchBody(BaseModel):
    tmdb_id: int


@router.post("/media/{file_id}/match")
async def post_media_match(
    file_id: int, body: MatchBody, request: Request, session: Session = Depends(get_session)
) -> dict:
    row = session.get(MediaFile, file_id)
    if row is None:
        raise MishkaHTTPException(
            status_code=404, detail=f"No media file with id {file_id}", code="not_found"
        )
    tmdb: TMDBClient = request.app.state.tmdb
    film = await _get_or_hydrate_film_by_id(body.tmdb_id, tmdb, session)
    row.film_id = film.id
    session.commit()
    return _serialize_media_file(row, film)


@router.delete("/media/{file_id}")
async def delete_media_file(file_id: int, session: Session = Depends(get_session)) -> dict:
    row = session.get(MediaFile, file_id)
    if row is None:
        raise MishkaHTTPException(
            status_code=404, detail=f"No media file with id {file_id}", code="not_found"
        )
    session.delete(row)
    session.commit()
    return {"deleted": file_id}


class PlayBody(BaseModel):
    film_id: int


@router.post("/media/play")
async def post_media_play(
    body: PlayBody, request: Request, session: Session = Depends(get_session)
) -> dict:
    jellyfin: JellyfinClient = request.app.state.jellyfin
    if not jellyfin.configured:
        raise MishkaHTTPException(
            status_code=400,
            detail="Jellyfin is not configured on this server.",
            code="jellyfin_not_configured",
        )
    row = session.scalar(
        select(MediaFile).where(
            MediaFile.film_id == body.film_id, MediaFile.jellyfin_item_id.is_not(None)
        )
    )
    if row is None:
        raise MishkaHTTPException(
            status_code=404,
            detail=f"No Jellyfin-linked media file for film {body.film_id}",
            code="not_found",
        )
    try:
        session_info = await jellyfin.find_tv_session()
        if session_info is None:
            raise MishkaHTTPException(
                status_code=503,
                detail="The living-room TV's Jellyfin app isn't reachable right now — "
                "make sure it's on and the app is open.",
                code="tv_not_available",
            )
        await jellyfin.play(session_info["Id"], row.jellyfin_item_id)
    except JellyfinError as exc:
        raise MishkaHTTPException(status_code=502, detail=str(exc), code="jellyfin_upstream_error") from exc
    return {"playing": True}
