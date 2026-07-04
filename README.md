# Mishka Hub

A private, self-hosted movie recommendation site for two people — built to answer one
question: *what should we actually watch tonight, that we haven't already seen, on
something we already pay for?*

- **Frontend:** React + Tailwind, warm ivory/clay design, full dark mode, mobile-first
  (drag-to-tilt poster cards, full-page detail view on phones).
- **Backend:** local FastAPI + SQLite server. Talks to TMDB for metadata/posters/UK
  streaming availability, and to Letterboxd for watch/rating history.
- **Recommender:** classical ML only (scikit-learn) — no generative AI. Content-similarity
  ("more like this") plus a personalised per-user taste model (Ridge regression blended
  with a genre/keyword/cast prototype vector), scored and diversified (MMR) over a
  TMDB-discovered candidate pool of thousands of films.
- **The core rule everything respects:** recommendations are restricted to films *neither
  of us has watched, or haven't watched in the last year* (weighted so "never seen"
  outranks "seen a while ago"), and to streaming services we actually subscribe to —
  never rent/buy listings.

Mishka Hub is a household project named after the family cat theme (the two household
members show up in the UI by their cat nicknames).

## What's actually built

- **Letterboxd import** — full CSV export backfill for both people, ongoing sync via RSS
  polling, and a manual-resolution queue + auto-matcher for anything that couldn't be
  confidently matched to a TMDB film.
- **Cat-alogue** — a dense, Letterboxd-style poster wall of everything watched, filterable
  by person/genre/decade/rating, with an in-app rating/liked/watched editor (Letterboxd
  stays the source of truth until you override something in-app).
- **"Something new to watch"** — a homepage row of unwatched/stale recommendations across
  different genres, filtered to what's actually streaming right now.
- **"Feeling Lucky"** — a single weighted-random pick, favouring never-seen films and
  increasingly favouring rewatches the longer it's been.
- **"More like this"** — pure content-similarity recommendations from any film, also
  restricted to unwatched-or-stale.
- **Where to watch** — real GB streaming availability (flatrate/free/ad-supported only,
  never rent/buy), filterable to a household-managed list of subscriptions, editable from
  a Settings page in the app.
- **Dark mode**, and a full mobile pass (44px tap targets, full-page detail card,
  scroll-locked overlay with a progressive blur header).

See [docs/PLAN.md](docs/PLAN.md) for the roadmap and documentation index — the full
planning suite lives in [docs/](docs/): [architecture](docs/ARCHITECTURE.md),
[data model](docs/DATA_MODEL.md), [API reference](docs/API.md), [design system](docs/DESIGN.md),
[deployment](docs/DEPLOYMENT.md), and per-phase implementation plans in
[docs/phases/](docs/phases/) (phase docs are kept up to date with what actually shipped vs.
what's deliberately scoped down or deferred).

## Run it locally

1. **Backend** (one-time): see [apps/server/README.md](apps/server/README.md) to create the
   venv, install deps, and paste a free [TMDB key](https://www.themoviedb.org/settings/api)
   into `apps/server/.env`.
2. **Both servers together:**
   ```bash
   chmod +x scripts/dev.sh   # first time only
   scripts/dev.sh            # backend :8000 + web :5173, Ctrl-C stops both
   ```
3. Open http://127.0.0.1:5173

This is a household tool, not a public product — there's no real auth yet (an interim
bearer token gates the API), accounts are hardcoded to the two household members, and the
database holds two real people's real watch history. `data/`, `backups/`, `reference/`, and
every `.env` are gitignored on purpose.

## Status

Well past the original Phase 1 scaffold — Letterboxd import, the Cat-alogue, in-app
rating/watch editing, real streaming availability, the personalised recommender, dark mode,
and a full mobile pass are all shipped and verified against the real household data. See
[docs/PLAN.md](docs/PLAN.md) and [docs/phases/](docs/phases/) for exactly what's done vs.
still planned (accounts/auth, Letterboxd write-back, and further service-optimisation work
are the main things still ahead).
