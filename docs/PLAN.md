# Mishka Hub — Architecture & Plan

## What we're building

A private movie-recommender for two people. A clean web app (hosted on GitHub Pages)
talks to a small server running on a home PC. The server:

1. Imports what we've watched from Letterboxd.
2. Runs **classical machine learning** (not generative AI) to recommend films we haven't seen.
3. Filters recommendations to only the streaming services / TV channels we own, in our
   region (UK / Scotland).
4. Gets better over time as we rate, like, and give feedback.
5. (Optionally) logs films back to Letterboxd when we mark them watched.
6. (Later) serves movies we own from local disk to the webOS TV.

## High-level architecture

```
  ┌─────────────────────────┐        HTTPS         ┌──────────────────────────────┐
  │  GitHub Pages (static)  │  ─────────────────▶  │  Home PC — local server       │
  │  React + Vite + Tailwind│   via secure tunnel  │  FastAPI (Python)             │
  │  polished movie UI      │  ◀─────────────────  │  SQLite + scikit-learn/LightFM│
  └─────────────────────────┘                      │  TMDB client, Letterboxd sync │
                                                    └──────────────────────────────┘
                                                                   │
                                        ┌──────────────────────────┼───────────────────────┐
                                        ▼                          ▼                       ▼
                                   TMDB API                 Letterboxd                Local media
                              (posters, metadata,       (CSV export / RSS in;      (owned films → TV,
                               UK watch providers)        auto-log out)             later phase)
```

### Why this split
- **ML must run locally** (your requirement) → Python backend, since that's where the ML
  ecosystem lives (scikit-learn, LightFM, implicit, pandas).
- **Frontend on GitHub Pages** (your requirement) → static React app, no server cost, always
  available. It just needs a URL to reach the home PC.
- **The tunnel** solves the hard part: a GitHub Pages site is served over HTTPS, and browsers
  block HTTPS pages from calling a plain `http://localhost`. A tunnel (e.g. Cloudflare Tunnel)
  gives the home server a real HTTPS address reachable from our phones *and* the TV, anywhere.

## Data sources & keys

| Need | Source | Notes |
|------|--------|-------|
| Films we've watched + our ratings | **Letterboxd, via a fallback cascade**: automated official data export (Playwright, our own logins) → public-profile scrape → RSS feed → in-app manual | No API needed for reading. Export is authoritative and now automated on a schedule; scrape covers export breakage; RSS (~50 newest) keeps things current; in-app "seen it/rate" is the floor. Details: [PHASE-2](phases/PHASE-2-letterboxd-import.md). |
| Posters, cast, genres, keywords | **TMDB API** (free key) | Rich metadata that also powers the content-based ML features. |
| UK streaming availability | **TMDB `/watch/providers` (region GB)** | Powered by JustWatch. Tells us *which* service has a film. Must attribute JustWatch. |
| Logging films back to Letterboxd | Browser automation **or** one-click pre-filled pages | Official API won't approve this use case (see below). |

### Letterboxd constraint (important)
Letterboxd's official API is request-only and they **explicitly refuse access for
recommendation engines, data analysis, and personal projects** — which is us. So:
- **Reading** our history: via the account data export (automated with our own logins, with a
  public-page scrape and RSS as fallbacks — the [Phase 2 cascade](phases/PHASE-2-letterboxd-import.md)).
  No approval needed; export automation carries the same ToS caveat as write-back and sits behind
  the same acknowledgement gate ([credential store](phases/PHASE-2-credentials.md)).
- **Writing** (auto-logging watched/rating/like/review): must be done by *automating the
  website with our own login*, or by opening a pre-filled log page for us to confirm. This
  is a product decision (see open questions).

## The recommender (classical ML, phased)

- **Phase A — content-based:** build a feature vector per film from TMDB (genres, keywords,
  cast, director, decade, runtime, language, popularity). Recommend films similar to the ones
  we rated highly. Works from day one with zero feedback ("cold start").
- **Phase B — collaborative / hybrid:** as ratings accumulate, add matrix-factorisation
  (LightFM or `implicit`) so it learns latent taste, blended with content features. Two users,
  so we keep per-user profiles and a shared "watch together" mode.
- **Phase C — active learning:** the app asks the occasional targeted question ("rate these
  5", "A or B?") chosen to most improve the model, and folds answers back in.
- **Always filtered** by: not-yet-seen, available on a service we own, region GB.

## Accounts
Two users only. Simple email/password with hashed credentials in SQLite, per-user taste
profiles, plus a shared household view. Because the server is exposed via the tunnel, the API
requires auth on every request.

## Streaming-service optimisation (nice-to-have, later)
- We tell the app which services/channels we pay for + our region; it aggregates availability.
- "Coming soon" additions: TMDB doesn't provide reliable future dates, so this needs a
  separate feed/scrape — parked as a stretch goal.
- "You'd like a lot on Service X → consider subscribing" and "little for you on Service Y →
  consider dropping it": computed from how many high-scoring recommendations each service holds.

## Local media → TV (later phase)
- Index owned movie files on the PC/Windows desktop.
- Serve them to the webOS TV. Most robust path is DLNA/UPnP (webOS has a built-in player) or a
  Jellyfin-style server; to be decided when we get there.

## Build phases (proposed order)
1. **Scaffold** — repo structure, FastAPI server, React app, TMDB client, health check. ✅ done (see repo).
2. **[Import + display](phases/PHASE-2-letterboxd-import.md)** — Letterboxd import **cascade** (automated export → public scrape → RSS → in-app manual), TMDB matching, poster wall UI (the "Cat-alogue"). ✅ done — full CSV backfill for both people, RSS sync, manual-resolution queue with an auto-matcher, in-app rating/liked/watched editing (Letterboxd stays source of truth until overridden). Encrypted [credential store](phases/PHASE-2-credentials.md) built and used; the export-automation/scrape legs are real but documented as Cloudflare-blocked in practice — CSV export + RSS are the reliable path day to day.
3. **[Recommender v1](phases/PHASE-3-recommender.md)** — content-based + personalised recs, filtered to owned services + region GB. ✅ shipped: content-similarity ("more like this"), "Feeling Lucky", and a per-user taste model (Ridge + prototype blend, MMR-diversified) over a TMDB-discovered candidate pool (thousands of films, still growing). Everything respects "not seen, or not seen in ≥365 days." Evaluation harness (§8) and the nightly retrain scheduler (§6) are the main pieces still deferred.
4. **[Accounts + feedback](phases/PHASE-4-accounts-feedback.md)** — login (2 fixed users, JWT), ratings/likes, active learning. ⬜ not started — an interim bearer token gates the API today; rating/liked/watched feedback loops already work (Phase 2/3 above), just not behind real per-user auth yet.
5. **[Letterboxd write-back](phases/PHASE-5-letterboxd-writeback.md)** — Playwright auto-log + one-click fallback. ⬜ not started (in-app overrides exist; they don't sync back to Letterboxd yet).
6. **[Service optimisation](phases/PHASE-6-service-optimisation.md)** — subscribe/drop suggestions. 🟡 partially done — a Settings page now lets the household pick which streaming services they subscribe to (backed by `GET/PUT /api/settings/subscriptions`, `GET /api/providers`), which the whole availability/recommendation pipeline already respects. The "you'd benefit from adding/dropping service X" *suggestion* logic itself is still ⬜ not built.
7. **[Local media → TV](phases/PHASE-7-local-media-tv.md)** — Jellyfin to the webOS TV, "Play on TV". ⬜ not started.
8. **[Streaming "coming soon"](phases/PHASE-8-coming-soon.md)** (stretch) — arrivals & upcoming dates. ⬜ not started.

**Also shipped, cutting across phases:** dark mode (manual toggle, persisted, full app coverage) and a full mobile responsiveness pass (44px tap targets, full-page detail overlay with scroll-lock + progressive blur header, responsive poster grids).

## Homepage/UX pass (2026-07-04, checkpoint before the next round)

A second round of frontend work landed on top of the above, reshaping the homepage into its
current form:

- **Layout** — header is back to just brand + status/settings/theme. Below it: a large
  centered title, then a full-width live-autocomplete search (debounced, poster+title/year
  dropdown, no submit button — replaces the old submit-and-see-a-grid flow), then a divider,
  then straight into "Something new to watch" as the page's main section. The Cat-alogue
  itself is untouched throughout all of this, per standing instruction.
- **Recommendation filters** — `GET /api/recommendations` gained `genres` (AND-matched — a
  film must match every selected genre, not just one), `runtime_buckets`
  (`under95`/`95to120`/`121to180`/`over180`, OR-matched across whichever are selected), and
  `vibe` (reuses `vibes.py`'s existing tagging). In the UI: genres are one
  horizontally-scrollable row, runtime buckets + a "Vibe" dropdown sit on the row below.
- **Expand-in-place recommendation detail** — clicking a card in "Something new to watch"
  expands a horizontal panel below the row (not the Cat-alogue's overlay), showing the exact
  same data/controls as the normal detail view via two new shared modules
  (`useFilmDetail.ts`, `components/FilmDetailSections.tsx`, `components/StarRatingInput.tsx`)
  so the Cat-alogue's `DetailDrawer` and this new panel can never drift apart. A curly-bracket
  SVG connector links the expanded panel back to the poster it came from. Clicking that same
  poster again collapses it.
- **"Fix the match"** — a hidden-by-default "Wrong film?" link in the detail view lets the
  household search TMDB and re-point a wrongly-matched film's whole watch/rating/like/review
  history to the correct one (`POST /api/films/{id}/rematch`). Already used once for real: a
  "Wake Up Dead Man" diary entry that had matched an unrelated film instead of the Knives Out
  sequel.
- **Progressive loading** — `useFilmDetail` now fetches the core detail, availability, and
  "more like this" independently (previously detail+availability were bundled), each with its
  own skeleton, so parts of a card appear as their own data arrives rather than one block wait.
- **Animations** — both the Cat-alogue's `DetailDrawer` and the new expansion panel now
  fade/slide in and out via `motion`'s `AnimatePresence` instead of popping instantly.

**Resolved from that list, plus further polish (2026-07-04, second checkpoint):**
- **"Watch now" deep links** — TMDB has no true per-title deep link into a streaming service,
  so each provider now gets a same-service *search* URL instead of TMDB's own watch page
  (`PROVIDER_SEARCH_URL` lookup in `FilmDetailSections.tsx`, keyed by TMDB provider ID,
  verified against the real Netflix/Prime/Mubi/iPlayer/ITVX/Apple TV/STV search endpoints).
  A true per-title link isn't possible without a paid affiliate API, so this is the practical
  ceiling — acknowledged with the user as "a good in-between."
- **Backdrop contrast** — the scrim over the expansion panel's backdrop image was first
  over-corrected (too dark to see the frame), then tuned down to two soft `color-mix` wash
  layers plus a theme-aware `textShadow` glow on the title/genre/year text itself (keyed off
  `--color-paper-mid`, so it auto-adapts light/dark). Genre chips became solid filled pills so
  they stay legible over any frame; year/runtime/"Wrong film?" text bumped up a shade.
- **Brace connector** — rebuilt from a user-supplied hand-edited SVG path (real devtools
  tuning) into a generalized `bracePath(peakPercent)` function using duplicate-control-point
  bezier corners for a proper curly-brace shape (flat shoulders, a tight point at the peak,
  ends curling straight down). The apparent "clipping by the panel's rounded corners" turned
  out to be the connector's *own* SVG bounding box clipping itself — fixed with
  `overflow: visible` and a taller viewBox/rendered height, not a panel change.
- **Panel outline merges into the brace** — the expansion panel now has a `border-clay/60`
  outline on its sides and bottom only (no top border), so the brace visually flows straight
  into the box instead of the two elements looking separate.
- **New cat-mark icon** — replaced the old clapperboard mark with a simple two-eared cat-face
  SVG, used both as the header wordmark icon (`CatMark` in `App.tsx`, theme-aware via
  `currentColor`/CSS vars) and as `public/cat-icon.svg` (hardcoded colors, since favicons don't
  get CSS vars) wired into `index.html`. Verified legible down to 16px.
- **Genre filter bug (real)** — `GET /api/recommendations?genres=...` was matching genres by
  substring search against the *entire* raw TMDB metadata blob, so e.g. `Animation` matched
  Okja (no animation genre at all, just the word "animation" somewhere in its overview text).
  Fixed in `pipeline.py`'s `_passes_filters` to parse the real `genres` array out of
  `metadata_json` and require every selected genre to be an exact (case-insensitive) match.
  The Cat-alogue's own genre filter (`films.py`) has the same substring-search pattern but was
  deliberately left alone — the bug report was specifically about recommendations.
- **Genre row no longer scrolls** — switched to a smaller `FilterPill` size variant (`size="sm"`)
  and `flex-wrap` instead of a horizontally-scrolling row, so all 16 genres fit without a scrollbar.
- **"More like this" opens the full overlay** — clicking a poster inside "more like this" (within
  the inline recommendation expansion panel) now opens the same `DetailDrawer` overlay used
  elsewhere in the app, layered on top, instead of replacing the expansion panel's own content.
  `DetailDrawer` is exported from `Catalogue.tsx` for reuse; `RecommendationExpansionPanel` takes
  a separate `onOpenOverlay` prop distinct from `onNavigate` (which still drives "Wrong film?"
  in-place rematching).
- **Letterboxd CSV export link** — RSS only reliably captures logged/reviewed entries; a plain
  "mark as watched" tap on Letterboxd doesn't show up there. Added a card on the Settings page
  linking straight to `https://letterboxd.com/user/exportdata` so the household can periodically
  re-export and re-import the full, definitive history.
- **RSS ingestion capability** — `importers/rss.py`'s `poll_user()` gained an `override_items`
  param and a `parse_feed_text()` helper so already-downloaded RSS XML (e.g. a manually-exported
  `.rss` file) can be run through the same import path as the live poller, without re-fetching.
  Used once for real on two manually-provided feed exports — both came back as 0 new items,
  confirming the live 6-hour scheduler had already caught everything (a correctness check, not a bug).

**Still open, deliberately parked or in progress:**
- A true per-title streaming deep link (would need a paid affiliate/deep-link API) — search
  links are the accepted ceiling for now.
- Documentation/README pass beyond this checkpoint, a "streaming coming soon" tab, an "owned
  movies" tab (with owned films counting as recommendation candidates), and Jellyfin
  integration prep are queued as the next round of autonomous work.
- Vibe-model improvement (possibly folding genre into the vibe scoring itself) is flagged as a
  larger ask needing its own scoping pass.

**Backlog beyond the current round** (unchanged from the phase table above, restated here for
one-stop visibility): real per-user accounts/auth (Phase 4, currently an interim bearer token),
Letterboxd write-back (Phase 5), the "you'd benefit from adding/dropping service X" suggestion
engine (Phase 6 — the settings *page* to change subscriptions is done, the *suggestion logic*
is not), local media → TV (Phase 7), and streaming "coming soon" dates (Phase 8, stretch). Also
still deferred from Phase 3: the temporal-holdout evaluation harness (§8) and a nightly
retrain scheduler (§6) — recompute-per-request remains fine at the current corpus size.

## Documentation index

Implementation-ready reference docs (each self-contained, with acceptance criteria):

| Doc | Contents |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | components, data flows, auth model, tunnel/CORS topology, failure modes, attribution rules |
| [DATA_MODEL.md](DATA_MODEL.md) | full SQLite DDL, indexes, migrations strategy (Alembic) |
| [API.md](API.md) | every REST endpoint by phase: auth, request/response examples, error shapes |
| [DESIGN.md](DESIGN.md) | design system: Anthropic-style tokens (verified palette/type), Letterboxd-density poster grids, 3D poster-drag physics |
| [DEPLOYMENT.md](DEPLOYMENT.md) | GitHub Pages workflow, Cloudflare named tunnel on macOS, launchd services, SQLite backups |
| [phases/PHASE-2 … PHASE-8](phases/) | one implementation plan per build phase (linked above) |
| [phases/PHASE-2-credentials.md](phases/PHASE-2-credentials.md) | shared Letterboxd credential store (macOS Keychain + Fernet) used by Phases 2 & 5 |

## Locked decisions
- [x] **Letterboxd history import:** fallback cascade — (1) automate the official data export
      with our own logins (Playwright), (2) scrape our public profile pages when that fails,
      (3) RSS polling for incremental freshness, (4) in-app "seen it / rate" as the always-available
      backstop. Spec: [PHASE-2](phases/PHASE-2-letterboxd-import.md). Consequence: the encrypted
      credential store is built in Phase 2 as a [shared module](phases/PHASE-2-credentials.md)
      (not duplicated in Phase 5).
- [x] **Letterboxd write-back:** auto-log via browser automation (Playwright) using our own
      login. Credentials in the macOS Keychain via the shared store above. (Note: account
      automation is against Letterboxd's ToS and can break if their site changes — accepted
      trade-off for hands-off logging, acknowledged per user in the UI.)
- [x] **Connectivity:** Cloudflare Tunnel — gives the home PC a stable HTTPS URL reachable by
      our phones and the webOS TV from anywhere. API is auth-protected on every request.
