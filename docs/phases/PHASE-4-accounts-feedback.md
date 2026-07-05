# Phase 4 — Accounts, Auth & Feedback Loop

Purpose: replace the interim static-token guard with real two-person auth (fixed accounts, argon2, JWT access+refresh), per-user profiles, and the in-app feedback events (rate / like / seen-it / not-interested) that make the [recommender](PHASE-3-recommender.md) learn — plus active-learning prompts that ask the highest-value questions. Endpoint contracts live in [API.md](../API.md); token/session storage in [DATA_MODEL.md](../DATA_MODEL.md).

**Status: §1-3 (accounts/auth) shipped 2026-07-05.** §4 (profile screen) is a thin read on
already-shipped data, not separately tracked. §5-§6 (feedback-event model wiring,
active-learning prompts) remain ⬜ not built — rating/liked/watched already work via Phase 2/3's
endpoints, just without a `feedback_events` audit trail or prompt system yet.

Real deviations from the original spec below, both deliberate: (1) `POST /api/auth/logout`
takes the refresh token in the body rather than requiring a Bearer access token — symmetric
with `/refresh`, and it means logging out still works even if the access token already expired;
(2) the CLI is a single-purpose `apps/server/scripts/set_password.py` rather than a
multi-subcommand `app.cli`, since seeding was already done ad hoc when the two user rows were
created — there was nothing left to build a `seed-users` subcommand for.

---

## 1. Accounts: two, fixed, seeded

- **No registration endpoint exists — ever.** The API is internet-exposed via the tunnel; the absence of signup is the first line of defence.
- Seeding via CLI on the server (idempotent):

```bash
python -m app.cli seed-users \
  --user1 "mack@example.com:Mack:mack_lb" \
  --user2 "partner@example.com:Partner:partner_lb"
# prompts for each password interactively (never in argv/history),
# writes users rows with argon2id hashes; re-running updates display
# names/letterboxd usernames but refuses to overwrite a password
# without --reset-password.
```

- Password changes: `python -m app.cli set-password <email>` (CLI-only; no HTTP endpoint for credential changes — an attacker with a stolen token still can't lock the couple out).

## 2. Password hashing: argon2id

**Choice: `argon2-cffi` (argon2id)** over bcrypt:
- argon2id is the current OWASP first recommendation; memory-hard (GPU-resistant).
- bcrypt silently truncates passwords at 72 bytes and commonly arrives via `passlib`, whose maintenance has stalled (no release since 2020; broken with newer `bcrypt` package versions).
- Parameters: `time_cost=3, memory_cost=65536 (64 MiB), parallelism=4` (library defaults as of 2026) — login happens a few times a month on a Mac; there is zero pressure to weaken them. `PasswordHasher.check_needs_rehash` on every successful login handles future parameter bumps automatically.

## 3. Tokens: JWT access + rotating refresh

| Token | Format | Lifetime | Storage (SPA) | Revocable |
|---|---|---|---|---|
| Access | JWT HS256, claims `sub` (user id), `exp`, `iat`, `jti` | 15 min | memory (module variable) | no (short-lived by design) |
| Refresh | 64-byte opaque random (not a JWT) | 30 days, **rotated on every use** | `localStorage` | yes — `refresh_tokens` table stores sha256, revoked flag |

- Signing secret: `MISHKA_JWT_SECRET` (32+ random bytes) in `apps/server/.env`; rotating it logs everyone out — acceptable.
- Refresh rotation: `/api/auth/refresh` marks the presented token revoked and issues a new pair. A revoked-token replay ⇒ revoke *all* the user's refresh tokens (classic reuse-detection — stolen-token tripwire).
- Why header Bearer instead of httpOnly cookies: the SPA (github.io) and API (tunnel hostname) are cross-site; `SameSite=None` cookie auth is fragile across Safari/ITP and adds CSRF surface. localStorage XSS risk is accepted for a 2-user app with no third-party scripts. Documented trade-off ([ARCHITECTURE.md](../ARCHITECTURE.md) §3).
- FastAPI wiring: `Depends(current_user)` on the two routers' `dependencies=[...]` — global-by-default; `/api/health`, `/api/auth/login`, `/api/auth/refresh` are the only opt-outs. The Phase 2–3 interim static token dependency is deleted in the same PR.
- Login rate-limit: in-process sliding window, 5 failures / 15 min per IP (`X-Forwarded-For` from cloudflared is trustworthy here since uvicorn only listens on loopback) ⇒ 429 with `Retry-After`. Failures logged.
- New deps: `argon2-cffi`, `pyjwt` (small, maintained; not `python-jose`, which has stalled).

## 4. Per-user profiles

`GET /api/auth/me` + a profile screen: display name, Letterboxd username (drives RSS sync + slug links), stats strip (films seen, mean rating, likes), model card ("your taste model: 812 ratings, retrained 02:00, top signals: …" — from `model_artifacts.metrics_json` + Ridge coefficients, see [PHASE-3 §4](PHASE-3-recommender.md)). The SPA keeps the selected identity implicit — you are who you logged in as; the only cross-user surface is the shared poster wall and `together` recs.

## 5. Feedback events → model updates

All in-app signals append to `feedback_events` (see [DATA_MODEL.md](../DATA_MODEL.md)) *and* update their state table where one exists:

| UI action | event_type | value | State change | Model effect (next retrain) |
|---|---|---|---|---|
| Rate a film (0.5–5★) | `rating` | the rating | upsert `ratings` (source `in-app`) | strongest signal: enters Ridge training set + prototype weights |
| Un-rate | `rating` | NULL | delete `ratings` row | removed from training set (event history keeps the retraction) |
| Like / unlike | `like` | 1 / 0 | upsert/delete `likes` | +0.75 prototype weight (like) |
| Seen it (with optional date) | `seen` | 1 | insert `watches` (source `in-app`) | removed from candidate pool immediately (no retrain needed) |
| Not interested | `not_interested` | 1 | — | hard-excluded from pool 90 days; mild negative prototype weight (0.3× a 1★) |
| Snooze ("not tonight") | `snooze` | 1 | — | excluded 14 days, **no** taste effect |
| Open a rec's detail | `clicked` | 1 | — | no direct training effect; logged for the online metrics ([PHASE-3 §8](PHASE-3-recommender.md)) |
| Add to watchlist | `watchlisted` | 1 | — | +0.25 prototype weight; boosts re-surfacing priority |

Retrain trigger: ≥5 events since active model version ⇒ debounced retrain within 10 min (defined in PHASE-3 §6). `seen`/`snooze`/`not_interested` also take effect *immediately* via pool filtering — no model wait.

**UI placement:** every poster detail drawer carries the full row (★ slider, heart, seen-it, not-interested); rec cards additionally get quick actions on long-press (mobile radial per [DESIGN.md](../DESIGN.md) §3c) / hover (desktop).

## 6. Active learning: targeted rating prompts

Goal: maximum model improvement per question asked. Keep it *polite*: max one prompt card per app-open, max 5 films per prompt, dismissible forever per film.

**Candidate question pool** (films the user hasn't rated), scored by expected information gain, approximated cheaply:

```
info(i) = 0.5 · uncertainty(i)      # |ridge ŷ(i)| near 0 (taste model can't decide)
        + 0.3 · coverage(i)         # distance to nearest already-rated film in
                                    #   feature space (unexplored taste regions)
        + 0.2 · leverage(i)         # popularity prior × pool influence: how many
                                    #   current candidates are similar to i
eligible(i): watched-by-user films first (rate what you've seen — cheap recall),
             else well-known films (vote_count high) the user *might* have seen
```

Two prompt types v1:
1. **"Rate these 5"** — top-5 by `info(i)` among films the user has watched but never rated (imports create many of these).
2. **"Have you seen…?"** — high-leverage popular films absent from history; answers `not_seen` (removes from prompt pool, keeps in rec pool) or a rating (jackpot).

Pairwise "A or B?" prompts are deferred (needs a preference-learning head; noted as v2).

Answers post to `/api/prompts/answer`, land as `prompt_answer`+`rating` events with `context='prompt'`, and count toward the retrain debounce. Prompt selection recomputes at each retrain and persists in `settings` (`active_prompts` key) so the same questions aren't regenerated per request.

## 7. Acceptance criteria

- [x] No HTTP path creates users (the two rows already existed from Phase 2 import seeding);
      `scripts/set_password.py` is the only way a password is ever set, and only for an
      already-existing email — verified live against both real accounts.
- [x] Login returns working token pair; wrong password → 401; 6th rapid failure → 429 — all
      verified live via curl (real 401/429 responses captured).
- [x] Access token expires in ~15 min and the SPA silently refreshes (verified: a page reload
      re-derives a session from the stored refresh token with no re-login); refresh rotation
      works; replaying an already-rotated refresh token returns `refresh_reuse_detected` and
      revokes every refresh token that user holds — verified live.
- [x] Every endpoint except `/api/health` and `/api/auth/(login|refresh|logout)` requires a
      valid Bearer JWT — verified live (old static dev token now correctly rejected).
- [x] Interim static-token guard removed — `MISHKA_DEV_TOKEN`/`dev_token` deleted from
      `config.py`/`.env`/`auth.py` entirely, not just superseded.
- [ ] Rating / like / seen / not-interested from the UI create correct `feedback_events` + state rows; not-interested films vanish from recs immediately; a new 5★ shifts recs after the debounced retrain.
- [ ] Prompt card appears at most once per app-open, questions come from the info-gain pool, `not_seen` answers stop that film re-prompting.
- [ ] Profile screen shows per-user stats + model card with human-readable top taste signals.
