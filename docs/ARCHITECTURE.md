# Mishka Hub — System Architecture

This document is the durable reference for how Mishka Hub's pieces fit together: components, data flows, the auth model, network topology (Cloudflare Tunnel + CORS), failure modes, and the legal/attribution obligations that constrain the UI. Every other doc in this suite assumes the decisions recorded here. For the phased delivery order see [PLAN.md](PLAN.md); for storage see [DATA_MODEL.md](DATA_MODEL.md); for the HTTP surface see [API.md](API.md).

**Status: planned** (Phase 1 scaffold is built; everything else here is the target design.)

---

## 1. System overview

Mishka Hub is a private, two-user (one household) movie recommender:

| Component | Technology | Runs on | Role |
|---|---|---|---|
| Web app | React 19 + Vite 8 + Tailwind 4, static build | GitHub Pages | UI: poster wall, recommendations, feedback, settings |
| API server | FastAPI, Python 3.12, uvicorn | Home Mac (later possibly a Windows desktop) | Auth, storage, TMDB proxy, Letterboxd sync, ML |
| Storage | SQLite (single file) + on-disk model artefacts | Home Mac | All persistent state |
| ML | scikit-learn (classical only — no generative AI) | Home Mac | Content-based → hybrid recommender, active learning |
| Tunnel | Cloudflare Tunnel (named tunnel) | Home Mac | Stable public HTTPS hostname for the API |
| Media server (Phase 7) | Jellyfin | Home Mac / Windows desktop | Serves owned files to the LG webOS TV |

External services:

| Service | Used for | Auth | Constraint |
|---|---|---|---|
| TMDB API v3 | Metadata, posters, GB watch providers, discovery | Free API key / v4 read token | ~50 req/s hard cap, 20 connections/IP ([docs](https://developer.themoviedb.org/docs/rate-limiting)); attribution required |
| JustWatch (via TMDB `/watch/providers`) | GB streaming availability | — (bundled in TMDB) | **Must attribute JustWatch** wherever shown ([TMDB docs](https://developer.themoviedb.org/reference/movie-watch-providers)) |
| Letterboxd (read) | Watch history | none — CSV export ZIP + public RSS | RSS limited to last 50 diary entries; official API unavailable to us |
| Letterboxd (write) | Auto-logging watches | Users' own credentials via Playwright | **Against Letterboxd ToS for automation** — accepted, locked decision; see [PHASE-5](phases/PHASE-5-letterboxd-writeback.md) |

## 2. Component & data-flow diagram

```
                       Internet (HTTPS everywhere)
  ┌─────────────────────────┐
  │  GitHub Pages           │      fetch() + Bearer JWT
  │  https://<user>.github.io/mishka-hub/                                │
  │  React SPA (static)     │───────────────────────────────┐
  └─────────────────────────┘                               │
                                                            ▼
                                          ┌────────────────────────────────┐
                                          │  Cloudflare edge               │
                                          │  https://mishka-api.<domain>   │
                                          └───────────────┬────────────────┘
                                                          │ outbound-only tunnel
                                                          │ (cloudflared, QUIC)
 ┌────────────────────────── Home Mac ─────────────────────▼──────────────────────┐
 │                                                                                │
 │   cloudflared ──► uvicorn :8000 (bound to 127.0.0.1)                           │
 │                     │                                                          │
 │                     ▼                                                          │
 │   FastAPI app ── auth middleware (JWT) ── routers                              │
 │        │            │            │              │                              │
 │        ▼            ▼            ▼              ▼                              │
 │   SQLite DB    TMDB client   Letterboxd     Recommender                        │
 │   data/         (httpx)      importer/RSS   (scikit-learn,                     │
 │   mishka.db                  + Playwright    artefacts in data/models/)        │
 │                                writeback                                       │
 └───────┬──────────────┬──────────────┬─────────────────────────────────────────┘
         │              │              │
         ▼              ▼              ▼
   nightly backup   api.themoviedb.org   letterboxd.com
   (sqlite .backup)  image.tmdb.org       (RSS read / Playwright write)
```

### Flow A — Letterboxd history in (Phase 2)

```
CSV ZIP (user download) ──upload──► POST /api/import/letterboxd
                                        │ parse CSVs (header-driven)
                                        ▼
RSS poll (every 6 h) ────────────► match to TMDB id ──► films / watches /
  letterboxd.com/<user>/rss/        (RSS carries         ratings / likes /
  (tmdb:movieId included!)           tmdb:movieId)        reviews tables
                                        │
                              unmatched ▼
                              unmatched_imports queue ──► manual resolve UI
```

### Flow B — Recommendations out (Phase 3)

```
subscriptions (household) ─┐
                           ▼
        TMDB /discover/movie?with_watch_providers=8|337|…&watch_region=GB
                           │  candidates (not seen)
                           ▼
        feature vectors (genres/keywords/cast/…)  ×  per-user taste model
                           │  score, novelty, availability boost
                           ▼
        MMR diversification ──► GET /api/recommendations ──► UI cards
                           ▲
        feedback events (rate/like/seen/not-interested) retrain nightly
```

### Flow C — Log back to Letterboxd (Phase 5)

```
UI "Mark watched" ──► POST /api/letterboxd/log ──► job queue
                                                      │ Playwright (own creds,
                                                      ▼  encrypted at rest)
                                              letterboxd.com log form
                                                      │ on repeated failure
                                                      ▼
                                    fallback payload returned to UI:
                                    iOS deep link letterboxd://x-callback-url/log?…
                                    + film page URL + review text to clipboard
```

## 3. Auth model

Full detail in [PHASE-4-accounts-feedback.md](phases/PHASE-4-accounts-feedback.md). Summary:

- **Two fixed accounts**, seeded by CLI on the server. No registration endpoint exists.
- Passwords hashed with **argon2id** (`argon2-cffi`).
- **JWT access token** (15 min, HS256) + **refresh token** (30 days, rotated on use, revocable server-side).
- Sent as `Authorization: Bearer <token>` header. Tokens are *not* cookies: the SPA origin (`github.io`) and API origin (tunnel hostname) are cross-site, and header-based auth avoids cross-site cookie fragility. Trade-off (XSS exposure of localStorage-held refresh token) is documented and accepted for a 2-user private app.
- **Every endpoint except `GET /api/health` requires a valid access token.** This is enforced by a router-level dependency, not per-endpoint opt-in, so new endpoints are secure by default.
- Login endpoint is rate-limited (5 attempts / 15 min / IP) since the tunnel exposes it to the internet.
- Optional hardening layer (documented, not required): Cloudflare Access in front of the hostname.

## 4. Network topology: tunnel + CORS

- uvicorn binds to `127.0.0.1:8000` only — nothing on the home LAN or WAN can reach it directly; the **only** ingress is cloudflared's outbound connection to Cloudflare's edge. No router port-forwarding.
- A **named tunnel** with a stable hostname (e.g. `mishka-api.example.com`) fronts the API. Setup steps: [DEPLOYMENT.md](DEPLOYMENT.md).
- CORS allowlist (`MISHKA_CORS_ORIGINS`) contains exactly:
  - `http://localhost:5173`, `http://127.0.0.1:5173` (dev)
  - `https://<user>.github.io` (production SPA)
- `allow_credentials` stays `true` but auth is header-based; preflight (`OPTIONS`) must be answered without auth — FastAPI's CORSMiddleware runs before the auth dependency, which gives this for free.
- The webOS TV (Phase 7) reaches the API through the same public hostname — one URL everywhere.

## 5. Failure modes & degradation

| Failure | Detection | Behaviour |
|---|---|---|
| Home server offline / tunnel down | `GET /api/health` fails (SPA pings on load + every 60 s) | Status pill shows "Server offline" (already built). SPA shows cached last-known recommendations & poster wall from `localStorage` (read-only snapshot with "as of <time>" banner). All mutating actions disabled. |
| TMDB down / rate-limited (429) | httpx error / status in TMDB client | Client retries with exponential backoff (respect `Retry-After`); serves stale `availability` cache rows past TTL rather than erroring; UI marks availability "stale". |
| TMDB key missing | `tmdb_configured=false` in health payload | Amber status pill (already built); import & recs endpoints return `503` with actionable detail. |
| Letterboxd RSS unreachable | sync job error | Logged in `sync_state.status`; retried next cycle; UI settings page shows last successful sync time. |
| Playwright write-back breaks (site redesign, captcha) | job failure after retries | Job marked `failed`; UI offers the one-click fallback (deep link / pre-filled page). See [PHASE-5](phases/PHASE-5-letterboxd-writeback.md). |
| SQLite corruption / disk loss | nightly backup job errors, or restore needed | Restore from rotated `.backup` copies; procedure in [DEPLOYMENT.md](DEPLOYMENT.md). |
| JWT secret rotated / tokens invalid | 401 on any call | SPA drops tokens, returns to login screen. |

## 6. Attribution & legal obligations (UI requirements)

These are **hard requirements** on every screen that shows the relevant data:

1. **TMDB**: display the TMDB logo/notice: "This product uses the TMDB API but is not endorsed or certified by TMDB." (Footer, plus About page.) Source: [TMDB API terms](https://www.themoviedb.org/api-terms-of-use).
2. **JustWatch**: watch-provider data "must attribute the source of the data as JustWatch … If we find any usage not complying with these terms we will revoke access" — quoted from [TMDB watch-providers docs](https://developer.themoviedb.org/reference/movie-watch-providers). Mishka Hub shows "Streaming availability by JustWatch" adjacent to any provider logos/lists (the scaffold footer already carries this; per-card popovers must too).
3. **No deep links**: TMDB's provider payload deliberately excludes direct deep links; link out to the film's TMDB watch page instead (same source).
4. **Letterboxd**: read paths (CSV/RSS) are user-initiated exports of the users' own data. The Playwright write path is against Letterboxd's ToS on automation — this risk statement lives in [PHASE-5](phases/PHASE-5-letterboxd-writeback.md) and is accepted as a locked decision. Mishka Hub is private, non-commercial, and never redistributes Letterboxd data.

## 7. Repository layout (target)

```
mishka-hub/
├── scripts/dev.sh            # run backend + web together (exists)
├── docs/                     # this documentation suite
│   └── phases/
├── apps/
│   ├── server/
│   │   ├── app/
│   │   │   ├── main.py           # app factory (exists)
│   │   │   ├── config.py         # pydantic-settings (exists)
│   │   │   ├── db.py             # engine/session + migration hook   (Phase 2)
│   │   │   ├── models.py         # ORM models mirroring DATA_MODEL   (Phase 2)
│   │   │   ├── auth.py           # JWT + password hashing            (Phase 4)
│   │   │   ├── cli.py            # user seeding, retrain, backup     (Phase 2+)
│   │   │   ├── clients/tmdb.py   # (exists)
│   │   │   ├── importers/        # letterboxd CSV + RSS              (Phase 2)
│   │   │   ├── recommender/      # features, models, ranking         (Phase 3)
│   │   │   ├── letterboxd_write/ # Playwright automation             (Phase 5)
│   │   │   └── routers/          # one module per API group
│   │   ├── migrations/           # alembic                           (Phase 2)
│   │   └── requirements.txt
│   └── web/                      # React SPA (exists)
├── config/                   # household.yaml (users, services, region)
├── data/                     # gitignored: mishka.db, letterboxd/ exports, models/, playwright/
├── reference/                # reference materials (Letterboxd export ZIPs)
└── backups/                  # backup archives
```

## 8. Cross-references

- Storage schema: [DATA_MODEL.md](DATA_MODEL.md)
- Endpoint catalogue: [API.md](API.md)
- Design system (visual language, tokens, poster-grid & drag physics): [DESIGN.md](DESIGN.md)
- Ops (Pages, tunnel, launchd, backups): [DEPLOYMENT.md](DEPLOYMENT.md)
- Phase docs: [2 import](phases/PHASE-2-letterboxd-import.md) · [3 recommender](phases/PHASE-3-recommender.md) · [4 accounts](phases/PHASE-4-accounts-feedback.md) · [5 write-back](phases/PHASE-5-letterboxd-writeback.md) · [6 service optimisation](phases/PHASE-6-service-optimisation.md) · [7 local media → TV](phases/PHASE-7-local-media-tv.md) · [8 coming soon](phases/PHASE-8-coming-soon.md)
