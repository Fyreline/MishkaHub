# Phase 2 — Shared Letterboxd Credential Store

Purpose: one credential/secret module (`SecretStore`) designed once and consumed by **both** the [Phase 2 automated-export import](PHASE-2-letterboxd-import.md) (§2) and the [Phase 5 Playwright write-back](PHASE-5-letterboxd-writeback.md). It holds each member's Letterboxd password and the encrypted Playwright session state, keeps every secret out of the database, logs, API responses and the repo, and carries the ToS-risk acknowledgement gate both features share. Built in Phase 2 (pulled forward from Phase 5 — see [PLAN.md](../PLAN.md)).

**Status: planned** (except the first Keychain item, which already exists — see §3.)

---

## 1. Design in one paragraph

Secrets live in the **macOS Keychain** as generic-password items, read through Python [`keyring`](https://pypi.org/project/keyring/) (whose default backend on macOS *is* the Keychain). The app never stores a password anywhere else — not in SQLite, not in `.env`, not in `config/household.yaml`. Bulkier sensitive blobs (Playwright `storage_state` session cookies) are too large and too structured for tidy Keychain items, so they live as **Fernet-encrypted files** under `data/secrets/`, with the Fernet master key itself held in the Keychain. For a possible future move to the Windows desktop (no macOS Keychain), a **Fernet-encrypted file backend** implements the same interface. Fernet is the [`cryptography` library's authenticated symmetric-encryption recipe](https://cryptography.io/en/latest/fernet/) (AES-128-CBC + HMAC-SHA256, versioned tokens) — no hand-rolled crypto.

## 2. The `SecretStore` abstraction

One small interface; two backends; everything above it (import automation, write-back, CLI) is backend-agnostic.

```python
# apps/server/app/secretstore.py  (shared — imported by importers/ AND letterboxd_write/)
from typing import Protocol

class SecretStore(Protocol):
    def get(self, service: str, account: str) -> str | None: ...
    def set(self, service: str, account: str, secret: str) -> None: ...
    def delete(self, service: str, account: str) -> None: ...
```

| Backend | Class | Where secrets live | When used |
|---|---|---|---|
| **Keychain** (primary) | `KeychainSecretStore` | macOS Keychain generic-password items, via `keyring.get_password(service, account)` / `set_password` / `delete_password` | Default on `sys.platform == "darwin"` (the current home-Mac host) |
| **Fernet file** (portable fallback) | `FernetFileSecretStore` | `data/secrets/secrets.enc` — a Fernet-encrypted JSON map `{service: {account: secret}}`; key from env `MISHKA_SECRETS_KEY` | Future Windows desktop host, CI, any box without a keychain |

- Selection: `MISHKA_SECRET_BACKEND=keychain|file` (pydantic-settings, `MISHKA_` prefix as in `apps/server/app/config.py`); default `keychain` on macOS, `file` elsewhere.
- `secrets.enc` and everything under `data/secrets/` is `chmod 600` in a `0700` directory, and `data/` is already gitignored ([ARCHITECTURE.md](../ARCHITECTURE.md) §7).
- File-backend key management: `python -m app.cli secrets init` generates `MISHKA_SECRETS_KEY` (a base64 Fernet key) and writes it to `apps/server/.env` (`chmod 600`). Documented trade-off: on a keychain-less host the key sits in a file readable by the same OS user — weaker than the Keychain, acceptable for the household threat model. (Windows could later use `keyring`'s Credential Locker backend instead; not designed here.)

## 3. Keychain layout (naming convention)

| Service | Account | Holds | Created by |
|---|---|---|---|
| `mishka-hub-letterboxd` | `<letterboxd_username>` — `Luminalmvm`, `garfieldsama` | that member's Letterboxd password | **`Luminalmvm`'s item already exists** (added manually as a generic password). `garfieldsama`'s is added later via the UI (§5) or `security` CLI |
| `mishka-hub` | `fernet-master-key` | base64 Fernet key that encrypts the blob files in `data/secrets/` | generated on first use by the app |

Resolution path: `users.letterboxd_username` ([DATA_MODEL.md](../DATA_MODEL.md)) → `SecretStore.get("mishka-hub-letterboxd", username)`. The usernames are seeded from [`config/household.yaml`](../../config/household.yaml), so the app needs no extra mapping table — the Keychain **is** the credentials table, which is why [DATA_MODEL.md](../DATA_MODEL.md) §"Secrets" documents a layout rather than DDL.

```python
import keyring  # macOS backend == Keychain (https://pypi.org/project/keyring/)
password = keyring.get_password("mishka-hub-letterboxd", "Luminalmvm")  # None if absent
```

Equivalent `security` CLI ([man page](https://ss64.com/mac/security.html)) for manual ops:

```bash
# inspect (metadata only — add -w to print the secret, avoid in shared terminals):
security find-generic-password -s mishka-hub-letterboxd -a Luminalmvm
# add the second member's item by hand (prompts for the password):
security add-generic-password -s mishka-hub-letterboxd -a garfieldsama -w
```

### Keychain access grants (the one gotcha)

macOS ACLs Keychain items **per requesting binary**. The first time the Python interpreter reads an item it didn't create (e.g. `Luminalmvm`'s manually added one), macOS shows an *"python wants to use your confidential information…"* dialog. Handle it once, deliberately:

- **Interactive grant (recommended):** run `python -m app.cli secrets check` once in a terminal and click **Always Allow** — this adds the interpreter to the item's ACL permanently.
- **Pre-authorised creation:** items created via `security add-generic-password … -T /path/to/python` (the *resolved* interpreter binary, not the venv symlink) or by the app itself via `keyring.set_password` need no dialog.
- **Headless caveat:** the launchd service ([DEPLOYMENT.md](../DEPLOYMENT.md)) cannot answer a GUI prompt — a run that hits an ungranted item fails with `keyring.errors.KeyringLocked`/access error, which the import cascade records as `no_credentials` and falls through ([PHASE-2 §1](PHASE-2-letterboxd-import.md)). Do the interactive grant before enabling the schedule.
- Upgrading/moving the Python interpreter changes the binary path → macOS re-prompts. Re-run `secrets check` after any Python upgrade; note this in the server README at implementation time.

## 4. Session blobs (Playwright `storage_state`)

Login sessions are as sensitive as passwords and much bigger, so:

- After every authenticated Playwright run (export **or** write-back), `storage_state` is exported and written to `data/secrets/letterboxd_session_<user_id>.enc` = `Fernet(master_key).encrypt(storage_state_json)`.
- The plaintext Playwright profile directory is **wiped after each run**; the next run rehydrates the context from the ciphertext. The password therefore only transits memory during an actual (re)login.
- `ensure_session(user)` — login + session persistence — is **built in Phase 2** with the export automation ([PHASE-2 §2](PHASE-2-letterboxd-import.md)) and **reused verbatim by Phase 5**; login selectors live in the shared selectors module ([PHASE-5 §4](PHASE-5-letterboxd-writeback.md)).

## 5. Credential lifecycle

Endpoints are defined in [API.md](../API.md) §Phase 2 (they ship with Phase 2, not Phase 5).

| Flow | Steps |
|---|---|
| **Set (one-time UI)** | Settings → "Letterboxd account" card, per signed-in user: password field (never echoed back), ToS modal (§6) on first save → `PUT /api/letterboxd/credentials` over the tunnel's TLS → `SecretStore.set("mishka-hub-letterboxd", username, password)` → response is only `{"configured": true}` |
| **Check** | `GET /api/letterboxd/credentials/status` → `{"configured": bool, "tos_acknowledged": bool, "backend": "keychain"}` — computed by probing `SecretStore.get(...) is not None`; the secret itself is never returned by any endpoint |
| **Rotate** | changed your password on letterboxd.com → same `PUT` (overwrites the Keychain item) → the session blob for that user is deleted so the next run performs a fresh login |
| **Clear** | `DELETE /api/letterboxd/credentials` → `keyring.delete_password(...)` + delete `letterboxd_session_<user_id>.enc`. The import cascade then skips source 1 (export) and falls to the public scrape; the ToS acknowledgement record is retained as consent history |
| **Master-key rotate** | `python -m app.cli secrets rotate-key`: decrypt all `data/secrets/*.enc`, generate a new Fernet key, re-encrypt, replace the `mishka-hub`/`fernet-master-key` item atomically |

## 6. ToS-risk acknowledgement gate (shared)

Automating a member account — for export download *or* write-back — is against Letterboxd's [terms](https://letterboxd.com/legal/terms-of-use/) on scripted access, and at least one community exporter reports that "as of December 2025, Letterboxd has forbidden the use of scrapers" ([aaronmanning/letterboxd-export](https://git.aaronmanning.net/aaronmanning/letterboxd-export)). The risk statement and mitigations are in [PHASE-5 §1](PHASE-5-letterboxd-writeback.md); this module owns the **gate**:

- One acknowledgement per user covers both features (same risk class: automation on their own account). Stored as `settings` key `letterboxd_automation_ack_user_<id>` = ISO timestamp ([DATA_MODEL.md](../DATA_MODEL.md)).
- First `PUT /api/letterboxd/credentials` must carry `"acknowledge_tos": true`, otherwise `403 {"code": "tos_not_acknowledged"}`. The UI shows the risk modal (account-suspension possibility, breaks-without-notice) before enabling the save button.
- The import cascade and the write-back worker both refuse to start an authenticated browser for a user whose ack key is absent.

## 7. Invariants (grep-testable)

- No plaintext secret at rest outside the Keychain (or `secrets.enc` ciphertext on the file backend).
- No secret ever: in SQLite, in any log line, in any API response body, in the repo, in `config/household.yaml`.
- `data/secrets/` contents are always Fernet tokens (they begin with the `gAAAA…` version prefix), never raw JSON.

## 8. Acceptance criteria

- [ ] `keyring.get_password("mishka-hub-letterboxd", "Luminalmvm")` returns the pre-existing manually created secret after a single interactive **Always Allow** grant; subsequent headless (launchd) runs read it without any GUI prompt.
- [ ] `PUT /api/letterboxd/credentials` for `garfieldsama` (with `acknowledge_tos: true`) creates the Keychain item; the same request without the ack flag on first save returns `403 tos_not_acknowledged`.
- [ ] A grep sweep of the DB file, server logs and API responses after a set/rotate/clear cycle finds no plaintext password (test plants a canary password).
- [ ] `GET /api/letterboxd/credentials/status` reports `configured`/`tos_acknowledged`/`backend` correctly in all four combinations of item-present × ack-present.
- [ ] `DELETE /api/letterboxd/credentials` removes the Keychain item **and** the session blob; the next import run's cascade log shows source 1 skipped with `no_credentials`.
- [ ] Session blob on disk is Fernet ciphertext; the plaintext Playwright profile dir does not exist between runs.
- [ ] With `MISHKA_SECRET_BACKEND=file`, the full set/status/rotate/clear cycle passes on the same test suite (backend swap is invisible above the interface).
- [ ] Master-key rotation re-encrypts existing blobs and the next authenticated run succeeds without re-login.

## 9. Cross-references

- Consumed by: [PHASE-2 import cascade §2](PHASE-2-letterboxd-import.md) · [PHASE-5 write-back §3](PHASE-5-letterboxd-writeback.md)
- Endpoints: [API.md §Phase 2 — credentials](../API.md)
- Storage layout note: [DATA_MODEL.md §Secrets](../DATA_MODEL.md)
- Risk statement text: [PHASE-5 §1](PHASE-5-letterboxd-writeback.md)
