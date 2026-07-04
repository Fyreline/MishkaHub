# Phase 5 — Letterboxd Write-back (Playwright)

Purpose: when a user marks a film watched/rated/liked/reviewed in Mishka Hub, log it to their Letterboxd account automatically. Locked decision: **browser automation with the users' own credentials** (the official API is request-only and explicitly refuses recommendation/personal projects — see [PLAN.md](../PLAN.md)). This doc specifies the Playwright flow, retries, the ToS risk statement, and the one-click fallback for when automation breaks. Credential storage and login-session management are **shared with the Phase 2 import cascade** and specified once in [PHASE-2-credentials.md](PHASE-2-credentials.md) (§3 below is a pointer). Queue table: [`letterboxd_log_jobs`](../DATA_MODEL.md); endpoints: [API.md](../API.md) §Phase 5.

**Status: planned**

---

## 1. ToS risk statement (read before building)

Letterboxd's terms prohibit automated/scripted access to member accounts. This feature:
- acts only on the **couple's own accounts**, at their explicit request, logging **their own viewing** — no scraping of other members, no bulk collection, no redistribution;
- is rate-trivial (a few log actions per week, human-scale timing);
- can nonetheless result in **account suspension** if detected, and **will break without notice** whenever letterboxd.com changes its DOM.

Accepted trade-off (locked). Mitigations: human-like pacing, persistent real sessions (login rarely), the fallback path (§6), and a kill switch (`settings.letterboxd_writeback_enabled=false` disables the worker; the UI then always shows the fallback).

## 2. Architecture

```
POST /api/letterboxd/log ──► letterboxd_log_jobs (queued)
                                   │  single async worker, one job at a time
                                   ▼
                        Playwright (chromium, headless)
                        persistent context per user:
                        data/playwright/user_<id>/   (session cookies live here)
                                   │ success                │ failure ×3
                                   ▼                        ▼
                            job done                 job failed → fallback
                            (+ mirror rows           payload returned to UI (§6)
                             into local DB)          + screenshot saved for debugging
```

- New dep: `playwright` (`playwright install chromium` documented in server README; add to [DEPLOYMENT.md](../DEPLOYMENT.md) launchd notes — first run downloads the browser).
- Worker runs in-process (asyncio + Playwright async API), serialised — two users' jobs never run concurrently (memory + detectability).
- Every job: random 1–3 s pauses between steps; realistic UA; viewport 1280×800.

## 3. Credential storage (shared module — do not re-implement here)

Defined once in **[PHASE-2-credentials.md](PHASE-2-credentials.md)** and already built by the time this phase starts. Summary of what this phase consumes:

- **`SecretStore`** (`apps/server/app/secretstore.py`): passwords live as macOS Keychain generic-password items — service `mishka-hub-letterboxd`, account = the Letterboxd username — read via `keyring.get_password(...)`; a Fernet-encrypted file backend under `data/secrets/` covers a future Windows host.
- Playwright **`storage_state` session blobs** are Fernet-encrypted at `data/secrets/letterboxd_session_<user_id>.enc`; the plaintext profile directory is wiped after each run and rehydrated from ciphertext, so passwords only touch letterboxd.com during (re)login.
- The credential endpoints (`PUT`/`DELETE /api/letterboxd/credentials`, `GET …/status`) ship in **Phase 2** ([API.md](../API.md) §Phase 2); responses never contain secrets (only `{"configured": true}`).
- The **ToS acknowledgement gate** is shared: one ack per user (`settings.letterboxd_automation_ack_user_<id>`) covers export automation *and* write-back ([PHASE-2-credentials.md §6](PHASE-2-credentials.md)).

## 4. The logging flow (selectors centralised)

All selectors live in one module (`letterboxd_write/selectors.py`) — the only file to touch when the site changes. The **login** selectors already exist by this phase (built with the Phase 2 export automation); this phase adds the log-dialog selectors to the same module.

```
ensure_session(user):        # BUILT IN PHASE 2 (export automation) — reused as-is here;
                             # see PHASE-2-letterboxd-import.md §2b
  load encrypted storage_state → context
  goto https://letterboxd.com/ ; if signed-in avatar present → done
  else login: goto /sign-in, fill form (username, password from SecretStore), submit,
       verify avatar, persist storage_state
       on captcha/2FA challenge → job fails immediately with code
       'login_challenge' (no retry — human needed; fallback shown)

log_film(job):
  1. resolve film page: films.letterboxd_slug → goto /film/<slug>/
     else search: /search/films/<title>+<year>/ → first result whose
     title+year match (same normalisation as import matching); no match →
     fail 'film_not_found'
  2. open the log dialog ("Review or log…" action on the film page)
  3. set watched date = payload.watched_date (specify-date control)
  4. set rating: payload.rating → the dialog's rating input (0.5–5)
  5. set liked: toggle heart if payload.liked
  6. rewatch checkbox if payload.rewatch
  7. review text + contains-spoilers checkbox if payload.review
  8. tags if payload.tags
  9. save; verify by reloading the film page and asserting the new
     diary entry / rating state is visible (assert-after-write)
 10. mirror into Mishka Hub tables with source='in-app' provenance kept
     (the local rows were already written when the user acted; step
     verifies Letterboxd agrees) and mark job done.
```

⚠️ The dialog's concrete DOM (field names, button text) is **deliberately not documented here** — it churns and any snapshot would rot; the selectors module is the source of truth, updated against the live site during implementation. The *steps* above are the stable contract.

## 5. Error handling & retry

| Failure | Behaviour |
|---|---|
| Transient (timeout, nav error, detached node) | retry job, max 3 attempts, backoff 30 s → 5 min → 30 min, `attempts++` |
| `login_challenge` (captcha/2FA/email verify) | no retry; job → `failed`; UI banner: "Letterboxd wants a human — use the one-click log below, then re-save credentials." |
| `film_not_found` | no retry; fallback with search URL |
| Selector not found (site change) | no retry after first failure of this class in 24 h; kill switch auto-flips off + all queued jobs go to fallback; screenshot + DOM dump to `data/playwright/failures/` |
| Success but verification step can't confirm | mark `done` with `detail='unverified'` (don't double-log); surfaced in sync state |

Jobs are never silently dropped: terminal states are `done`, `failed` (with fallback shown), and each is visible via `GET /api/letterboxd/log/{job_id}`.

## 6. One-click fallback (always available, automatic on failure)

Two flavours, both pre-filled from the job payload:

1. **iOS deep link** (the couple's phones): Letterboxd's official app x-callback scheme — verified from [Letterboxd/letterboxd-ios-x-callback-url](https://github.com/Letterboxd/letterboxd-ios-x-callback-url):

   ```
   letterboxd://x-callback-url/log?name=Jaws&date=2026-07-02&rating=4.5&like=true&rewatch=false&review=Still%20perfect.&tags=date-night
   ```

   Parameters (all optional, URL-encoded): `name` (title — app asks user to confirm the search match), `date` (YYYY-MM-DD), `rating` (0.5–5), `like`, `rewatch`, `containsSpoilers` (booleans), `review`, `tags` (comma-separated). iOS-app-only.

2. **Web link** (desktop): no pre-filled log URL exists for the website ⚠️ (verified absence — only the iOS scheme is documented), so the fallback is: open `https://letterboxd.com/film/<slug>/` in a new tab + copy the review text to the clipboard + a compact "what to enter" summary (date/★/♥) in the toast. Two clicks instead of one; acceptable.

The UI renders flavour by platform (`navigator.userAgent` iOS check), showing both when unsure. Fallback payload shape is in [API.md](../API.md).

## 7. Acceptance criteria

- [ ] Credential handling passes the shared-store criteria in [PHASE-2-credentials.md §8](PHASE-2-credentials.md) (Keychain-only passwords, no plaintext in DB/logs/API — those tests already exist from Phase 2; re-run them here).
- [ ] First job for a user performs a full login and persists an encrypted session; second job reuses the session without touching `/sign-in`. If Phase 2's export automation ran recently, the write-back job reuses **that** session (one session store per user, not per feature).
- [ ] Happy path: marking a film watched+rated+liked+reviewed in Mishka Hub produces a matching diary entry on letterboxd.com (manually verified on both accounts) and the job ends `done`.
- [ ] The RSS sync ([Phase 2](PHASE-2-letterboxd-import.md)) picks up the entry we just wrote and dedups it (no double rows) — full-circle test.
- [ ] Kill switch: with `letterboxd_writeback_enabled=false`, `POST /api/letterboxd/log` immediately returns the fallback payload, no browser launches.
- [ ] Forced selector breakage (rename a selector) → job fails ≤3 attempts, screenshot saved, kill switch auto-flips, UI shows fallback.
- [ ] iOS deep link opens the Letterboxd app with fields pre-filled on a real device; web fallback opens the film page with review on clipboard.
- [ ] ToS risk statement (§1) acknowledged once per user before first credential save — the shared `settings.letterboxd_automation_ack_user_<id>` gate ([PHASE-2-credentials.md §6](PHASE-2-credentials.md)); users who acknowledged during Phase 2 are not re-prompted.
