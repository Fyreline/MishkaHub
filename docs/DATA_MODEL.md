# Mishka Hub — Data Model (SQLite)

This document is the canonical schema for Mishka Hub's SQLite database: executable DDL, the reasoning behind each table, index choices, and the migrations strategy. Application ORM models (SQLAlchemy 2.x) must mirror this file; when they diverge, this file wins and a migration is written. See [ARCHITECTURE.md](ARCHITECTURE.md) for where the DB sits and [API.md](API.md) for how rows surface over HTTP.

**Status: planned**

---

## 1. Conventions

- SQLite file: `data/mishka.db` (`MISHKA_DATABASE_URL=sqlite:///./data/mishka.db`).
- Connection pragmas set on every connect: `PRAGMA journal_mode=WAL;` `PRAGMA foreign_keys=ON;` `PRAGMA busy_timeout=5000;`
- Timestamps are UTC ISO-8601 strings (`TEXT`); calendar dates (`watched_date`) are `TEXT 'YYYY-MM-DD'` because Letterboxd deals in calendar dates, not timestamps ([import docs](https://letterboxd.com/about/importing-data/)).
- `films.id` **is the TMDB movie id** — the canonical key of the whole system. Everything joins on it.
- Ratings use Letterboxd's scale: **0.5–5.0 in 0.5 steps**, stored as `REAL`.
- Enumerations are `TEXT` + `CHECK` constraints (SQLite has no enum type).
- Big blobs (feature matrices, model weights) live **on disk** under `data/models/`; the DB stores only paths + metadata (keeps the DB tiny and backups fast).

## 2. Full DDL

```sql
-- ============ users (Phase 4; created in Phase 2 with placeholder rows) ============
CREATE TABLE users (
    id              INTEGER PRIMARY KEY,          -- 1 and 2. Two rows, ever.
    email           TEXT    NOT NULL UNIQUE,
    display_name    TEXT    NOT NULL,
    password_hash   TEXT,                         -- argon2id; NULL until Phase 4 seeds it
    letterboxd_username TEXT UNIQUE,              -- for RSS URL + slug links
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ============ films: one row per TMDB movie ============
CREATE TABLE films (
    id                INTEGER PRIMARY KEY,        -- TMDB movie id (canonical)
    imdb_id           TEXT,
    letterboxd_slug   TEXT,                       -- e.g. 'toy-story-4' (from RSS link)
    letterboxd_uri    TEXT,                       -- e.g. 'https://boxd.it/29qU' (from CSV)
    title             TEXT NOT NULL,
    original_title    TEXT,
    release_year      INTEGER,
    release_date      TEXT,                       -- YYYY-MM-DD
    runtime_min       INTEGER,
    original_language TEXT,                       -- ISO 639-1
    overview          TEXT,
    poster_path       TEXT,                       -- TMDB path, e.g. '/abc.jpg'
    backdrop_path     TEXT,
    popularity        REAL,
    vote_average      REAL,
    vote_count        INTEGER,
    metadata_json     TEXT,                       -- full TMDB payload incl. credits+keywords
    tmdb_fetched_at   TEXT,                       -- when metadata_json was last refreshed
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_films_slug  ON films(letterboxd_slug);
CREATE INDEX idx_films_year  ON films(release_year);
CREATE INDEX idx_films_title ON films(title);

-- ============ watches: diary entries (one row per viewing) ============
CREATE TABLE watches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    film_id         INTEGER NOT NULL REFERENCES films(id),
    watched_date    TEXT,                         -- YYYY-MM-DD; NULL for 'watched' w/o date
    rewatch         INTEGER NOT NULL DEFAULT 0,   -- boolean
    tags_json       TEXT,                         -- JSON array of strings
    source          TEXT NOT NULL DEFAULT 'letterboxd-import'
                    CHECK (source IN ('letterboxd-import','letterboxd-scrape','letterboxd-rss','in-app')),
    letterboxd_guid TEXT UNIQUE,                  -- RSS guid 'letterboxd-watch-NNN' (dedup key;
                                                  -- scraped diary rows set it from data-viewing-id,
                                                  -- see PHASE-2 §3a assumption)
    letterboxd_uri  TEXT,                         -- diary.csv per-entry URI (dedup key)
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_watches_user_film ON watches(user_id, film_id);
CREATE INDEX idx_watches_date      ON watches(user_id, watched_date);
CREATE UNIQUE INDEX idx_watches_dedup
    ON watches(user_id, film_id, ifnull(watched_date,''));  -- CSV re-import idempotency

-- ============ ratings: current rating per (user, film) ============
CREATE TABLE ratings (
    user_id     INTEGER NOT NULL REFERENCES users(id),
    film_id     INTEGER NOT NULL REFERENCES films(id),
    rating      REAL    NOT NULL CHECK (rating >= 0.5 AND rating <= 5.0),
    source      TEXT    NOT NULL CHECK (source IN ('letterboxd-import','letterboxd-scrape','letterboxd-rss','in-app')),
    rated_at    TEXT,                             -- when the user rated (from export 'Date')
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, film_id)
);
CREATE INDEX idx_ratings_film ON ratings(film_id);

-- ============ likes ============
CREATE TABLE likes (
    user_id     INTEGER NOT NULL REFERENCES users(id),
    film_id     INTEGER NOT NULL REFERENCES films(id),
    source      TEXT    NOT NULL CHECK (source IN ('letterboxd-import','letterboxd-scrape','letterboxd-rss','in-app')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, film_id)
);

-- ============ reviews ============
CREATE TABLE reviews (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL REFERENCES users(id),
    film_id           INTEGER NOT NULL REFERENCES films(id),
    review_text       TEXT NOT NULL,
    contains_spoilers INTEGER NOT NULL DEFAULT 0,
    watched_date      TEXT,
    source            TEXT NOT NULL CHECK (source IN ('letterboxd-import','letterboxd-scrape','letterboxd-rss','in-app')),
    -- note: 'letterboxd-scrape' never inserts review rows (text isn't scraped —
    -- PHASE-2 §3d) but the value is allowed for forward-compatibility
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_reviews_user_film ON reviews(user_id, film_id);

-- ============ subscriptions: household streaming services ============
CREATE TABLE subscriptions (
    provider_id        INTEGER PRIMARY KEY,       -- TMDB watch-provider id (e.g. Netflix=8)
    provider_name      TEXT NOT NULL,
    logo_path          TEXT,                      -- TMDB logo path
    monthly_cost_pence INTEGER,                   -- optional; powers Phase 6 cost math
    active             INTEGER NOT NULL DEFAULT 1,
    added_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Seed list (verified TMDB GB ids, 2026-07): Netflix=8, Amazon Prime Video=9,
-- Disney Plus=337, Apple TV(+)=350, BBC iPlayer=38, Channel 4=103, ITVX=41,
-- STV Player=593, Now TV=39, Sky Go=29, MUBI=11. Full list & enumeration
-- endpoint: docs/phases/PHASE-3-recommender.md §3.

-- ============ availability: film × provider cache (TTL) ============
CREATE TABLE availability (
    film_id     INTEGER NOT NULL REFERENCES films(id),
    provider_id INTEGER NOT NULL,
    kind        TEXT    NOT NULL CHECK (kind IN ('flatrate','free','ads','rent','buy')),
    region      TEXT    NOT NULL DEFAULT 'GB',
    fetched_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (film_id, provider_id, kind, region)
);
CREATE INDEX idx_availability_fetched ON availability(fetched_at);
-- TTL policy: rows older than 7 days are refreshed lazily on read and by the
-- nightly job for any film currently in the recommendation pool. A film with
-- zero rows + a fresh 'availability_checked_at' sentinel in sync_state means
-- "checked, nowhere to stream".

-- ============ unmatched_imports: manual-resolution queue (Phase 2) ============
CREATE TABLE unmatched_imports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    source_file     TEXT NOT NULL,                -- 'diary.csv', 'ratings.csv', 'rss',
                                                  -- 'scrape:films', 'scrape:diary', ...
    raw_row_json    TEXT NOT NULL,                -- the untouched CSV row / RSS item
    name            TEXT,
    year            INTEGER,
    letterboxd_uri  TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','matched','ignored')),
    matched_film_id INTEGER REFERENCES films(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_unmatched_status ON unmatched_imports(status);

-- ============ import_runs: one row per Letterboxd import run (Phase 2 cascade) ============
-- Audit + progress record for the multi-source cascade (PHASE-2 §1). Surfaced over
-- HTTP as job_id 'imp_<id>' (API.md §Phase 2). Cursors live in sync_state; this
-- table is the per-run history.
CREATE TABLE import_runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    source_requested TEXT NOT NULL CHECK (source_requested IN
                     ('auto','export','export-upload','scrape','rss')),
    source_used      TEXT CHECK (source_used IN
                     ('export','export-upload','scrape','rss')),  -- NULL until the cascade settles
    trigger          TEXT NOT NULL CHECK (trigger IN ('schedule','api','upload','drop_folder')),
    status           TEXT NOT NULL DEFAULT 'running'
                     CHECK (status IN ('running','done','done_unchanged','failed')),
    stage            TEXT,                        -- 'login','downloading','scraping:films:p3',
                                                  -- 'parsing','matching','hydrating'
    started_at       TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at      TEXT,
    counts_json      TEXT,                        -- per-file/per-entity counts (job response shape)
    cascade_json     TEXT,                        -- [{"source":"export","outcome":"failed",
                                                  --   "code":"login_challenge"}, …] fallback audit
    export_zip_path  TEXT,                        -- data/letterboxd/… when source_used = export*
    export_sha256    TEXT,                        -- unchanged hash vs last success ⇒ 'done_unchanged'
    error            TEXT
);
CREATE INDEX idx_import_runs_user ON import_runs(user_id, started_at);

-- ============ feedback_events: append-only log driving the model (Phase 4) ============
CREATE TABLE feedback_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    film_id     INTEGER NOT NULL REFERENCES films(id),
    event_type  TEXT NOT NULL CHECK (event_type IN
                ('rating','like','seen','not_interested','snooze','clicked','watchlisted','prompt_answer')),
    value       REAL,                             -- rating value; 1/0 for booleans
    context     TEXT CHECK (context IN ('rec','search','poster_wall','prompt','import')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_feedback_user_time ON feedback_events(user_id, created_at);
CREATE INDEX idx_feedback_film      ON feedback_events(film_id);
-- Note: 'ratings'/'likes' tables hold current state; feedback_events holds the
-- full history (including retractions) for retraining + active learning.

-- ============ model_artifacts: pointer records for on-disk artefacts (Phase 3) ============
CREATE TABLE model_artifacts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL CHECK (kind IN
                 ('feature_matrix','vocab','taste_model','item_embeddings','rec_snapshot')),
    user_id      INTEGER REFERENCES users(id),    -- NULL for shared artefacts
    version      TEXT NOT NULL,                   -- e.g. '2026-07-03T02:00Z' or semver
    path         TEXT NOT NULL,                   -- relative to data/models/
    metrics_json TEXT,                            -- eval metrics at train time
    is_active    INTEGER NOT NULL DEFAULT 0,
    trained_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_artifacts_kind_active ON model_artifacts(kind, is_active);

-- ============ recommendations_cache: last computed ranking (serves UI fast) ============
CREATE TABLE recommendations_cache (
    profile        TEXT    NOT NULL,              -- 'user:1', 'user:2', 'together'
    film_id        INTEGER NOT NULL REFERENCES films(id),
    rank           INTEGER NOT NULL,
    score          REAL    NOT NULL,
    components_json TEXT,                         -- per-term score breakdown ("why this?")
    model_version  TEXT NOT NULL,
    generated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (profile, film_id)
);

-- ============ sync_state: cursors + job bookkeeping ============
CREATE TABLE sync_state (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL CHECK (kind IN
                  ('rss','csv_import','letterboxd_export','letterboxd_scrape',
                   'availability_refresh','retrain','backup','letterboxd_log')),
    user_id       INTEGER REFERENCES users(id),
    cursor        TEXT,                           -- 'rss': JSON array of seen guids (cap 200)
                                                  -- 'letterboxd_export': {"last_sha256": "…",
                                                  --   "last_success_at": "…"}
                                                  -- 'letterboxd_scrape': {"last_full_scrape_at": "…"}
    last_run_at   TEXT,
    last_ok_at    TEXT,
    status        TEXT CHECK (status IN ('ok','running','error')),
    detail_json   TEXT,                           -- error text, counts, job payloads
    UNIQUE (kind, user_id)
);

-- ============ letterboxd_log_jobs: Phase 5 write-back queue ============
CREATE TABLE letterboxd_log_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    film_id       INTEGER NOT NULL REFERENCES films(id),
    payload_json  TEXT NOT NULL,                  -- {watched_date, rating, liked, review, rewatch, tags}
    status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','done','failed','fallback_shown')),
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at   TEXT
);

-- ============ settings: misc key/value (region, thresholds, feature flags) ============
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- Known keys (Phase 2/5): 'letterboxd_automation_ack_user_<id>' (ISO timestamp of the
-- shared ToS acknowledgement — phases/PHASE-2-credentials.md §6),
-- 'letterboxd_writeback_enabled' (Phase 5 kill switch),
-- 'letterboxd_import_automation_enabled' (Phase 2 cascade source-1 switch).
-- NEVER a credential ciphertext — secrets stay out of the DB entirely (§3).

-- ============ refresh_tokens: revocable JWT refresh sessions (Phase 4) ============
CREATE TABLE refresh_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    token_hash  TEXT NOT NULL UNIQUE,             -- sha256 of the opaque token
    expires_at  TEXT NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_refresh_user ON refresh_tokens(user_id, revoked);

-- ============ media_files: Phase 7 local library ============
CREATE TABLE media_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    film_id         INTEGER REFERENCES films(id), -- NULL until matched
    path            TEXT NOT NULL UNIQUE,
    size_bytes      INTEGER,
    container       TEXT,                         -- mkv/mp4/...
    video_codec     TEXT, audio_codec TEXT,
    jellyfin_item_id TEXT,                        -- cross-ref into Jellyfin
    scanned_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_media_film ON media_files(film_id);
```

## 3. Secrets & keychain layout (deliberately NOT in the database)

There is no credentials table. Letterboxd passwords and the Fernet master key live in the **macOS Keychain**; encrypted session blobs live as files under `data/secrets/`. Full design (the `SecretStore` abstraction, backends, access-grant handling, rotation): [phases/PHASE-2-credentials.md](phases/PHASE-2-credentials.md). The layout, for reference alongside the schema:

| Store | Location | Item | Holds |
|---|---|---|---|
| Keychain (generic password) | macOS login keychain, via [`keyring`](https://pypi.org/project/keyring/) | service `mishka-hub-letterboxd`, account `<letterboxd_username>` (`example_user1`, `example_user2`) | that member's Letterboxd password |
| Keychain (generic password) | macOS login keychain | service `mishka-hub`, account `fernet-master-key` | Fernet key encrypting the blob files below |
| File ([Fernet](https://cryptography.io/en/latest/fernet/) ciphertext) | `data/secrets/letterboxd_session_<user_id>.enc` | — | Playwright `storage_state` (login-session cookies) |
| File (Fernet ciphertext, fallback backend only) | `data/secrets/secrets.enc` | JSON map `{service: {account: secret}}` | all of the above on a keychain-less host (future Windows desktop), key from env `MISHKA_SECRETS_KEY` |

The DB references secrets only indirectly: `users.letterboxd_username` is the Keychain account name (resolution path in [PHASE-2-credentials.md §3](phases/PHASE-2-credentials.md)), and `settings` holds the boolean/timestamp gates listed in §2 — never ciphertext.

## 4. Design notes

| Decision | Why |
|---|---|
| `films.id` = TMDB id | TMDB is the metadata + availability + discovery source; Letterboxd RSS even hands us `tmdb:movieId` directly (verified live, 2026-07). No surrogate-key mapping layer needed. |
| No credentials table | Passwords belong in the OS keychain, not SQLite — the DB file is backed up nightly and copied around; keychain items are neither. See §3. |
| `import_runs` separate from `sync_state` | `sync_state` is one row per (kind, user) holding *cursors* (seen RSS guids, last export SHA-256); `import_runs` is append-only *history* — one row per cascade run with source/fallback audit (`cascade_json`) and the counts the API job endpoint serves. |
| Ratings as state table + feedback_events as log | The recommender trains on current ratings but active learning and "model got better/worse" analyses need history. Upserting `ratings` and appending `feedback_events` on every change gives both cheaply. |
| Watches dedup via three keys | RSS items dedupe on `letterboxd_guid`; CSV rows dedupe on `(user, film, watched_date)`; diary URIs kept as a tertiary audit key. Re-running any import must be a no-op. |
| Availability composite PK incl. `kind` | The same film is often on a provider both `flatrate` and `rent` (e.g. Amazon); Phase 6 needs the distinction. |
| Artefacts on disk, not BLOBs | Feature matrices are scipy sparse `.npz` + JSON vocabs; models are joblib dumps. Keeping them out of SQLite keeps `.backup` fast and lets retrains write atomically (write new dir → flip `is_active`). |
| `subscriptions.provider_id` as PK | The household has one subscription list (not per-user) per the product decision; region lives in `settings`. |

## 5. Migrations strategy

- **Tool: Alembic** (works fine with SQLAlchemy 2.x models on SQLite), migrations in `apps/server/migrations/`.
- Baseline migration `0001_initial` creates everything in §2.
- Autogenerate is a starting point only — SQLite's limited `ALTER TABLE` means many changes need Alembic's **batch mode** (`render_as_batch=True` in `env.py`), which does the copy-table dance safely.
- The server refuses to start if the DB revision ≠ head; `dev.sh` and the launchd service run `alembic upgrade head` before uvicorn. A CLI shortcut `python -m app.cli migrate` wraps it.
- Every migration must be reversible or explicitly marked destructive in its docstring; the nightly backup ([DEPLOYMENT.md](DEPLOYMENT.md)) runs before any deploy that includes migrations.
- Data migrations (e.g. backfilling `letterboxd_slug`) are separate revisions from schema migrations.

## 6. Sizing expectations (sanity)

Two users, ~2–3k watched films, ~50k candidate films with metadata cached over time: `films.metadata_json` dominates at roughly 10–20 KB/row → DB stays well under 1 GB. WAL mode handles the concurrency we have (uvicorn single process + background jobs). No sharding/Postgres migration is ever expected; if it happens anyway, the DDL above is portable except the pragmas and `ifnull` index expression.
