"""Application settings, loaded from environment / .env file.

All settings are prefixed with MISHKA_ (e.g. MISHKA_TMDB_READ_TOKEN).
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .../mishka-hub/apps/server/app/config.py
#   parents[1] = apps/server (the backend dir, where .env lives)
#   parents[3] = mishka-hub  (the project root, where data/ lives)
SERVER_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(SERVER_DIR / ".env"),
        env_prefix="MISHKA_",
        extra="ignore",
    )

    # --- TMDB: provide a v4 read token (preferred) OR a v3 api key. ---
    tmdb_read_token: str = ""
    tmdb_api_key: str = ""

    # --- Household defaults (UK / Scotland). ---
    region: str = "GB"
    language: str = "en-GB"

    # --- CORS. Add the GitHub Pages origin here once it's live, e.g.
    # ["https://<user>.github.io"]. Override via MISHKA_CORS_ORIGINS as a JSON list. ---
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # SQLite lives in the project-level data/ folder (CWD-independent absolute path).
    database_url: str = f"sqlite:///{DATA_DIR / 'mishka.db'}"
    environment: str = "development"

    # --- Shared credential store (docs/phases/PHASE-2-credentials.md §2). ---
    # "keychain" (macOS Keychain via `keyring`) or "file" (Fernet-encrypted
    # data/secrets/secrets.enc). Defaults to keychain on macOS, file elsewhere.
    secret_backend: str = "keychain" if sys.platform == "darwin" else "file"

    # --- Interim bearer-token guard for Phases 2-3 (docs/API.md closing note). ---
    # Replaced transparently by JWTs in Phase 4. A single static long random
    # token, required by `Authorization: Bearer <token>` on every router
    # except /api/health.
    dev_token: str = ""

    # --- Background sync scheduler (PHASE-2 §4/§11). How often the app's
    # lifespan background task runs the full auto cascade (export -> scrape
    # -> rss, whichever succeeds first) for every user with a
    # letterboxd_username. PHASE-2 documents a ~6h RSS cadence; kept short-ish
    # by default for practical testing and made configurable here. ---
    sync_interval_hours: float = 6

    @property
    def tmdb_configured(self) -> bool:
        return bool(self.tmdb_read_token or self.tmdb_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
