"""Model artefact persistence — docs/phases/PHASE-3-recommender.md §6 (SCOPED).

SCOPE-DOWN (documented, not silent): the doc's §6 describes versioned artefact
DIRECTORIES (features.npz + film_ids.json + vocab.json + per-user joblib +
metrics.json), keep-last-5 pruning, and a nightly retrain scheduler that flips
`is_active` atomically. This pass implements the *storage contract* only:

  - one real joblib file per retrain under data/models/<version>/taste.joblib
    holding both users' fitted UserTasteModel objects + corpus film_ids + the
    global mean,
  - one `model_artifacts` row (kind='taste_model', user_id NULL for the shared
    bundle) marked is_active=1, with any previously-active taste_model row
    flipped to is_active=0 in the same transaction,

and deliberately SKIPS: separate feature_matrix/vocab artefact rows,
keep-last-5 pruning, and the nightly scheduler. Recompute-per-request is fine
at this corpus size (~1k films, 2 users) — v0 already recomputes features per
call. The full versioned-dir + pruning + scheduler machinery is a real
follow-up.

The joblib file is loaded at scoring time (or the model is recomputed in
memory) — either path yields identical results since fitting is deterministic
and fast.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import joblib
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..config import DATA_DIR
from ..models import ModelArtifact
from .taste import UserTasteModel

logger = logging.getLogger(__name__)

MODELS_DIR = DATA_DIR / "models"


def _version_stamp() -> str:
    # e.g. 2026-07-04T1530Z — matches the API.md model_version example shape.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")


def save_taste_artifact(
    session: Session,
    *,
    models: dict[int, UserTasteModel],
    film_ids: list[int],
    global_mean: float,
    metrics: dict,
) -> tuple[str, str]:
    """Persist the fitted taste models as ONE joblib bundle + flip the active
    model_artifacts row. Returns (version, relative_path).

    The caller owns the outer transaction boundary but this function commits
    itself (single self-contained unit: write file, then flip is_active).
    """
    version = _version_stamp()
    version_dir = MODELS_DIR / version
    version_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = version_dir / "taste.joblib"

    bundle = {
        "version": version,
        "film_ids": film_ids,
        "global_mean": global_mean,
        "models": models,  # dict[user_id -> UserTasteModel] (picklable dataclasses)
        "metrics": metrics,
    }
    joblib.dump(bundle, artifact_path)

    rel_path = str(artifact_path.relative_to(DATA_DIR / "models"))

    # Flip any previously-active taste_model rows off, then insert the new
    # active row — one transaction, mirrors §6's atomic is_active flip.
    session.execute(
        update(ModelArtifact)
        .where(ModelArtifact.kind == "taste_model", ModelArtifact.is_active == 1)
        .values(is_active=0)
    )
    import json as _json

    row = ModelArtifact(
        kind="taste_model",
        user_id=None,  # shared bundle (both users in one file)
        version=version,
        path=rel_path,
        metrics_json=_json.dumps(metrics),
        is_active=1,
    )
    session.add(row)
    session.commit()
    logger.info("saved taste artifact version=%s path=%s", version, artifact_path)
    return version, rel_path


def active_taste_artifact(session: Session) -> ModelArtifact | None:
    from sqlalchemy import select

    return session.scalars(
        select(ModelArtifact).where(
            ModelArtifact.kind == "taste_model", ModelArtifact.is_active == 1
        )
    ).first()
