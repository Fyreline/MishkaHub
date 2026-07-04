"""Source 1 — automated data export (docs/phases/PHASE-2-letterboxd-import.md §2b).

``run_export`` checks the ToS-acknowledgement gate, ensures a signed-in
session, then fetches the authenticated ``/data/export/`` URL (whose response
body is the ZIP directly — no DOM needed for this step), verifies the ZIP
magic bytes, and saves it under ``data/letterboxd/exports/<username>/``.

Returns a result dict with ``outcome`` (one of the PHASE-2 §2c classes plus
``ok``), and — on success — ``path`` and ``sha256`` of the saved ZIP.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from ..config import DATA_DIR
from ..db import SessionLocal
from ..models import AppSetting
from . import selectors, session as session_mod

logger = logging.getLogger(__name__)

ZIP_MAGIC = b"PK\x03\x04"
EXPORTS_DIR = DATA_DIR / "letterboxd" / "exports"


def _ack_key(user_id: int) -> str:
    return f"letterboxd_automation_ack_user_{user_id}"


def tos_acknowledged(user_id: int) -> bool:
    """True if the settings row ``letterboxd_automation_ack_user_<id>`` exists.

    Per PHASE-2-credentials.md §6 the value is an ISO timestamp; its mere
    presence is the gate.
    """
    with SessionLocal() as db:
        row = db.execute(
            select(AppSetting).where(AppSetting.key == _ack_key(user_id))
        ).scalar_one_or_none()
        return row is not None


def _sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


async def run_export(user_id: int, username: str) -> dict:
    """Download the authenticated Letterboxd data export ZIP for ``username``.

    Steps (PHASE-2 §2b): ToS ack -> ensure_session -> GET /data/export/ ->
    verify ZIP magic -> save to data/letterboxd/exports/<username>/<UTC>.zip.
    """
    # 1. ToS ack gate (PHASE-2-credentials.md §6).
    if not tos_acknowledged(user_id):
        return {
            "outcome": "tos_not_acknowledged",
            "detail": f"settings key {_ack_key(user_id)} is absent",
        }

    # 2. Ensure a signed-in session.
    sess = await session_mod.ensure_session(user_id, username)
    if not sess.ok:
        return {"outcome": sess.outcome, "detail": sess.detail}

    try:
        context = sess.context
        assert context is not None

        # 3. GET /data/export/ — APIRequestContext reuses the browser cookies,
        # so no DOM interaction is needed for the download itself.
        resp = await context.request.get(selectors.EXPORT_URL, timeout=60000)
        body = await resp.body()

        if not body.startswith(ZIP_MAGIC):
            # Not a ZIP — record what we got for the export_unavailable class.
            content_type = resp.headers.get("content-type", "")
            snippet = body[:200].decode("utf-8", "replace")
            return {
                "outcome": "export_unavailable",
                "detail": (
                    f"/data/export/ returned status={resp.status} "
                    f"content-type={content_type!r}; body did not start with the "
                    f"ZIP magic bytes. First 200 bytes: {snippet!r}"
                ),
            }

        # 4. Save to data/letterboxd/exports/<username>/<UTC-timestamp>.zip.
        out_dir = EXPORTS_DIR / username
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        out_path = out_dir / f"{ts}.zip"
        out_path.write_bytes(body)

        sha = _sha256_file(out_path)
        logger.info(
            "run_export: saved %d-byte export for %s to %s (sha256=%s)",
            len(body), username, out_path, sha,
        )
        return {
            "outcome": "ok",
            "path": str(out_path),
            "sha256": sha,
            "bytes": len(body),
        }
    finally:
        await session_mod.close_session(sess)
