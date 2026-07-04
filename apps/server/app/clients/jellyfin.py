"""Thin async client for a Jellyfin media server — docs/phases/PHASE-7-local-media-tv.md.

Jellyfin is the "dumb-but-excellent playback pipe" for owned films: it does
its own TMDB-aware library scraping (so we can join `media_files` to
`films.id` via `ProviderIds.Tmdb`) and exposes a remote-control API to start
playback on a signed-in client (the webOS TV app), per the phase doc's
decision record.

Configured via MISHKA_JELLYFIN_URL / MISHKA_JELLYFIN_API_KEY (Settings). Not
exercised against a real server in this environment — the household's
Jellyfin instance runs on a separate desktop machine (docs/PLAN.md) — so this
is intentionally minimal and defers to Jellyfin's own error responses rather
than guessing at edge cases we can't observe.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..config import Settings


class JellyfinError(RuntimeError):
    """Jellyfin returned an error, or the client isn't configured."""


class JellyfinClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        base_url = (settings.jellyfin_url or "").rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"accept": "application/json"},
            timeout=15.0,
        )

    @property
    def configured(self) -> bool:
        return bool(self._settings.jellyfin_url and self._settings.jellyfin_api_key)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _auth_params(self) -> dict[str, str]:
        return {"api_key": self._settings.jellyfin_api_key}

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self.configured:
            raise JellyfinError(
                "Jellyfin is not configured — set MISHKA_JELLYFIN_URL and "
                "MISHKA_JELLYFIN_API_KEY."
            )
        merged = {**self._auth_params(), **(params or {})}
        try:
            resp = await self._client.get(path, params=merged)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise JellyfinError(
                f"Jellyfin {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Jellyfin request failed: {exc}") from exc
        return resp.json() if resp.content else None

    async def _post(self, path: str, params: dict[str, Any] | None = None) -> None:
        if not self.configured:
            raise JellyfinError(
                "Jellyfin is not configured — set MISHKA_JELLYFIN_URL and "
                "MISHKA_JELLYFIN_API_KEY."
            )
        merged = {**self._auth_params(), **(params or {})}
        try:
            resp = await self._client.post(path, params=merged)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise JellyfinError(
                f"Jellyfin {exc.response.status_code}: {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise JellyfinError(f"Jellyfin request failed: {exc}") from exc

    async def library_items_with_tmdb_ids(self) -> list[dict[str, Any]]:
        """All movie items in the library that carry a TMDB provider id.

        Used by the media scanner to join `media_files.path` -> `films.id`
        via Jellyfin's own scrape, per PHASE-7 §2's "Jellyfin join (primary)".
        """
        data = await self._get(
            "/Items",
            {
                "Recursive": "true",
                "IncludeItemTypes": "Movie",
                "Fields": "ProviderIds,Path",
            },
        )
        items = (data or {}).get("Items", [])
        return [i for i in items if i.get("ProviderIds", {}).get("Tmdb")]

    async def find_tv_session(self) -> dict[str, Any] | None:
        """The active session running the Jellyfin webOS app, if any.

        PHASE-7 §3: "pick session where Client == 'Jellyfin webOS' (else 404
        tv_not_available)".
        """
        sessions = await self._get("/Sessions")
        for s in sessions or []:
            if s.get("Client") == "Jellyfin webOS":
                return s
        return None

    async def play(self, session_id: str, item_id: str) -> None:
        """Command a session to start playing an item now (PHASE-7 §3)."""
        await self._post(
            f"/Sessions/{session_id}/Playing",
            {"itemIds": item_id, "playCommand": "PlayNow"},
        )
