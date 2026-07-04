# Mishka Hub — REST API Reference

Every planned endpoint, grouped by build phase: method, path, auth, request/response examples, and error shapes. This is the contract between the React SPA and the FastAPI server; implement routers against this doc and update it in the same PR as any change. Base URL is the tunnel hostname (prod) or `http://127.0.0.1:8000` (dev). See [ARCHITECTURE.md](ARCHITECTURE.md) §3 for the auth model and [DATA_MODEL.md](DATA_MODEL.md) for the storage each endpoint touches.

**Status: planned** (only the two Phase-1 endpoints exist today.)

---

## 0. Conventions

- All bodies are JSON (`Content-Type: application/json`) except the ZIP upload (multipart).
- **Auth: every endpoint requires `Authorization: Bearer <access_token>` except `GET /api/health` and the two `/api/auth/*` token endpoints.** Enforced globally; the tables below only flag the exceptions.
- Timestamps UTC ISO-8601; dates `YYYY-MM-DD`; ratings 0.5–5.0 step 0.5.
- Pagination: `?limit=` (default 50, max 200) & `?offset=`; responses carry `total`.

### Error shape (uniform)

FastAPI-style `detail`, extended with a machine code:

```json
{ "detail": "Film 4951 has no availability data yet", "code": "availability_pending" }
```

| HTTP | Meaning here |
|---|---|
| 401 | Missing/expired/invalid token → SPA refreshes or re-logins |
| 403 | Valid token, forbidden action (e.g. acting as the other user) |
| 404 | Unknown resource id |
| 409 | Conflict (duplicate import job, already-resolved unmatched row) |
| 422 | Validation error (FastAPI default body) |
| 429 | Login rate-limit |
| 502 | Upstream (TMDB/Letterboxd) failed |
| 503 | Feature unconfigured (no TMDB key, no Letterboxd creds) |

---

## Phase 1 — exists today

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /api/health` | **none** | liveness + config probe |
| `GET /api/tmdb/search?q=` | none today → **Bearer from Phase 4** | TMDB title search passthrough |

```json
// GET /api/health → 200
{ "status": "ok", "environment": "development", "region": "GB", "tmdb_configured": true }

// GET /api/tmdb/search?q=heat → 200
{ "query": "heat", "count": 2, "results": [
  { "id": 949, "title": "Heat", "year": "1995", "overview": "…",
    "poster": "https://image.tmdb.org/t/p/w500/….jpg", "vote_average": 7.9 } ] }
```

---

## Phase 4 — Auth (documented first: everything else depends on it)

| Method & path | Auth | Purpose |
|---|---|---|
| `POST /api/auth/login` | none (rate-limited 5/15 min/IP) | password → token pair |
| `POST /api/auth/refresh` | none (refresh token in body) | rotate tokens |
| `POST /api/auth/logout` | Bearer | revoke the presented refresh token |
| `GET  /api/auth/me` | Bearer | current user profile |

```json
// POST /api/auth/login
{ "email": "mack@example.com", "password": "…" }
// → 200
{ "access_token": "eyJ…", "token_type": "bearer", "expires_in": 900,
  "refresh_token": "9f2c…64-char-opaque…",
  "user": { "id": 1, "email": "mack@example.com", "display_name": "Mack",
            "letterboxd_username": "mack" } }
// → 401 {"detail":"Invalid email or password","code":"bad_credentials"}
// → 429 {"detail":"Too many attempts; try again in 12 minutes","code":"rate_limited"}

// POST /api/auth/refresh
{ "refresh_token": "9f2c…" }
// → 200 same shape as login (refresh token is rotated; old one is revoked)
```

---

## Phase 2 — Films & Letterboxd import

### Films

| Method & path | Purpose |
|---|---|
| `GET /api/films` | poster-wall listing with filters |
| `GET /api/films/{tmdb_id}` | full detail (metadata + both users' state); auto-hydrates from TMDB on first access if not yet in our library |
| `GET /api/films/{tmdb_id}/availability` | GB **streaming** availability (flatrate/free/ads only — rent/buy always excluded), cached (TTL 7 d). `?subscribed_only=` (bool, default `true`) — when true, `offers` is filtered to only providers in the household's active `subscriptions`; when false, all streaming-kind offers are returned (rent/buy still excluded), each carrying a `subscribed` flag either way. |
| `GET /api/films/{tmdb_id}/similar` | **shipped ahead of schedule as "recommender v0"** — pure content-similarity (cosine, PHASE-3-recommender.md §1-2), not yet personalised. `?limit=&max_runtime=&vibe=` (vibe ∈ slow_burn/feel_good/sad/tense/dark/quick_watch). Auto-hydrates the seed film too. Comparison corpus is filtered to films eligible for at least one of the two users (same staleness rule as `/lucky` — not-seen, or not seen in ≥365 days); the seed film itself is always kept as the comparison anchor even if already watched (2026-07-04 fix — see PHASE-3-recommender.md's "corpus expansion" status note). See [PHASE-3 §0](phases/PHASE-3-recommender.md). |
| `GET /api/films/{tmdb_id}/rematch/search` | **shipped** — "Wrong film?" UI support. `?query=` (defaults to the current film's title), proxies a TMDB search for candidates to re-point a wrongly-matched film to. |
| `POST /api/films/{tmdb_id}/rematch` | **shipped** — body `{ "correct_tmdb_id": <int> }`. Moves every watch/rating/like/review off `{tmdb_id}` onto `correct_tmdb_id`, hydrating the destination from TMDB first if we don't have it yet. 400 `noop_rematch` if `correct_tmdb_id` equals the current id. Used for real once already (a misresolved Letterboxd diary entry). See `_rematch_film`'s docstring in `films.py` for the full collision/merge policy. |

```json
// GET /api/films?user=1&rated=true&year_from=1990&sort=watched_desc&limit=2
{ "total": 812, "items": [
  { "id": 949, "title": "Heat", "year": 1995,
    "poster": "https://image.tmdb.org/t/p/w500/….jpg",
    "my": { "rating": 4.5, "liked": true, "watch_count": 2, "last_watched": "2026-05-01" },
    "partner": { "rating": null, "liked": false, "watch_count": 0, "last_watched": null } } ] }

// GET /api/films/949/availability  (subscribed_only defaults to true)
{ "film_id": 949, "region": "GB", "fetched_at": "2026-07-03T02:11:00Z",
  "attribution": "Streaming availability by JustWatch",
  "offers": [ { "provider_id": 8, "provider_name": "Netflix", "kind": "flatrate",
                "logo": "https://image.tmdb.org/t/p/w92/….jpg", "subscribed": true } ],
  "tmdb_watch_page": "https://www.themoviedb.org/movie/949/watch?locale=GB" }

// GET /api/films/949/availability?subscribed_only=false — same shape, but includes
// non-subscribed streaming (flatrate/free/ads) offers too, e.g. a `"subscribed": false`
// Disney Plus flatrate row; rent/buy kinds are still never included in either case.
```

Query params for `GET /api/films`: `user` (1|2), `seen` (bool), `rated` (bool), `liked` (bool), `year_from`/`year_to`, `genre`, `q` (title substring), `sort` (`watched_desc|rating_desc|title|year`), `limit`/`offset`.

### Import & sync (the cascade — [PHASE-2 §1](phases/PHASE-2-letterboxd-import.md))

| Method & path | Purpose |
|---|---|
| `POST /api/import/letterboxd/run` | trigger an import run: `{ "user": 1, "source": "auto" }` (`auto` walks the cascade export→scrape; `export`/`scrape`/`rss` force one source) → job |
| `POST /api/import/letterboxd` | multipart ZIP upload (field `file`, query `user=1|2`) → job (manual export variant) |
| `GET  /api/import/jobs/{job_id}` | progress/result of an import run (`job_id` = `imp_<import_runs.id>`) |
| `GET  /api/import/runs?user=1&limit=20` | run history (from [`import_runs`](DATA_MODEL.md)) |
| `GET  /api/import/unmatched?status=pending` | manual-resolution queue |
| `POST /api/import/unmatched/{id}/resolve` | `{ "tmdb_id": 949 }` or `{ "action": "ignore" }` |
| `POST /api/sync/rss/run` | trigger RSS poll now (both users) |
| `GET  /api/sync/state` | cursors + last-run status of all background jobs |

```json
// POST /api/import/letterboxd/run
{ "user": 1, "source": "auto" }
// → 202 { "job_id": "imp_18", "user_id": 1, "source_requested": "auto", "status": "running" }
// → 409 {"detail":"An import for this user is already running","code":"duplicate_job"}
// → 503 {"detail":"No source available: no credentials and scrape disabled","code":"no_source_available"}

// GET /api/import/jobs/imp_18 → 200 (mid-run, cascade fell through to scrape)
{ "job_id": "imp_18", "user_id": 1, "source_requested": "auto", "source_used": "scrape",
  "status": "running", "stage": "scraping:diary:p4",
  "cascade": [
    { "source": "export", "outcome": "failed", "code": "login_challenge" },
    { "source": "scrape", "outcome": "running", "code": null } ],
  "counts": null }

// GET /api/import/jobs/imp_18 → 200 (finished)
{ "job_id": "imp_18", "user_id": 1, "source_requested": "auto", "source_used": "export",
  "status": "done", "stage": null,
  "cascade": [ { "source": "export", "outcome": "ok", "code": null } ],
  "counts": { "watched": 743, "diary": 512, "ratings": 690, "likes": 154, "reviews": 61,
              "matched": 731, "unmatched": 12, "skipped_duplicates": 508 } }
// status 'done_unchanged' ⇒ export SHA-256 matched the last successful run; counts all zero.

// POST /api/import/letterboxd?user=1  (multipart zip) → 202
{ "job_id": "imp_19", "user_id": 1, "source_requested": "export-upload", "status": "running" }

// POST /api/import/unmatched/17/resolve
{ "tmdb_id": 424694 }
// → 200 { "id": 17, "status": "matched", "film_id": 424694 }
// → 409 {"detail":"Row already resolved","code":"already_resolved"}
```

### Letterboxd credentials (shared store — [PHASE-2-credentials.md](phases/PHASE-2-credentials.md); also consumed by Phase 5)

| Method & path | Purpose |
|---|---|
| `GET    /api/letterboxd/credentials/status` | is the signed-in user's credential configured? (never returns the secret) |
| `PUT    /api/letterboxd/credentials` | store my Letterboxd password in the OS keychain; first save requires the ToS acknowledgement flag |
| `DELETE /api/letterboxd/credentials` | remove the keychain item + encrypted session blob (cascade then skips source 1) |

Each user can only manage their **own** credential (403 otherwise — same rule as all per-user actions).

```json
// GET /api/letterboxd/credentials/status → 200
{ "configured": true, "tos_acknowledged": true, "backend": "keychain" }

// PUT /api/letterboxd/credentials
{ "password": "…", "acknowledge_tos": true }
// → 200 { "configured": true }
// → 403 {"detail":"Automation risk must be acknowledged first","code":"tos_not_acknowledged"}
//   (username is not in the body — it's the signed-in user's users.letterboxd_username)

// DELETE /api/letterboxd/credentials → 200
{ "configured": false }
```

---

## Phase 3 — Recommendations

**Status: `/similar` and `/lucky` shipped (v0/v0.5). `/recommendations`, `/why`, `/model/*` SHIPPED as recommender v1 (2026-07-04) — real candidate pool from TMDB discover, per-user Ridge/prototype taste models, MMR, eligibility rule (task #18). Computed synchronously per request; retrain is synchronous (not async job) at this corpus size. Scope-downs (§3 depth, §6 versioning/scheduler, §8 eval harness) documented in PHASE-3-recommender.md's status block.**

| Method & path | Purpose |
|---|---|
| `GET /api/films/lucky` | **shipped** — "Feeling Lucky": weighted-random pick of ONE eligible film. See below. |
| `GET /api/recommendations` | **shipped (v1)** — ranked recs for a profile, availability-filtered, eligibility-respecting |
| `GET /api/recommendations/{film_id}/why` | **shipped (v1)** — score component breakdown + weights for a film in the profile |
| `POST /api/model/retrain` | **shipped (v1)** — candidate-gen + refit taste models (synchronous); `?skip_candidates=true` to refit only |
| `GET  /api/model/status` | **shipped (v1)** — active model version + real counts (eval metrics deferred, §8) |

### `GET /api/films/lucky` (shipped — homepage "Feeling Lucky")

Picks ONE film via weighted-random selection from the local library (same corpus as `/similar` — recommender v0's content-similarity pool, not yet the full TMDB-discover candidate pool from §3). Eligibility and weighting (per user, since "haven't watched" is per-person):

- **Eligible**: never watched by this user, OR last watched ≥365 days ago. Watched <365 days ago is hard-excluded (never resurfaced as a "new" suggestion).
- **Edge case**: if this user's watch history for a film has no dated watch at all (every row's `watched_date IS NULL` — an undated "watched at some point" import record, common in the real Letterboxd backfill), staleness can't be computed, so that film is treated as **not eligible** (neither never-watched nor stale-rewatch) rather than guessed. Once any dated watch exists it's evaluated normally against that date.
- **Weight**: never-watched = `1.0`. Watched ≥365 days ago ramps from `0.3` at exactly 365 days up to `1.0` at 1825+ days (5 years), linear in between — i.e. **the longer it's been, the more likely**, approaching (never exceeding) a never-watched film's weight. Formula: `weight = min(1.0, 0.3 + 0.7 * min(days_since - 365, 1460) / 1460)`.
- One film is drawn via weighted-random sampling from the eligible pool (after `genre`/`max_runtime`/`vibe` filters narrow it), so repeat presses give variety rather than always the single top-weighted film.

Query params: `user` (1|2, required — eligibility is per-person), `genre`, `max_runtime`, `vibe` (same enum as `/similar`).

```json
// GET /api/films/lucky?user=1&max_runtime=100&vibe=feel_good
{ "film": { "id": 578, "title": "Jaws", "year": 1975, "poster": "…", "runtime_min": 124 },
  "eligibility": "never_watched" | "stale_rewatch",
  "days_since_last_watched": 812,     // null if never_watched
  "weight": 0.72,
  "pool_size": 214 }                  // how many films were eligible after filters, for transparency
// 503 {"detail":"No eligible films match these filters","code":"lucky_pool_empty"}
```

### Full personalised system (shipped — PHASE-3-recommender.md §3-8)

Query params for `GET /api/recommendations`:
`profile` (`me` | `partner` | `together`, default `me`), `providers` (CSV of TMDB provider ids to *narrow* below the household set, e.g. `8,337`), `include_unavailable` (bool, default false), `novelty` (0–1, weight override), `genres` (CSV of genre names, AND-matched — a film must match every listed genre, not just one; matched against the film's actual parsed `genres` array from TMDB metadata, case-insensitive exact match per genre — e.g. `?genres=Comedy,Horror` only returns films whose genre list contains both. **Not** a substring match against the raw metadata blob — that was a real bug (fixed 2026-07-04) that let e.g. `?genres=Animation` match non-animated films whose *overview text* happened to contain the word "animation"), `runtime_buckets` (CSV of `under95`/`95to120`/`121to180`/`over180`, OR-matched — a film with no known runtime matches none of them), `vibe` (single value, same enum as `/similar`: `slow_burn`/`feel_good`/`sad`/`tense`/`dark`/`quick_watch`; 422 `invalid_vibe` on an unknown value), `limit`/`offset`.

Note: `GET /api/films` (the Cat-alogue's own genre filter) still uses the older substring-match
pattern (`Film.metadata_json.ilike(f"%{genre}%")`) — left as-is since the 2026-07-04 bug report
was specifically about recommendations, not the Cat-alogue.

```json
// GET /api/recommendations?profile=together&limit=2&genres=Comedy,Horror&runtime_buckets=under95,over180&vibe=feel_good
{ "profile": "together", "model_version": "2026-07-03T02:00Z",
  "generated_at": "2026-07-03T02:05:11Z",
  "attribution": "Streaming availability by JustWatch",
  "items": [
    { "film": { "id": 578, "title": "Jaws", "year": 1975, "poster": "…", "runtime_min": 124 },
      "score": 0.87,
      "providers": [ { "provider_id": 8, "provider_name": "Netflix", "kind": "flatrate" } ],
      "why": { "content_similarity": 0.71, "quality_prior": 0.09,
               "novelty": 0.05, "availability_boost": 0.02,
               "together": { "user_1": 0.91, "user_2": 0.83, "blend": "0.7*min+0.3*mean" } } } ] }
// 503 {"detail":"Model not trained yet — import history first","code":"model_missing"}
```

---

## Phase 4 — Feedback & active learning

| Method & path | Purpose |
|---|---|
| `PUT    /api/films/{id}/rating` | `{ "rating": 4.5 }` upsert (also logs feedback event) |
| `DELETE /api/films/{id}/rating` | remove rating |
| `PUT    /api/films/{id}/like` | `{ "liked": true }` |
| `POST   /api/films/{id}/seen` | `{ "watched_date": "2026-07-02", "rewatch": false }` → creates watch |
| `POST   /api/feedback` | generic event: `{ "film_id": 578, "event_type": "not_interested", "context": "rec" }` |
| `GET    /api/prompts/next` | next active-learning question(s) for me |
| `POST   /api/prompts/answer` | answer a prompt |

```json
// GET /api/prompts/next → 200
{ "prompts": [
  { "id": "p_2026w27_1", "type": "rate_these",
    "reason": "high_model_uncertainty",
    "films": [ { "id": 27205, "title": "Inception", "year": 2010, "poster": "…" } ] } ] }

// POST /api/prompts/answer
{ "prompt_id": "p_2026w27_1", "film_id": 27205, "response": "rating", "value": 4.0 }
// or  { "prompt_id": "…", "film_id": 27205, "response": "not_seen" }
```

---

## Phase 5 — Letterboxd write-back

Credential endpoints already exist from Phase 2 (see §Phase 2 — Letterboxd credentials; shared store per [PHASE-2-credentials.md](phases/PHASE-2-credentials.md)). Phase 5 adds only the logging queue:

| Method & path | Purpose |
|---|---|
| `POST /api/letterboxd/log` | queue an auto-log job (Playwright) |
| `GET  /api/letterboxd/log/{job_id}` | job status; carries fallback links on failure |

```json
// POST /api/letterboxd/log
{ "film_id": 578, "watched_date": "2026-07-02", "rating": 4.5,
  "liked": true, "rewatch": false, "tags": ["date-night"],
  "review": "Still perfect.", "contains_spoilers": false }
// → 202 { "job_id": "log_31ab", "status": "queued" }

// GET /api/letterboxd/log/log_31ab → 200 (after failure path)
{ "job_id": "log_31ab", "status": "failed", "attempts": 3,
  "error": "Log dialog selector not found (site change?)",
  "fallback": {
    "ios_deeplink": "letterboxd://x-callback-url/log?name=Jaws&date=2026-07-02&rating=4.5&like=true",
    "web_url": "https://letterboxd.com/film/jaws/",
    "clipboard_review": "Still perfect." } }
```

---

## Phase 2/6 — Settings & admin

| Method & path | Purpose |
|---|---|
| `GET /api/settings/subscriptions` | household service list |
| `PUT /api/settings/subscriptions` | replace list |
| `GET /api/providers?region=GB` | full TMDB provider catalogue for pickers (cached daily) |
| `GET /api/settings/region` / `PUT` | region + language (defaults GB / en-GB) |
| `GET /api/insights/services` | Phase 6 subscribe/drop suggestions |

```json
// PUT /api/settings/subscriptions
{ "subscriptions": [
  { "provider_id": 8,   "monthly_cost_pence": 1299 },
  { "provider_id": 337, "monthly_cost_pence": 799 },
  { "provider_id": 38,  "monthly_cost_pence": 0 },
  { "provider_id": 103, "monthly_cost_pence": 0 },
  { "provider_id": 593, "monthly_cost_pence": 0 } ] }
// → 200 echoes with names/logos resolved from the provider catalogue

// GET /api/insights/services → see PHASE-6 doc for full example
```

---

## Phase 7 — Local media → TV

| Method & path | Purpose |
|---|---|
| `GET  /api/media/library` | indexed local files + TMDB match state |
| `POST /api/media/scan` | rescan media folders (async job) |
| `POST /api/media/match/{file_id}` | `{ "tmdb_id": 949 }` manual match |
| `POST /api/media/play` | `{ "film_id": 949 }` → tell Jellyfin to play on the TV |

```json
// POST /api/media/play
{ "film_id": 949 }
// → 200 { "status": "playing", "target": "LG webOS TV", "via": "jellyfin",
//          "jellyfin_item_id": "f0e1…" }
// → 503 {"detail":"No Jellyfin session found for the TV — is it on?","code":"tv_not_available"}
```

---

## Endpoint ↔ phase summary

| Phase | New endpoints |
|---|---|
| 2 | `/api/films*`, `/api/import/*`, `/api/sync/*`, `/api/letterboxd/credentials*`, `/api/settings/subscriptions`, `/api/providers` |
| 3 | `/api/recommendations*`, `/api/model/*` |
| 4 | `/api/auth/*`, rating/like/seen/feedback, `/api/prompts/*` (+ global auth switch-on) |
| 5 | `/api/letterboxd/log*` |
| 6 | `/api/insights/services` |
| 7 | `/api/media/*` |

> Note on ordering: full login ships in Phase 4, but the API is internet-exposed from the moment the tunnel goes live. **Interim guard for Phases 2–3:** a single static bearer token (`MISHKA_DEV_TOKEN`, long random string, required by the same global dependency; the SPA keeps it in localStorage after a minimal token-entry screen). This keeps "every endpoint except /api/health requires auth" true from day one and is replaced transparently by JWTs in Phase 4.
