"""Shared pytest fixtures.

There was no test suite in this repo before this file — these fixtures are
intentionally minimal (just enough to exercise a router end-to-end against
an isolated in-memory SQLite database) rather than a full test framework.

Fixtures:
- ``db_session``: a fresh in-memory SQLite database per test (tables created
  from ``app.models.Base.metadata``), seeded with the two household users
  (ids 1 and 2, matching the real app's fixed household).
- ``client``: a ``fastapi.testclient.TestClient`` wired to the real ``app``
  from ``app.main``, with the ``get_session`` dependency overridden to hand
  out ``db_session`` instead of a session bound to the real
  ``data/mishka.db`` — tests never touch the production database.
- ``service_token``: sets ``get_settings().service_token`` for the duration
  of a test and restores the previous value afterward. ``app.config.
  get_settings()`` is ``@lru_cache``d and the same object is stashed on
  ``app.state.settings`` in the app's lifespan, so mutating the live object
  is the standard way to flip a setting for one test (pydantic-settings +
  lru_cache pattern also used in the sibling Michi/Sukumo repos).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import get_session
from app.main import app
from app.models import Base, User


@pytest.fixture()
def db_session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = TestingSessionLocal()
    session.add_all(
        [
            User(id=1, email="one@example.test", display_name="Household One"),
            User(id=2, email="two@example.test", display_name="Household Two"),
        ]
    )
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    def _override_get_session() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture()
def service_token() -> Iterator[str]:
    settings = get_settings()
    original = settings.service_token
    token = "test-service-token"
    settings.service_token = token
    try:
        yield token
    finally:
        settings.service_token = original
