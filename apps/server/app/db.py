"""Database engine/session setup.

Mirrors docs/DATA_MODEL.md §1 (connection pragmas) and §5 (migrations via
Alembic). This module only builds the engine/session machinery — wiring it
into the FastAPI lifespan (creating/disposing the engine on app.state) is a
later integration step in app/main.py, not done here.
"""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

settings = get_settings()

# Ensure the parent directory of the SQLite file exists before connecting.
# database_url looks like "sqlite:///{DATA_DIR / 'mishka.db'}"; only bother
# with this for sqlite URLs (other backends manage their own storage).
if settings.database_url.startswith("sqlite:///"):
    _db_path = settings.database_url.removeprefix("sqlite:///")
    from pathlib import Path

    Path(_db_path).resolve().parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Apply the pragmas from docs/DATA_MODEL.md §1 on every new DBAPI connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a Session, closing it after the request."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
