"""Shared credential store (docs/phases/PHASE-2-credentials.md).

One small `SecretStore` interface, two backends:

- `KeychainSecretStore` — macOS Keychain generic-password items via the
  `keyring` library (default backend on macOS *is* the Keychain).
- `FernetFileSecretStore` — a Fernet-encrypted JSON map on disk, for hosts
  without a keychain (future Windows desktop, CI).

Backend selection is via `Settings.secret_backend` (`MISHKA_SECRET_BACKEND`,
default `keychain` on macOS / `file` elsewhere — see `app/config.py`).

This module is imported by both the Phase 2 import automation and the
Phase 5 Playwright write-back (docs/phases/PHASE-2-credentials.md §1); it
must never log, print, or return a secret value except through `get()`.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from cryptography.fernet import Fernet, InvalidToken

from .config import DATA_DIR

if TYPE_CHECKING:
    from .config import Settings

SECRETS_DIR = DATA_DIR / "secrets"
SECRETS_FILE = SECRETS_DIR / "secrets.enc"

# Keychain layout (docs/phases/PHASE-2-credentials.md §3).
MASTER_KEY_SERVICE = "mishka-hub"
MASTER_KEY_ACCOUNT = "fernet-master-key"
LETTERBOXD_SERVICE = "mishka-hub-letterboxd"


class SecretStore(Protocol):
    def get(self, service: str, account: str) -> str | None: ...

    def set(self, service: str, account: str, secret: str) -> None: ...

    def delete(self, service: str, account: str) -> None: ...


class KeychainSecretStore:
    """Backed by the OS keychain via `keyring` (macOS Keychain by default)."""

    def get(self, service: str, account: str) -> str | None:
        import keyring

        return keyring.get_password(service, account)

    def set(self, service: str, account: str, secret: str) -> None:
        import keyring

        keyring.set_password(service, account, secret)

    def delete(self, service: str, account: str) -> None:
        import keyring
        import keyring.errors

        try:
            keyring.delete_password(service, account)
        except keyring.errors.PasswordDeleteError:
            # Already absent — delete() is a no-op in that case, matching
            # the Fernet file backend's behaviour below.
            pass


def _ensure_secrets_dir() -> None:
    """Create data/secrets/ with 0700 perms if it doesn't already exist."""
    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS_DIR, stat.S_IRWXU)  # 0700: rwx for owner only


def _chmod_600(path: Path) -> None:
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600


class FernetFileSecretStore:
    """Fallback backend: a Fernet-encrypted JSON map under data/secrets/.

    Layout on disk (before encryption): {"service": {"account": "secret"}}.
    The key comes from the MISHKA_SECRETS_KEY env var (a base64 Fernet key,
    see `python -m app.cli secrets init`).
    """

    def __init__(self, key: str | None = None) -> None:
        raw_key = key if key is not None else os.environ.get("MISHKA_SECRETS_KEY")
        if not raw_key:
            raise RuntimeError(
                "MISHKA_SECRETS_KEY is not set. Run `python -m app.cli secrets init` "
                "to generate one (file secret-store backend)."
            )
        self._fernet = Fernet(raw_key.encode("utf-8") if isinstance(raw_key, str) else raw_key)

    def _load(self) -> dict[str, dict[str, str]]:
        if not SECRETS_FILE.exists():
            return {}
        ciphertext = SECRETS_FILE.read_bytes()
        if not ciphertext:
            return {}
        plaintext = self._fernet.decrypt(ciphertext)
        return json.loads(plaintext.decode("utf-8"))

    def _save(self, data: dict[str, dict[str, str]]) -> None:
        _ensure_secrets_dir()
        plaintext = json.dumps(data).encode("utf-8")
        ciphertext = self._fernet.encrypt(plaintext)
        SECRETS_FILE.write_bytes(ciphertext)
        _chmod_600(SECRETS_FILE)

    def get(self, service: str, account: str) -> str | None:
        data = self._load()
        return data.get(service, {}).get(account)

    def set(self, service: str, account: str, secret: str) -> None:
        data = self._load()
        data.setdefault(service, {})[account] = secret
        self._save(data)

    def delete(self, service: str, account: str) -> None:
        data = self._load()
        if service in data and account in data[service]:
            del data[service][account]
            if not data[service]:
                del data[service]
            self._save(data)


def get_secret_store(settings: "Settings") -> SecretStore:
    """Factory: pick the backend per `settings.secret_backend`."""
    if settings.secret_backend == "keychain":
        return KeychainSecretStore()
    if settings.secret_backend == "file":
        return FernetFileSecretStore()
    raise ValueError(f"Unknown MISHKA_SECRET_BACKEND: {settings.secret_backend!r}")


# ------------------------------------------------------------------
# Session blobs (Playwright storage_state) — PHASE-2-credentials.md §4.
# ------------------------------------------------------------------


def _get_or_create_master_key(store: SecretStore | None = None) -> bytes:
    """Fetch the Fernet master key from the Keychain, generating it on first use.

    Always stored via KeychainSecretStore regardless of the configured
    SecretStore backend, per PHASE-2-credentials.md §4 ("the Fernet master
    key itself held in the Keychain").
    """
    keychain = store if store is not None else KeychainSecretStore()
    existing = keychain.get(MASTER_KEY_SERVICE, MASTER_KEY_ACCOUNT)
    if existing:
        return existing.encode("utf-8")

    new_key = Fernet.generate_key()
    keychain.set(MASTER_KEY_SERVICE, MASTER_KEY_ACCOUNT, new_key.decode("utf-8"))
    return new_key


def _session_blob_path(user_id: int) -> Path:
    return SECRETS_DIR / f"letterboxd_session_{user_id}.enc"


def save_session_blob(user_id: int, storage_state_json: str) -> None:
    """Encrypt and persist a Playwright storage_state JSON blob for user_id."""
    key = _get_or_create_master_key()
    fernet = Fernet(key)
    ciphertext = fernet.encrypt(storage_state_json.encode("utf-8"))

    _ensure_secrets_dir()
    path = _session_blob_path(user_id)
    path.write_bytes(ciphertext)
    _chmod_600(path)


def load_session_blob(user_id: int) -> str | None:
    """Decrypt and return the storage_state JSON blob for user_id, or None."""
    path = _session_blob_path(user_id)
    if not path.exists():
        return None

    key = _get_or_create_master_key()
    fernet = Fernet(key)
    ciphertext = path.read_bytes()
    try:
        plaintext = fernet.decrypt(ciphertext)
    except InvalidToken:
        return None
    return plaintext.decode("utf-8")


def delete_session_blob(user_id: int) -> None:
    """Remove the session blob for user_id, if present (Clear flow, §5)."""
    path = _session_blob_path(user_id)
    if path.exists():
        path.unlink()
