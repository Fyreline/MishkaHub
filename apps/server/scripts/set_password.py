#!/usr/bin/env python3
"""Set (or reset) a household member's login password — the ONLY way a
password ever gets set. There is no HTTP endpoint for this, on purpose:
run locally, on the household's own machine, so a real password never
has to be typed into a browser form, sent over the wire during setup, or
seen by anything other than this terminal.

Usage (run from apps/server/, with the venv active):
    python scripts/set_password.py luminal@mishka-hub.local
    python scripts/set_password.py garfield@mishka-hub.local

Prompts for the new password twice (hidden input, not echoed). Only
works for an email that already has a row in `users` — see
docs/phases/PHASE-4-accounts-feedback.md §1: there is no user-creation
path either, the two accounts are fixed rows seeded when the DB was
built.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402
from app.security import hash_password  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <email>", file=sys.stderr)
        return 1

    email = sys.argv[1].strip().lower()
    session = SessionLocal()
    try:
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            print(f"No user with email {email!r}. Existing users:", file=sys.stderr)
            for u in session.scalars(select(User)):
                print(f"  - {u.email} ({u.display_name})", file=sys.stderr)
            return 1

        password = getpass.getpass(f"New password for {user.display_name} <{email}>: ")
        if len(password) < 8:
            print("Password must be at least 8 characters.", file=sys.stderr)
            return 1
        confirm = getpass.getpass("Confirm: ")
        if password != confirm:
            print("Passwords didn't match — nothing changed.", file=sys.stderr)
            return 1

        user.password_hash = hash_password(password)
        session.commit()
        print(f"Password set for {user.display_name} <{email}>.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
