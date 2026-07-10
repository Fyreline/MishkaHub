"""Tests for GET /api/activity/service (app/routers/service.py)."""
from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import FeedbackEvent, Film, Rating, Watch

URL = "/api/activity/service"


def test_503_when_service_token_unset(client: TestClient) -> None:
    resp = client.get(URL)
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "service_not_configured"
    assert set(body.keys()) == {"detail", "code"}


def test_401_when_no_header(client: TestClient, service_token: str) -> None:
    resp = client.get(URL)
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_401_when_wrong_token(client: TestClient, service_token: str) -> None:
    resp = client.get(URL, headers={"Authorization": "Bearer not-the-token"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def test_401_when_malformed_header(client: TestClient, service_token: str) -> None:
    resp = client.get(URL, headers={"Authorization": service_token})
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthorized"


def _seed(db_session: Session) -> None:
    films = [
        Film(id=1, title="Film One", poster_path="/one.jpg"),
        Film(id=2, title="Film Two", poster_path=None),
        Film(id=3, title="Film Three", poster_path="/three.jpg"),
        Film(id=4, title="Film Four", poster_path="/four.jpg"),
        Film(id=5, title="Film Five", poster_path=None),  # watchlisted, never watched
        Film(id=6, title="Film Six", poster_path=None),  # watchlisted, but watched
    ]
    db_session.add_all(films)
    db_session.commit()

    # Oldest -> newest by explicit watched_date, plus one watch (film 3) with
    # no watched_date at all, which must fall back to created_at and — since
    # created_at is "now" — sort as the most recent entry regardless of the
    # explicit dates below (all set safely in the past).
    watch_film1 = Watch(user_id=1, film_id=1, watched_date="2000-01-01", source="in-app")
    watch_film2 = Watch(user_id=2, film_id=2, watched_date="2000-01-02", source="in-app")
    watch_film4 = Watch(user_id=2, film_id=4, watched_date="2000-01-02", source="in-app")
    watch_film6 = Watch(user_id=1, film_id=6, watched_date="1999-01-01", source="in-app")
    db_session.add_all([watch_film1, watch_film2, watch_film4, watch_film6])
    db_session.commit()

    watch_film3 = Watch(user_id=1, film_id=3, watched_date=None, source="in-app")
    db_session.add(watch_film3)
    db_session.commit()
    db_session.refresh(watch_film3)  # populate server-generated created_at

    db_session.add(
        Rating(user_id=1, film_id=1, rating=4.5, source="in-app")
    )

    # watchlist_count seed: film 5 has no Watch row (counts); two different
    # users both "watchlisted" it, but it's one distinct film_id so the count
    # must still be 1, not 2. film 6 has a Watch row above, so its
    # 'watchlisted' event must NOT be counted.
    db_session.add_all(
        [
            FeedbackEvent(user_id=1, film_id=5, event_type="watchlisted"),
            FeedbackEvent(user_id=2, film_id=5, event_type="watchlisted"),
            FeedbackEvent(user_id=1, film_id=6, event_type="watchlisted"),
        ]
    )
    db_session.commit()


def test_200_with_correct_token(
    client: TestClient, db_session: Session, service_token: str
) -> None:
    _seed(db_session)

    resp = client.get(URL, headers={"Authorization": f"Bearer {service_token}"})
    assert resp.status_code == 200
    body = resp.json()

    assert set(body.keys()) == {"recent", "watchlist_count"}
    assert body["watchlist_count"] == 1

    recent = body["recent"]
    assert len(recent) == 5

    expected_titles_in_order = [
        "Film Three",  # no watched_date -> created_at fallback -> most recent
        "Film Four",  # tie on 2000-01-02, higher Watch.id
        "Film Two",  # tie on 2000-01-02, lower Watch.id
        "Film One",  # 2000-01-01
        "Film Six",  # 1999-01-01, oldest
    ]
    assert [item["title"] for item in recent] == expected_titles_in_order

    for item in recent:
        assert set(item.keys()) == {"title", "watched_at", "poster_url", "rating"}

    by_title = {item["title"]: item for item in recent}

    assert by_title["Film One"]["rating"] == 4.5
    assert by_title["Film One"]["poster_url"] == "https://image.tmdb.org/t/p/w500/one.jpg"
    assert by_title["Film One"]["watched_at"] == "2000-01-01"

    assert by_title["Film Two"]["rating"] is None
    assert by_title["Film Two"]["poster_url"] is None
    assert by_title["Film Two"]["watched_at"] == "2000-01-02"

    assert by_title["Film Four"]["rating"] is None
    assert by_title["Film Four"]["watched_at"] == "2000-01-02"

    assert by_title["Film Six"]["rating"] is None
    assert by_title["Film Six"]["watched_at"] == "1999-01-01"

    # Film Three has no watched_date — watched_at must fall back to created_at
    # (a full timestamp string), and it must not be null/empty.
    assert by_title["Film Three"]["watched_at"]
    assert by_title["Film Three"]["watched_at"] != "2000-01-01"


def test_no_watches_returns_empty_recent(client: TestClient, service_token: str) -> None:
    resp = client.get(URL, headers={"Authorization": f"Bearer {service_token}"})
    assert resp.status_code == 200
    assert resp.json() == {"recent": [], "watchlist_count": 0}
