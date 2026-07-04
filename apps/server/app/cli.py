"""Mishka Hub CLI.

Run with ``python -m app.cli <subcommand>`` from ``apps/server/``.

Subcommands are registered as subparsers so later phases (e.g. the shared
credential store) can add their own group without touching this dispatch
logic — see ``build_parser()``.
"""
from __future__ import annotations

import argparse
import os
import stat
import sys

import yaml
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from .config import PROJECT_ROOT, SERVER_DIR, get_settings
from .db import SessionLocal
from .models import Subscription, User
from .secretstore import (
    LETTERBOXD_SERVICE,
    MASTER_KEY_ACCOUNT,
    MASTER_KEY_SERVICE,
    SECRETS_DIR,
    KeychainSecretStore,
    _ensure_secrets_dir,
    get_secret_store,
)

ALEMBIC_INI = PROJECT_ROOT / "apps" / "server" / "alembic.ini"
ENV_FILE = SERVER_DIR / ".env"


def _alembic_config() -> Config:
    cfg = Config(str(ALEMBIC_INI))
    return cfg


def cmd_migrate(_args: argparse.Namespace) -> None:
    """Run Alembic migrations up to head."""
    command.upgrade(_alembic_config(), "head")
    print("Migrated to head.")


def cmd_seed(_args: argparse.Namespace) -> None:
    """Seed the two household users and their subscriptions from config/household.yaml.

    Idempotent: safe to run repeatedly (upserts by primary key).
    """
    household_path = PROJECT_ROOT / "config" / "household.yaml"
    with open(household_path, encoding="utf-8") as fh:
        household = yaml.safe_load(fh)

    with SessionLocal() as session:
        # users.id is INTEGER PRIMARY KEY (DATA_MODEL.md: "1 and 2. Two rows,
        # ever.") — household.yaml uses string slugs, so the primary member
        # becomes id=1, the other id=2.
        yaml_users = sorted(
            household["users"], key=lambda u: 0 if u.get("primary") else 1
        )
        for db_id, yu in enumerate(yaml_users, start=1):
            existing = session.get(User, db_id)
            email = f"{yu['id']}@mishka-hub.local"
            if existing is None:
                session.add(
                    User(
                        id=db_id,
                        email=email,
                        display_name=yu["display_name"],
                        letterboxd_username=yu["letterboxd_username"],
                    )
                )
            else:
                existing.email = email
                existing.display_name = yu["display_name"]
                existing.letterboxd_username = yu["letterboxd_username"]

        for sub in household["subscriptions"]:
            provider_id = sub["tmdb_provider_id"]
            existing_sub = session.get(Subscription, provider_id)
            if existing_sub is None:
                session.add(
                    Subscription(provider_id=provider_id, provider_name=sub["name"], active=1)
                )
            else:
                existing_sub.provider_name = sub["name"]
                existing_sub.active = 1

        session.commit()

    with SessionLocal() as session:
        n_users = len(session.scalars(select(User)).all())
        n_subs = len(session.scalars(select(Subscription)).all())
    print(f"Seeded {n_users} users, {n_subs} subscriptions.")


def cmd_secrets_check(_args: argparse.Namespace) -> None:
    """For each configured user, report whether their Letterboxd secret is set.

    Never prints the secret itself. Running this interactively is also how
    a human triggers/accepts the macOS "Always Allow" Keychain dialog for
    items the app didn't create itself (PHASE-2-credentials.md §3).
    """
    settings = get_settings()
    store = get_secret_store(settings)
    print(f"Backend: {settings.secret_backend}")

    with SessionLocal() as session:
        users = session.scalars(select(User)).all()

    if not users:
        print("No users found (run `python -m app.cli seed` first).")
        return

    for user in users:
        if not user.letterboxd_username:
            print(f"  user {user.id} ({user.display_name}): no letterboxd_username set")
            continue
        try:
            secret = store.get(LETTERBOXD_SERVICE, user.letterboxd_username)
        except Exception as exc:  # noqa: BLE001 — surface backend errors, don't crash the loop
            print(
                f"  {user.letterboxd_username}: ERROR reading secret "
                f"({type(exc).__name__}: {exc})"
            )
            continue
        status = "configured" if secret else "not configured"
        print(f"  {user.letterboxd_username}: {status}")


def cmd_secrets_init(_args: argparse.Namespace) -> None:
    """Prepare the file secret-store backend: ensure MISHKA_SECRETS_KEY exists.

    Generates a base64 Fernet key if one isn't already present in the
    environment or apps/server/.env, and writes it to .env (chmod 600).
    No-op (besides a log line) if a key is already configured.
    """
    if os.environ.get("MISHKA_SECRETS_KEY"):
        print("MISHKA_SECRETS_KEY is already set in the environment. Nothing to do.")
        return

    existing_line = None
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("MISHKA_SECRETS_KEY="):
                existing_line = line
                break

    if existing_line is not None:
        print(f"MISHKA_SECRETS_KEY already present in {ENV_FILE}. Nothing to do.")
        return

    new_key = Fernet.generate_key().decode("utf-8")
    with ENV_FILE.open("a", encoding="utf-8") as fh:
        fh.write("\n# Fernet file secret-store key (python -m app.cli secrets init)\n")
        fh.write(f"MISHKA_SECRETS_KEY={new_key}\n")
    os.chmod(ENV_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600

    print(f"Generated MISHKA_SECRETS_KEY and appended it to {ENV_FILE} (chmod 600).")
    print("Restart the app / re-source your shell so it picks up the new value.")


def cmd_secrets_rotate_key(_args: argparse.Namespace) -> None:
    """Master-key rotate (PHASE-2-credentials.md §5).

    Decrypts every data/secrets/*.enc blob with the current Keychain-held
    Fernet master key, generates a new key, re-encrypts everything, then
    swaps the Keychain item atomically (write new, then drop old only on
    success). Safe to run with zero blobs present.
    """
    keychain = KeychainSecretStore()
    old_key_str = keychain.get(MASTER_KEY_SERVICE, MASTER_KEY_ACCOUNT)

    if not old_key_str:
        # Nothing to rotate from — just establish a fresh master key.
        new_key = Fernet.generate_key()
        keychain.set(MASTER_KEY_SERVICE, MASTER_KEY_ACCOUNT, new_key.decode("utf-8"))
        print("No existing master key found; generated a new one.")
        return

    old_fernet = Fernet(old_key_str.encode("utf-8"))
    new_key = Fernet.generate_key()
    new_fernet = Fernet(new_key)

    _ensure_secrets_dir()
    blob_paths = sorted(SECRETS_DIR.glob("*.enc"))
    # secrets.enc (the FernetFileSecretStore's own store) is keyed by
    # MISHKA_SECRETS_KEY, not the Keychain master key — only session blobs
    # (letterboxd_session_*.enc) are encrypted with the master key.
    session_blobs = [p for p in blob_paths if p.name.startswith("letterboxd_session_")]

    reencrypted: dict[str, bytes] = {}
    for path in session_blobs:
        ciphertext = path.read_bytes()
        try:
            plaintext = old_fernet.decrypt(ciphertext)
        except InvalidToken:
            print(f"  WARNING: could not decrypt {path.name} with current master key; skipping")
            continue
        reencrypted[path.name] = new_fernet.encrypt(plaintext)

    # Write re-encrypted blobs out only after all decrypts succeeded above,
    # then swap the Keychain item — if anything failed we'd have bailed
    # via the exception propagating before this point.
    for name, ciphertext in reencrypted.items():
        path = SECRETS_DIR / name
        path.write_bytes(ciphertext)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)

    keychain.set(MASTER_KEY_SERVICE, MASTER_KEY_ACCOUNT, new_key.decode("utf-8"))
    print(f"Rotated master key. Re-encrypted {len(reencrypted)} session blob(s).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Mishka Hub CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_migrate = subparsers.add_parser("migrate", help="Run Alembic migrations to head")
    p_migrate.set_defaults(func=cmd_migrate)

    p_seed = subparsers.add_parser(
        "seed", help="Seed users + subscriptions from config/household.yaml"
    )
    p_seed.set_defaults(func=cmd_seed)

    p_secrets = subparsers.add_parser(
        "secrets", help="Manage the shared credential store (see docs/phases/PHASE-2-credentials.md)"
    )
    secrets_subparsers = p_secrets.add_subparsers(dest="secrets_command", required=True)

    p_secrets_check = secrets_subparsers.add_parser(
        "check", help="Report configured/not-configured per user (never prints secrets)"
    )
    p_secrets_check.set_defaults(func=cmd_secrets_check)

    p_secrets_init = secrets_subparsers.add_parser(
        "init", help="Generate MISHKA_SECRETS_KEY for the file backend, if not already set"
    )
    p_secrets_init.set_defaults(func=cmd_secrets_init)

    p_secrets_rotate = secrets_subparsers.add_parser(
        "rotate-key", help="Rotate the Fernet master key, re-encrypting existing session blobs"
    )
    p_secrets_rotate.set_defaults(func=cmd_secrets_rotate_key)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
