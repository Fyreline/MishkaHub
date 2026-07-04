# Phase 2 — Letterboxd Import Cascade & Cat-alogue

Purpose: get both members' complete Letterboxd history (watches, diary, ratings, likes, reviews) into the [data model](../DATA_MODEL.md), matched to TMDB ids, kept current automatically, and rendered as the **Cat-alogue** (poster wall UI). This unlocks everything downstream: the recommender trains on this data, and "not already seen" filtering depends on it.

**Locked decision:** history is imported through a **fallback cascade** — (1) automate the official data export with the members' own logins, (2) scrape their public profile pages when that fails, (3) RSS polling keeps things current, (4) in-app "seen it / rate" is the always-available floor. Consequence: the encrypted credential store moves forward from Phase 5 into Phase 2 as a **shared module** — see [PHASE-2-credentials.md](PHASE-2-credentials.md).

**Status: planned**

---

## 1. The cascade at a glance

```
  weekly schedule (per user, staggered) ──or── POST /api/import/letterboxd/run {"source":"auto"}
                       │
                       ▼
        ┌───────────────────────────────────┐
        │ 1 · AUTOMATED EXPORT  (primary)   │  authoritative & complete:
        │   creds in SecretStore?           │  watched, ratings, diary, reviews,
        │   Playwright login →              │  likes, watchlist — incl. entries
        │   GET /data/export/ → ZIP → CSVs  │  with restricted visibility
        └───────┬───────────────────────────┘
           ok   │        │ fell through: no_credentials · tos_not_acknowledged ·
                │        │ login_challenge · selector_broken · export_unavailable ·
                │        │ download timeout
                │        ▼
                │   ┌───────────────────────────────────┐
                │   │ 2 · PUBLIC PROFILE SCRAPE (fallback)│  public data only:
                │   │   /<user>/films/ (+ pagination)     │  watched + rating + like
                │   │   /<user>/films/diary/ (dated)      │  + dated diary entries;
                │   │   no login, throttled               │  no review text, no
                │   └───────┬───────────────────────────┘  restricted entries
                │      ok   │        │ fell through: cloudflare_challenge ·
                │           │        │ dom_changed · zero rows parsed
                │           │        ▼
                │           │   run marked failed → library stays as-is;
                │           │   sources 3 & 4 still cover new activity
                ▼           ▼
        ┌─────────────────────────────┐
        │ shared parse / match / merge │──► films · watches · ratings · likes ·
        │        (§6 §7 §8)            │    reviews · unmatched_imports
        └─────────────────────────────┘
                ▲                ▲
   every 6 h,   │                │ always available in the UI
   independent  │                │
  ┌─────────────┴───┐   ┌────────┴─────────────────┐
  │ 3 · RSS POLLING │   │ 4 · IN-APP "seen it/rate" │──► feedback events
  │  ~50 newest (§4)│   │   manual backstop (§5)    │    (Phase 4)
  └─────────────────┘   └──────────────────────────┘
```

| Step | Runs when | Falls through when | Rows marked `source=` |
|---|---|---|---|
| 1 export | schedule (weekly) or API trigger, **and** credentials present **and** ToS acknowledged | credentials missing, login challenge (captcha/2FA), login selectors broken, response isn't a ZIP, download timeout | `letterboxd-import` |
| 1b manual ZIP | member uploads/drops an export ZIP themselves (kept from the original design, §2d) | n/a — it *is* the manual variant of 1 | `letterboxd-import` |
| 2 scrape | step 1 fell through, or forced via `{"source":"scrape"}` | Cloudflare challenge persists in the real browser, page structure changed, zero films parsed | `letterboxd-scrape` |
| 3 rss | every 6 h regardless of 1/2 outcomes | feed non-200 → skip cycle, retry next | `letterboxd-rss` |
| 4 in-app | always (UI) | never | `in-app` |

Each run writes one [`import_runs`](../DATA_MODEL.md) row (`source_requested`, `source_used`, `cascade_json` audit trail, counts) surfaced as `job_id` via [API.md](../API.md) §Phase 2. One concurrent run per user (`409 duplicate_job` otherwise).

## 2. Source 1 — automated data export (primary)

### 2a. Mechanism (verified)

- The export lives at **Settings → Data** ([letterboxd.com/settings/data](https://letterboxd.com/settings/data), login required) with an *"Export your data"* control; the login-gated canonical page is [letterboxd.com/user/exportdata/](https://letterboxd.com/user/exportdata/).
- Behind the button is a **direct authenticated URL**: an authenticated `GET https://letterboxd.com/data/export/` returns the ZIP in the response body. Verified from the source of a working automated exporter ([aaronmanning/letterboxd-export](https://git.aaronmanning.net/aaronmanning/letterboxd-export), Rust: CSRF cookie `com.xk72.webparts.csrf` from the homepage → `POST /user/login.do` with `__csrf` form field → `GET /data/export/`, expecting `200` + `Content-Length`).
- ⚠️ **Assumption:** some third-party guides (e.g. [Cinelytix](https://cinelytix.app/letterboxd-data)) describe an asynchronous "we'll email you a download link" flow instead. Possibly account-size dependent or a newer variant. Handled as the `export_unavailable` failure class below — first real run against our accounts settles it; update this section then.
- ⚠️ **Assumption:** downloaded ZIP filename format (commonly reported as `letterboxd-<username>-YYYY-MM-DD-HH-MM-utc.zip`) is unverified — irrelevant to correctness, as we name the saved file ourselves.
- The export is the **authoritative** source: it includes everything the profile pages may hide (entries with restricted visibility) and, per community reports, even deleted content ([feadin.eu export analysis](https://www.feadin.eu/en/posts/letterboxd_i_love_you_but_we_need_to_talk_about_your_exports/) ⚠️ deleted-content claim community-reported, unverified).

### 2b. Run steps (Playwright, chromium headless — shared stack with [Phase 5](PHASE-5-letterboxd-writeback.md))

```
run_export(user):
  1. password = SecretStore.get("mishka-hub-letterboxd", user.letterboxd_username)
       None / keychain access denied ⇒ outcome 'no_credentials' ⇒ cascade to §3
       ToS ack missing ⇒ 'tos_not_acknowledged' ⇒ cascade      (PHASE-2-credentials.md §6)
  2. ensure_session(user)          # BUILT HERE, reused by Phase 5 §4:
       rehydrate Fernet-encrypted storage_state → context; verify signed-in avatar
       else login via /sign-in form (selectors in the shared selectors module);
       captcha / 2FA / email-verify challenge ⇒ 'login_challenge' ⇒ cascade
  3. resp = context.request.get("https://letterboxd.com/data/export/")
       # APIRequestContext reuses the browser session's cookies — the export step
       # needs NO DOM selectors at all (only login does)
       body starts 'PK\x03\x04' (ZIP magic)?
         no & HTML ⇒ try the UI path once: page.goto('/settings/data/'),
                     click the export control inside expect_download();
                     still no ZIP ⇒ 'export_unavailable' ⇒ cascade
  4. save data/letterboxd/exports/<username>/<UTC-timestamp>.zip   (keep newest 3)
  5. sha256(zip) == sync_state(letterboxd_export, user).last_sha256
       ⇒ run status 'done_unchanged', stop (nothing new to parse)
  6. unzip → sibling dir → shared header-driven CSV parser (§6) → matching (§7) → merge (§8)
  7. update sync_state cursor {last_sha256, last_success_at}; run 'done' with counts
```

- Schedule: weekly per user, staggered (e.g. Sun 03:10 `Luminalmvm`, 03:40 `garfieldsama`) from the FastAPI-lifespan task loop; also on demand via `POST /api/import/letterboxd/run`. Weekly is plenty — RSS (§4) covers the week's activity; the export reconciles edits, old-entry changes and restricted items.
- Pacing/politeness as Phase 5: random 1–3 s pauses, realistic UA, one user at a time, sessions persisted so `/sign-in` is touched rarely ([PHASE-5 §2](PHASE-5-letterboxd-writeback.md)).
- ToS note: automating one's own account is against Letterboxd's [terms](https://letterboxd.com/legal/terms-of-use/); accepted, gated per user behind the shared acknowledgement ([PHASE-2-credentials.md §6](PHASE-2-credentials.md)).

### 2c. Failure classes

| Code | Meaning | Cascade behaviour |
|---|---|---|
| `no_credentials` | Keychain item absent, or headless keychain access not yet granted | skip to scrape; surface a settings-page hint ("add your Letterboxd password") |
| `tos_not_acknowledged` | ack key absent | skip to scrape; UI shows the ack modal on next visit |
| `login_challenge` | captcha / 2FA / verification interstitial | no retry (human needed); scrape; banner mirrors Phase 5's |
| `selector_broken` | sign-in form fields not found (site change) | scrape; screenshot + DOM dump to `data/playwright/failures/` (same convention as [PHASE-5 §5](PHASE-5-letterboxd-writeback.md)) |
| `export_unavailable` | `/data/export/` and the UI click both failed to yield a ZIP | scrape; if HTML mentioned an emailed link, record that in `cascade_json` for investigation |
| transient (timeout, nav error) | network blip | retry once within the run, then cascade |

### 2d. Manual variant (kept from the original design)

Members can always fetch the ZIP themselves from [letterboxd.com/settings/data](https://letterboxd.com/settings/data). Two ingestion routes, both feeding the same parser (§6):

1. **UI upload** — `POST /api/import/letterboxd?user=1|2` (multipart, see [API.md](../API.md)).
2. **Drop folder** — place the ZIP in `data/letterboxd/incoming/<user_id>/`; a watcher (poll every 30 s while the server runs) picks it up, processes it, and moves it to `…/processed/` with a timestamp suffix. (Initial ZIPs may also be staged in `reference/` per its README, then dropped here.)

These record `source_requested='export-upload'`, `trigger='upload'|'drop_folder'` in `import_runs`.

## 3. Source 2 — public profile scrape (fallback)

No login, no credentials at risk, public data only. Activates automatically when source 1 falls through, or explicitly via `{"source":"scrape"}`.

### 3a. Pages, pagination, parsing

Runs in a **fresh, signed-out Playwright context** (see 3c for why a real browser rather than plain `httpx`). All facts below verified live against `letterboxd.com/dave/films/` on 2026-07-03 unless noted.

**Films grid — `letterboxd.com/<user>/films/`** → every watched film with current rating/like (undated):

| What | Where in the HTML |
|---|---|
| grid items | `div.poster-grid > ul.grid > li.griditem` — **72 per page** |
| film slug | `div.react-component[data-component-class="LazyPoster"]` attr **`data-item-slug`** (e.g. `toy-story-4`) → `films.letterboxd_slug` |
| title + year | `data-item-name` = `"Toy Story 4 (2019)"` (also `data-item-full-display-name`); split on the final ` (` |
| film page link | `data-item-link` = `/film/<slug>/` |
| per-film JSON hook | `data-details-endpoint` = `/film/<slug>/json/` (available if ever needed; not used in v1) |
| **rating** | sibling `p.poster-viewingdata > span.rating.rated-<N>` where **N ∈ 1–10 = stars × 2** (`rated-7` = ★★★½ = 3.5); span absent ⇒ unrated |
| liked | `p.poster-viewingdata > span.like … span.icon-liked` present |
| pagination | `div.paginate-pages` → links `/<user>/films/page/<N>/`; iterate until the "next" anchor in `.paginate-nextprev` is disabled/absent |

**Diary — `letterboxd.com/<user>/films/diary/`** → dated entries (adds `watched_date`, rewatch). Structure per the actively maintained [letterboxdpy](https://github.com/nmcassa/letterboxdpy) parser (matches the same `react-component`/`data-item-*` markup seen live on `/films/`):

| What | Where in the HTML |
|---|---|
| table | `table#diary-table`; `<th class="th-*">` classes name the columns (daydate, production, releaseyear, rating, like, rewatch, review, actions) |
| row | `tr.diary-entry-row[data-viewing-id]` |
| watched date | daydate cell link `/<user>/films/diary/for/YYYY/MM/DD/` |
| film | `div.react-component` attrs `data-item-slug`, `data-item-name`, `data-film-id` (Letterboxd-internal id) |
| rating | `span.rated-<N>` → N ÷ 2 stars |
| rewatch | rewatch cell **lacks** class `icon-status-off` |
| liked | like cell contains `span.icon-liked` |
| has review | review cell contains an `<a>` (text itself not scraped — §3d) |
| pagination | `/<user>/films/diary/page/<N>/`; year/month filters `/for/YYYY/MM/` exist if ever needed |

**Watchlist — `letterboxd.com/<user>/watchlist/`** (optional, same grid markup as films): rows → `feedback_events` `watchlisted`, mirroring the CSV mapping in §6.

⚠️ **Assumption:** the diary row's `data-viewing-id` appears to be the same viewing id used in RSS guids (`letterboxd-watch-<id>`, §4). Scraped diary watches therefore store `letterboxd_guid = 'letterboxd-watch-' + viewing_id` for free cross-source dedup — but this equality is unverified. Safe either way: the `(user_id, film_id, watched_date)` unique index (§8) is the load-bearing dedup key; verify the id equality on first run by comparing one entry present in both feeds, and drop the synthetic guid if it mismatches.

### 3b. Scrape flow

```
run_scrape(user):
  A. films grid: walk /<u>/films/ pages → rows {slug, title, year, rating?, liked}
  B. diary:      walk /<u>/films/diary/ pages → rows {slug, title, year, watched_date,
                                                      rating?, rewatch, liked, viewing_id}
  C. watchlist (optional): walk /<u>/watchlist/ → rows {slug, title, year}
  normalise every row to the same shape the CSV parser (§6) emits
  → TMDB matching (§7; slug + title + year available — no tmdb id in the HTML)
  → merge (§8) with source='letterboxd-scrape'
```

A dated diary row upgrades the corresponding undated films-grid row exactly as `diary.csv` upgrades `watched.csv` (§8).

### 3c. Politeness & the Cloudflare reality

- **Verified live (2026-07-03):** letterboxd.com sits behind Cloudflare. To a plain HTTP client (curl, honest UA), `/<user>/films/` and `/<user>/rss/` returned `200`, but **`/<user>/films/diary/` returned `403` with `cf-mitigated: challenge`** (the "Just a moment…" JS challenge) — path-level bot protection. Hence the scraper uses the real Playwright browser (already a dependency), which passes managed challenges in normal circumstances; a challenge that persists ⇒ failure class `cloudflare_challenge` ⇒ run fails, RSS + manual still cover.
- Throttle: sequential fetches, `2.5 s ± 1 s` jitter between pages, hard cap 100 pages per user per run (7,200 films — far beyond the couple's libraries). One user at a time. Runs only as a fallback, typically never — this is not a crawler.
- Record per-page progress in `import_runs.stage` so a mid-run failure resumes politely rather than restarting from page 1 within the same day.

### 3d. What the scrape cannot see (why it's the fallback)

- **Review text, tags** — deliberately out of scope v1 (reviews are paginated per-film and HTML-heavy; the export carries them properly). The diary's has-review flag is kept so the export can fill text later.
- **Restricted-visibility items**: Letterboxd supports per-member and per-item visibility (Anyone / Close friends / You) for activity and the watchlist — anything not "Anyone" is invisible publicly (and to RSS); a fully private account hides everything ([Letterboxd help centre](https://letterboxd.zendesk.com/hc/en-us/articles/15179261056143-What-s-the-difference-between-my-lists-and-my-watchlist)). Both members' profiles are currently public, so exposure is theoretical for us.
- **No `tmdb:movieId`** anywhere in the markup — unlike RSS, every scraped row goes through title/year matching (§7), so expect a slightly higher unmatched rate than CSV (same queue handles it).
- Historical rating *changes* (only current state is shown) and precise log timestamps (`Date` column in CSVs).

## 4. Source 3 — RSS polling (incremental, always on)

`https://letterboxd.com/<username>/rss/` — public, no auth, sanctioned feed. Structure verified by fetching live feeds (2026-07-03; re-verified same day for this revision):

```xml
<rss xmlns:letterboxd="https://letterboxd.com" xmlns:tmdb="https://themoviedb.org" …>
  <item>
    <title>Toy Story 4, 2019 - ★★★½</title>
    <link>https://letterboxd.com/dave/film/toy-story-4/1/</link>
    <guid isPermaLink="false">letterboxd-watch-1369875590</guid>
    <pubDate>Sat, 27 Jun 2026 00:17:04 +1200</pubDate>
    <letterboxd:watchedDate>2026-06-25</letterboxd:watchedDate>
    <letterboxd:rewatch>Yes</letterboxd:rewatch>
    <letterboxd:filmTitle>Toy Story 4</letterboxd:filmTitle>
    <letterboxd:filmYear>2019</letterboxd:filmYear>
    <letterboxd:memberRating>3.5</letterboxd:memberRating>
    <letterboxd:memberLike>Yes</letterboxd:memberLike>
    <tmdb:movieId>301528</tmdb:movieId>
    <description><![CDATA[ <p><img src="…poster…"/></p> <p>Watched on Thursday June 25, 2026.</p> ]]></description>
    <dc:creator>Dave Vis</dc:creator>
  </item>
```

Field notes (all verified):

| Field | Notes |
|---|---|
| `guid` | `letterboxd-watch-<id>` (plain log), `letterboxd-review-<id>` (log with review), `letterboxd-list-<id>` (**skip** — list activity). Stable → **the dedup/cursor key**. |
| `link` | `…/<user>/film/<slug>/` (+ `/2/` etc. for rewatch ordinals) → **extract `letterboxd_slug`** for `films`. |
| `tmdb:movieId` | **TMDB id handed to us directly** — no title matching needed for RSS items. |
| `letterboxd:memberRating` | 0.5–5.0; element absent when unrated. |
| `letterboxd:memberLike` | `Yes` when liked; absent otherwise. |
| `letterboxd:rewatch` | `Yes`/`No`. |
| `description` | poster `<img>` + review HTML (for `letterboxd-review-*` items) or "Watched on …" text. Review text = description minus the poster paragraph. |

Limit: the feed carries only the **most recent ~50 diary/review entries** (plus up to 50 list items) — confirmed by live fetch and community docs ([samdking.co.uk](https://samdking.co.uk/blog/sync-your-letterboxd-film-data-with-eleventy/)). Hence: sources 1/2 = backfill, RSS = keep-fresh. If the couple somehow logs >50 films between polls, the next weekly export reconciles.

**Polling:** every 6 h per user (async task started from FastAPI lifespan; also `POST /api/sync/rss/run`). Politeness: single request per user per cycle, honest User-Agent (`Mishka-Hub/0.x private household sync`), skip cycle on non-200 and record in `sync_state`. RSS also serves the Phase 5 full-circle check (write-back entries flow home and dedup — [PHASE-5 §7](PHASE-5-letterboxd-writeback.md)).

**Cursor:** `sync_state.cursor` for `(rss, user)` = JSON array of the last 200 seen guids. New item ⇒ guid not in set. (Not "newer than last pubDate" — edits can reorder.)

## 5. Source 4 — in-app manual backstop (always available)

The guaranteed floor when everything upstream is broken (and the natural path for films watched off-Letterboxd):

- The Cat-alogue detail drawer (§10) carries **seen-it (with optional date), ★ rating and heart** controls. They write `watches`/`ratings`/`likes` rows with `source='in-app'` **and** append `feedback_events` — the exact contract of [PHASE-4 §feedback](PHASE-4-accounts-feedback.md); endpoints are specified in [API.md](../API.md) §Phase 4 (`POST /api/films/{id}/seen`, `PUT /api/films/{id}/rating`, `PUT /api/films/{id}/like`).
- Films not yet in the library are added via the existing TMDB search (`GET /api/tmdb/search`) → film detail → "seen it", which upserts the `films` row first.
- Delivery note: these endpoints belong to Phase 4; if sources 1–3 are all dead at Phase 2 time (unlikely), pull the three state endpoints forward under the interim token guard — they have no dependency on JWT specifics.
- In-app actions never trigger Letterboxd writes until [Phase 5](PHASE-5-letterboxd-writeback.md) ships its queue.

## 6. Shared CSV parser & column mappings (used by sources 1 and 1b)

#### ZIP contents & column mappings

Verified against Letterboxd's own docs and multiple independent parsers ([letterboxd.com/about/importing-data](https://letterboxd.com/about/importing-data/), [feadin.eu export analysis](https://www.feadin.eu/en/posts/letterboxd_i_love_you_but_we_need_to_talk_about_your_exports/), [flaviasalutari.github.io](https://flaviasalutari.github.io/blog/2023/letterboxd/)):

| File in ZIP | Columns (header row) | → Mishka Hub tables |
|---|---|---|
| `watched.csv` | `Date,Name,Year,Letterboxd URI` | `watches` (no watched_date → date NULL), `films` |
| `ratings.csv` | `Date,Name,Year,Letterboxd URI,Rating` | `ratings` (source `letterboxd-import`) |
| `diary.csv` | `Date,Name,Year,Letterboxd URI,Rating,Rewatch,Tags,Watched Date` | `watches` (with date, rewatch, tags), `ratings` |
| `reviews.csv` | `Date,Name,Year,Letterboxd URI,Rating,Rewatch,Review,Tags,Watched Date` | `reviews` (+ ratings/watches as present) |
| `likes/films.csv` | `Date,Name,Year,Letterboxd URI` | `likes` |
| `watchlist.csv` | `Date,Name,Year,Letterboxd URI` | feedback_events `watchlisted` (optional, useful signal) |
| `profile.csv`, `comments.csv`, `lists/*.csv`, `likes/reviews.csv`, `likes/lists.csv` | — | ignored |

⚠️ **Assumption (verify once against a real export):** the community sources above confirm the *fields* of each file but Letterboxd publishes no official spec of export headers, and exact header order for `reviews.csv` and the `likes/` folder layout could drift. **Mitigation is structural:** the parser must be header-driven (`csv.DictReader`, UTF-8) and must log-and-queue any file whose headers don't match expectations instead of erroring — never index columns by position. First real import doubles as verification; update this table if reality differs.

Semantics to honour (from the [official import docs](https://letterboxd.com/about/importing-data/)):
- `Date` = the date the row was created on Letterboxd (log date), **not** the watch date. `Watched Date` is the viewing date.
- `Rating` is 0.5–5.0 in 0.5 steps.
- `Rewatch` is boolean-ish text (`Yes`/empty).
- `Tags` is a comma-delimited quoted string.
- In `diary.csv` the `Letterboxd URI` points at the **specific diary entry** (e.g. `https://boxd.it/xyz`), not the film page — store it on the watch row, don't treat it as a film key.
- Review text may contain HTML (the site allows a small tag set).

#### Processing order

`watched.csv` → `ratings.csv` → `diary.csv` → `reviews.csv` → `likes/films.csv`. Later files enrich earlier rows (diary adds dates/rewatch to films already known as watched; reviews attach to diary entries). Each file is one DB transaction; the run record accumulates per-file counts (see the job response in [API.md](../API.md)).

The scraper (§3) emits rows in this same normalised shape (a "virtual CSV"), so parsing, matching, merging and counting are one code path for all backfill sources.

## 7. TMDB matching strategy

Goal: every imported row ends as a `films.id` = TMDB id.

```
row ──► have tmdb:movieId (RSS)? ──────────────yes──► GET /movie/{id} → upsert film → done
         │ no (CSV / scrape)
         ▼
   films table lookup by (normalised title, year), letterboxd_uri, or letterboxd_slug (scrape)
         │ miss                                       ── hit ──► done
         ▼
   TMDB GET /search/movie?query=<Name>&primary_release_year=<Year>&region=GB
         │ no results → retry once without year   (also retry with year±1 built in below)
         ▼
   score candidates:
     s = 3·exact_title + 2·exact_original_title + 1·(|year diff| ≤ 1)
         + log10(1+vote_count)/4          (popularity tiebreak)
     where exact_* compares casefolded, accent-stripped, punctuation-stripped
         │
         ├─ top score ≥ 3 and beats runner-up by ≥ 1 ──► auto-accept
         └─ otherwise ──► unmatched_imports queue (status 'pending')
```

Disambiguation rules of thumb encoded above: exact title + exact year wins; a ±1-year tolerance absorbs the festival-vs-release-year problem (Letterboxd uses first-release year, TMDB `release_date` can differ by region); never auto-accept on title alone when two candidates share the title (remakes: *Nosferatu* 1922/1979/2024). Scraped rows carry the `letterboxd_slug`, which is also stored on match so future scrapes hit the slug index directly.

**Unmatched queue UI:** a settings-area list showing the raw row (name, year, source) beside the top 5 TMDB candidates (poster, year, director from `/search/movie` + one `/movie/{id}?append_to_response=credits` call on expand). Actions: pick candidate / search manually / ignore. Resolution endpoint in [API.md](../API.md). Target: <2 % of rows unmatched after auto-matching (CSV); scrape may run slightly higher — same queue absorbs it.

**Metadata hydration:** on first insert of a film, fetch `GET /movie/{id}?append_to_response=credits,keywords,release_dates&language=en-GB` and store the payload in `films.metadata_json` (feeds [Phase 3 features](PHASE-3-recommender.md)). Batch politely: ≤10 concurrent requests — TMDB's cap is ~50 req/s with 20 connections/IP ([rate-limiting docs](https://developer.themoviedb.org/docs/rate-limiting)) but there's no hurry; a 1,500-film first import hydrates in ~3 min at 10 rps.

## 8. Dedup & cross-source merge rules (imports must be idempotent)

| Entity | Dedup key | Rule |
|---|---|---|
| watch (CSV/scrape) | `(user_id, film_id, watched_date)` (unique index; NULL date → `''`) | insert-or-skip; a dated diary row (CSV **or** scraped diary) upgrades a dateless row for the same film if one exists (fills date/rewatch/tags) |
| watch (RSS) | `letterboxd_guid` unique | insert-or-skip; scraped diary rows also set a synthetic guid (§3a ⚠️) so RSS↔scrape dedup is direct |
| rating | PK `(user_id, film_id)` | upsert; newest information wins (`Date`/feed order); every change also appends a `feedback_events` row. Scrape only knows *current* state — it upserts like any other source |
| like | PK `(user_id, film_id)` | insert-or-skip |
| review | `(user_id, film_id, watched_date)` + exact-text equality | insert-or-skip; changed text for same key ⇒ new row (history kept). Scrape never writes reviews (§3d) |
| whole ZIP | run-level `export_sha256` | unchanged export ⇒ `done_unchanged`, zero parsing; re-uploading the same ZIP must produce `skipped_duplicates ≈ everything`, 0 new rows |

Cross-source precedence needs no extra machinery: the export is a superset, the scrape is current-state, RSS is recent-state — the keys above make any arrival order converge to the same rows. Provenance is retained per row via `source`, and per run via `import_runs`.

The two members' rows share the same `films` table and differ only by `user_id` — the shared Cat-alogue (§10) is a join, not a merge job.

## 9. Credentials

Everything secret-related — the `SecretStore` abstraction, the macOS Keychain layout (service `mishka-hub-letterboxd`, account = Letterboxd username; `Luminalmvm`'s item already exists), the Fernet file fallback for a future Windows host, encrypted Playwright session blobs, keychain access-grant handling, set/rotate/clear flows, and the shared ToS acknowledgement gate — is specified once in **[PHASE-2-credentials.md](PHASE-2-credentials.md)** and consumed by this phase and [Phase 5](PHASE-5-letterboxd-writeback.md).

## 10. Cat-alogue poster wall UI spec

Route `/films` (SPA). The emotional payoff of Phase 2 — make it feel like a wall of your life in film. Visual language per [DESIGN.md](../DESIGN.md) (poster grid density, drag physics, tokens).

- **Grid:** reuse `MovieCard` (poster 2:3, hover title/year overlay); 2/3/4/6 columns responsive as in the current scaffold; infinite scroll (`limit=60` pages via `GET /api/films`).
- **Card badges:** my rating (★4.5, as now), tiny heart when liked, `↻` when rewatched, and a **two-dot user indicator** (each member their accent from [`config/household.yaml`](../../config/household.yaml) / [DESIGN.md](../DESIGN.md): Luminal = clay, Garfield = sky; filled = they've seen it) — the couple's shared wall shows both at a glance.
- **Filter bar (sticky under header):** user toggle (Me / Partner / Both / Either), rated/unrated, liked, decade chips, genre select, min-rating slider, title search box (client-debounced → `q=`).
- **Sort:** recently watched (default), rating, year, title.
- **Detail drawer** on card click: backdrop, overview, genres, runtime, both users' ratings/likes/watch dates, the §5 manual controls, a small provenance chip (imported via export / scrape / RSS / added in app), availability row with provider logos + **"Streaming availability by JustWatch"** caption (attribution requirement — [ARCHITECTURE.md](../ARCHITECTURE.md) §6).
- **Import status surface:** while a run is active, a slim progress banner (source + stage + counts live-polled from the job endpoint; if the cascade fell to scrape, say so plainly — "export failed, reading public profile instead"); unmatched rows surface as a dismissible chip linking to the resolution queue.
- **Empty state:** "Connect Letterboxd" card offering the two paths: add credentials for automatic import ([PHASE-2-credentials.md §5](PHASE-2-credentials.md)) **or** upload the export ZIP yourself (dropzone + the two-step instructions), with the drop-folder alternative noted for the server admin.

## 11. Implementation notes

- New deps: `sqlalchemy>=2`, `alembic`, `python-multipart` (upload), `feedparser` (RSS; or stdlib `xml.etree` — feedparser handles encoding edge cases), `rapidfuzz` optional for title normalisation, **`playwright`** (+ `playwright install chromium` — pulled forward from Phase 5; document in [DEPLOYMENT.md](../DEPLOYMENT.md) launchd notes, first run downloads the browser), **`keyring`**, **`cryptography`** ([PHASE-2-credentials.md](PHASE-2-credentials.md)).
- Background work: `asyncio.create_task` loops started in FastAPI lifespan (weekly export schedule, RSS poll, drop-folder watcher, availability refresh). No Celery — single-process is plenty for 2 users.
- Import runs execute in a thread executor (CSV parse + many TMDB calls) reporting progress into `import_runs` (`stage`, `counts_json`); Playwright steps use the async API on the loop, serialised — never two browser jobs at once (shared discipline with [PHASE-5 §2](PHASE-5-letterboxd-writeback.md)).
- HTML parsing for the scrape: `BeautifulSoup4` + the attribute/class contracts in §3a, centralised in one `importers/scrape_selectors.py` module (mirror of Phase 5's selectors-in-one-file rule — the only file to touch when Letterboxd's markup changes).
- Time zone: treat all Letterboxd dates as naive calendar dates; never convert.
- Storage layout: `data/letterboxd/{incoming,processed}/<user_id>/` (manual), `data/letterboxd/exports/<username>/` (automated), `data/secrets/` (encrypted blobs — [PHASE-2-credentials.md](PHASE-2-credentials.md)), `data/playwright/failures/` (debug dumps).

## 12. Acceptance criteria

Cascade & sources:
- [ ] With `Luminalmvm`'s Keychain credential present and ToS acknowledged, `POST /api/import/letterboxd/run {"source":"auto"}` downloads a fresh export ZIP to `data/letterboxd/exports/Luminalmvm/`, parses it, and completes with ≥98 % of rows auto-matched to TMDB ids; counts in the job response reconcile with the CSV line counts.
- [ ] Re-running immediately ends `done_unchanged` via the export SHA-256 with zero row changes; re-uploading the same ZIP manually creates **zero** new rows (idempotency proven by counts).
- [ ] With the Keychain item removed (or a deliberately wrong password), the same trigger records the export failure in `cascade_json` and **falls through to the scrape**, which fills watched films + ratings + likes from `/films/` and dated entries from `/films/diary/` with `source='letterboxd-scrape'`; re-running the scrape is idempotent.
- [ ] Scrape throttling holds (sequential fetches, ≥1.5 s apart per the 2.5 s ± 1 s jitter in §3c) and a persistent Cloudflare challenge ends the run `failed` with `cloudflare_challenge` — no crash, no partial-row corruption.
- [ ] Manual upload and drop-folder ingestion still work; ZIP is moved to `processed/`; both share the parser and counts shape.
- [ ] `diary.csv` rows produce watches with correct `watched_date`, `rewatch`, tags; ratings land in `ratings` with source `letterboxd-import`; `likes/films.csv` rows appear as likes on the poster wall.
- [ ] RSS poll inserts a diary entry logged on letterboxd.com within one cycle (≤6 h), matched via `tmdb:movieId`, deduped by guid across restarts — including entries already imported by export **and** by scrape (cross-source dedup proven).
- [ ] Both members import cleanly and the shared wall shows correct per-user badges for a film only one of them has seen, and for one both have seen.

Pipeline & UI:
- [ ] Unmatched queue UI can resolve and ignore rows (from CSV and scrape sources); resolved rows re-enter the normal pipeline.
- [ ] Poster wall renders 1,000+ films smoothly (infinite scroll), filters and both-user badges work; import progress banner shows source + stage and surfaces cascade fallbacks in plain language.
- [ ] JustWatch attribution visible wherever availability shows.
- [ ] All new endpoints refuse unauthenticated requests (interim token guard — [API.md](../API.md) note).
- [ ] All acceptance criteria in [PHASE-2-credentials.md §8](PHASE-2-credentials.md) pass.
