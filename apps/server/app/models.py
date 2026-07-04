"""SQLAlchemy 2.x ORM models — mirrors docs/DATA_MODEL.md §2 exactly.

This module is the single source of truth for table structure in code. When it
diverges from docs/DATA_MODEL.md, the doc wins and a migration must be written
to reconcile (see docs/DATA_MODEL.md §5). Column names, nullability, defaults,
CHECK constraints, and indexes are all intended to match the DDL there 1:1.

Note: this module is named ``models.py`` (Python/ORM convention) and is
unrelated to the ``Settings`` class in ``app/config.py``. The ORM class for
the ``settings`` table is named ``AppSetting`` to avoid the name collision.
"""
from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# datetime('now') default, shared by every *_at/created_at/updated_at column
# that uses it in docs/DATA_MODEL.md §2.
NOW = text("datetime('now')")


# ============ users (Phase 4; created in Phase 2 with placeholder rows) ============
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    email: Mapped[str] = mapped_column(nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(nullable=False)
    password_hash: Mapped[str | None] = mapped_column(nullable=True)
    letterboxd_username: Mapped[str | None] = mapped_column(nullable=True, unique=True)
    created_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)


# ============ films: one row per TMDB movie ============
class Film(Base):
    __tablename__ = "films"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)  # TMDB movie id
    imdb_id: Mapped[str | None] = mapped_column(nullable=True)
    letterboxd_slug: Mapped[str | None] = mapped_column(nullable=True)
    letterboxd_uri: Mapped[str | None] = mapped_column(nullable=True)
    title: Mapped[str] = mapped_column(nullable=False)
    original_title: Mapped[str | None] = mapped_column(nullable=True)
    release_year: Mapped[int | None] = mapped_column(nullable=True)
    release_date: Mapped[str | None] = mapped_column(nullable=True)
    runtime_min: Mapped[int | None] = mapped_column(nullable=True)
    original_language: Mapped[str | None] = mapped_column(nullable=True)
    overview: Mapped[str | None] = mapped_column(nullable=True)
    poster_path: Mapped[str | None] = mapped_column(nullable=True)
    backdrop_path: Mapped[str | None] = mapped_column(nullable=True)
    popularity: Mapped[float | None] = mapped_column(nullable=True)
    vote_average: Mapped[float | None] = mapped_column(nullable=True)
    vote_count: Mapped[int | None] = mapped_column(nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(nullable=True)
    tmdb_fetched_at: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)

    __table_args__ = (
        Index("idx_films_slug", "letterboxd_slug"),
        Index("idx_films_year", "release_year"),
        Index("idx_films_title", "title"),
    )


# ============ watches: diary entries (one row per viewing) ============
class Watch(Base):
    __tablename__ = "watches"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    film_id: Mapped[int] = mapped_column(ForeignKey("films.id"), nullable=False)
    watched_date: Mapped[str | None] = mapped_column(nullable=True)
    rewatch: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    tags_json: Mapped[str | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(
        nullable=False, server_default=text("'letterboxd-import'")
    )
    letterboxd_guid: Mapped[str | None] = mapped_column(nullable=True, unique=True)
    letterboxd_uri: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)

    __table_args__ = (
        CheckConstraint(
            "source IN ('letterboxd-import','letterboxd-scrape','letterboxd-rss','in-app')",
            name="ck_watches_source",
        ),
        Index("idx_watches_user_film", "user_id", "film_id"),
        Index("idx_watches_date", "user_id", "watched_date"),
        Index(
            "idx_watches_dedup",
            "user_id",
            "film_id",
            text("ifnull(watched_date,'')"),
            unique=True,
        ),
    )


# ============ ratings: current rating per (user, film) ============
class Rating(Base):
    __tablename__ = "ratings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    film_id: Mapped[int] = mapped_column(ForeignKey("films.id"), primary_key=True)
    rating: Mapped[float] = mapped_column(nullable=False)
    source: Mapped[str] = mapped_column(nullable=False)
    rated_at: Mapped[str | None] = mapped_column(nullable=True)
    # Shadow copy of the most recent Letterboxd-sourced rating value, kept even
    # after an in-app edit overrides `rating`/`source` — lets the UI show
    # "you said X, Letterboxd says Y" instead of losing Letterboxd's value.
    # Only letterboxd-* upserts write this; in-app upserts never touch it.
    letterboxd_rating: Mapped[float | None] = mapped_column(nullable=True)
    updated_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)

    __table_args__ = (
        CheckConstraint("rating >= 0.5 AND rating <= 5.0", name="ck_ratings_rating_range"),
        CheckConstraint(
            "letterboxd_rating IS NULL OR (letterboxd_rating >= 0.5 AND letterboxd_rating <= 5.0)",
            name="ck_ratings_letterboxd_rating_range",
        ),
        CheckConstraint(
            "source IN ('letterboxd-import','letterboxd-scrape','letterboxd-rss','in-app')",
            name="ck_ratings_source",
        ),
        Index("idx_ratings_film", "film_id"),
    )


# ============ likes ============
class Like(Base):
    __tablename__ = "likes"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    film_id: Mapped[int] = mapped_column(ForeignKey("films.id"), primary_key=True)
    source: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)

    __table_args__ = (
        CheckConstraint(
            "source IN ('letterboxd-import','letterboxd-scrape','letterboxd-rss','in-app')",
            name="ck_likes_source",
        ),
    )


# ============ reviews ============
class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    film_id: Mapped[int] = mapped_column(ForeignKey("films.id"), nullable=False)
    review_text: Mapped[str] = mapped_column(nullable=False)
    contains_spoilers: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    watched_date: Mapped[str | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)

    __table_args__ = (
        CheckConstraint(
            "source IN ('letterboxd-import','letterboxd-scrape','letterboxd-rss','in-app')",
            name="ck_reviews_source",
        ),
        Index("idx_reviews_user_film", "user_id", "film_id"),
    )


# ============ subscriptions: household streaming services ============
class Subscription(Base):
    __tablename__ = "subscriptions"

    provider_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    provider_name: Mapped[str] = mapped_column(nullable=False)
    logo_path: Mapped[str | None] = mapped_column(nullable=True)
    monthly_cost_pence: Mapped[int | None] = mapped_column(nullable=True)
    active: Mapped[int] = mapped_column(nullable=False, server_default=text("1"))
    added_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)


# ============ availability: film x provider cache (TTL) ============
class Availability(Base):
    __tablename__ = "availability"

    film_id: Mapped[int] = mapped_column(ForeignKey("films.id"), primary_key=True)
    provider_id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(primary_key=True)
    region: Mapped[str] = mapped_column(
        primary_key=True, nullable=False, server_default=text("'GB'")
    )
    fetched_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('flatrate','free','ads','rent','buy')", name="ck_availability_kind"
        ),
        Index("idx_availability_fetched", "fetched_at"),
    )


# ============ unmatched_imports: manual-resolution queue (Phase 2) ============
class UnmatchedImport(Base):
    __tablename__ = "unmatched_imports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_file: Mapped[str] = mapped_column(nullable=False)
    raw_row_json: Mapped[str] = mapped_column(nullable=False)
    name: Mapped[str | None] = mapped_column(nullable=True)
    year: Mapped[int | None] = mapped_column(nullable=True)
    letterboxd_uri: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(nullable=False, server_default=text("'pending'"))
    matched_film_id: Mapped[int | None] = mapped_column(
        ForeignKey("films.id"), nullable=True
    )
    created_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','matched','ignored')", name="ck_unmatched_imports_status"
        ),
        Index("idx_unmatched_status", "status"),
    )


# ============ import_runs: one row per Letterboxd import run (Phase 2 cascade) ============
class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    source_requested: Mapped[str] = mapped_column(nullable=False)
    source_used: Mapped[str | None] = mapped_column(nullable=True)
    trigger: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, server_default=text("'running'"))
    stage: Mapped[str | None] = mapped_column(nullable=True)
    started_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)
    finished_at: Mapped[str | None] = mapped_column(nullable=True)
    counts_json: Mapped[str | None] = mapped_column(nullable=True)
    cascade_json: Mapped[str | None] = mapped_column(nullable=True)
    export_zip_path: Mapped[str | None] = mapped_column(nullable=True)
    export_sha256: Mapped[str | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "source_requested IN ('auto','export','export-upload','scrape','rss')",
            name="ck_import_runs_source_requested",
        ),
        CheckConstraint(
            "source_used IN ('export','export-upload','scrape','rss')",
            name="ck_import_runs_source_used",
        ),
        CheckConstraint(
            "trigger IN ('schedule','api','upload','drop_folder')",
            name="ck_import_runs_trigger",
        ),
        CheckConstraint(
            "status IN ('running','done','done_unchanged','failed')",
            name="ck_import_runs_status",
        ),
        Index("idx_import_runs_user", "user_id", "started_at"),
    )


# ============ feedback_events: append-only log driving the model (Phase 4) ============
class FeedbackEvent(Base):
    __tablename__ = "feedback_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    film_id: Mapped[int] = mapped_column(ForeignKey("films.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(nullable=False)
    value: Mapped[float | None] = mapped_column(nullable=True)
    context: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)

    __table_args__ = (
        CheckConstraint(
            "event_type IN "
            "('rating','like','seen','not_interested','snooze','clicked','watchlisted','prompt_answer')",
            name="ck_feedback_events_event_type",
        ),
        CheckConstraint(
            "context IN ('rec','search','poster_wall','prompt','import')",
            name="ck_feedback_events_context",
        ),
        Index("idx_feedback_user_time", "user_id", "created_at"),
        Index("idx_feedback_film", "film_id"),
    )


# ============ model_artifacts: pointer records for on-disk artefacts (Phase 3) ============
class ModelArtifact(Base):
    __tablename__ = "model_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    version: Mapped[str] = mapped_column(nullable=False)
    path: Mapped[str] = mapped_column(nullable=False)
    metrics_json: Mapped[str | None] = mapped_column(nullable=True)
    is_active: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    trained_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('feature_matrix','vocab','taste_model','item_embeddings','rec_snapshot')",
            name="ck_model_artifacts_kind",
        ),
        Index("idx_artifacts_kind_active", "kind", "is_active"),
    )


# ============ recommendations_cache: last computed ranking (serves UI fast) ============
class RecommendationCache(Base):
    __tablename__ = "recommendations_cache"

    profile: Mapped[str] = mapped_column(primary_key=True)
    film_id: Mapped[int] = mapped_column(ForeignKey("films.id"), primary_key=True)
    rank: Mapped[int] = mapped_column(nullable=False)
    score: Mapped[float] = mapped_column(nullable=False)
    components_json: Mapped[str | None] = mapped_column(nullable=True)
    model_version: Mapped[str] = mapped_column(nullable=False)
    generated_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)


# ============ sync_state: cursors + job bookkeeping ============
class SyncState(Base):
    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    cursor: Mapped[str | None] = mapped_column(nullable=True)
    last_run_at: Mapped[str | None] = mapped_column(nullable=True)
    last_ok_at: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str | None] = mapped_column(nullable=True)
    detail_json: Mapped[str | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('rss','csv_import','letterboxd_export','letterboxd_scrape',"
            "'availability_refresh','retrain','backup','letterboxd_log')",
            name="ck_sync_state_kind",
        ),
        CheckConstraint("status IN ('ok','running','error')", name="ck_sync_state_status"),
        UniqueConstraint("kind", "user_id", name="uq_sync_state_kind_user"),
    )


# ============ letterboxd_log_jobs: Phase 5 write-back queue ============
class LetterboxdLogJob(Base):
    __tablename__ = "letterboxd_log_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    film_id: Mapped[int] = mapped_column(ForeignKey("films.id"), nullable=False)
    payload_json: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(nullable=False, server_default=text("'queued'"))
    attempts: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)
    finished_at: Mapped[str | None] = mapped_column(nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','done','failed','fallback_shown')",
            name="ck_letterboxd_log_jobs_status",
        ),
    )


# ============ settings: misc key/value (region, thresholds, feature flags) ============
# Named AppSetting in Python to avoid colliding with app.config.Settings.
class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value_json: Mapped[str] = mapped_column(nullable=False)
    updated_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)


# ============ refresh_tokens: revocable JWT refresh sessions (Phase 4) ============
class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(nullable=False, unique=True)
    expires_at: Mapped[str] = mapped_column(nullable=False)
    revoked: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    created_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)

    __table_args__ = (Index("idx_refresh_user", "user_id", "revoked"),)


# ============ media_files: Phase 7 local library ============
class MediaFile(Base):
    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    film_id: Mapped[int | None] = mapped_column(ForeignKey("films.id"), nullable=True)
    path: Mapped[str] = mapped_column(nullable=False, unique=True)
    size_bytes: Mapped[int | None] = mapped_column(nullable=True)
    container: Mapped[str | None] = mapped_column(nullable=True)
    video_codec: Mapped[str | None] = mapped_column(nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(nullable=True)
    jellyfin_item_id: Mapped[str | None] = mapped_column(nullable=True)
    scanned_at: Mapped[str] = mapped_column(nullable=False, server_default=NOW)

    __table_args__ = (Index("idx_media_film", "film_id"),)
