# Phase 8 (stretch) — "Coming Soon to Your Services"

Purpose: surface films that will soon be *added to* the household's UK streaming services ("Heat hits Netflix UK on the 14th") and, ideally, near-future digital release dates for watchlisted films. This is a stretch goal because — as locked in [PLAN.md](../PLAN.md) — **TMDB does not provide "coming soon to streaming" data**; its `/watch/providers` reflects *current* availability only. This doc assesses candidate sources honestly and sketches a design that degrades gracefully.

**Status: v1 shipped as a much smaller subset (2026-07-04), Tiers 1-3 below still planned.**
A "Coming soon" tab (`GET /api/upcoming`, `TMDBClient.upcoming_movies`) ships theatrical GB
release dates straight from `/movie/upcoming` — no personalisation, no streaming-arrival data,
no new table/job. It exists specifically because the three tiers below all require a weekly
snapshot-diff job and a new `coming_soon` migration that weren't built in this round; this is
the zero-new-infrastructure stopgap, clearly labelled in the UI as cinema dates rather than
implying a streaming date. Tiers 1-3 remain the real target design.

---

## 1. Candidate data sources (assessed 2026-07)

| Source | What it gives | Access | Feasibility |
|---|---|---|---|
| **TMDB `/movie/{id}/release_dates`** ([docs](https://developer.themoviedb.org/reference/movie-release-dates)) | Per-region dated release events; **type 4 = Digital** — a future GB type-4 date ≈ "hits digital platforms then" (which platform is not stated) | already have it (fetched in Phase 2 hydration) | ✅ **usable now**, partial answer |
| **JustWatch partner API** ([apis.justwatch.com/docs/api](https://apis.justwatch.com/docs/api/)) | exactly what we want: upcoming per-provider, per-country | **B2B/partner only** — a private 2-person app won't get a key | ❌ assume unavailable |
| **JustWatch unofficial GraphQL** (used by e.g. [simple-justwatch-python-api](https://github.com/Electronic-Mango/simple-justwatch-python-api)) + [justwatch.com/uk/new](https://www.justwatch.com/uk/new) & [/uk/provider/netflix/upcoming](https://www.justwatch.com/uk/provider/netflix/upcoming) pages | full upcoming lists per GB provider | unofficial: no key, but unsanctioned; endpoint/DOM can change or be blocked; ToS-grey | ⚠️ workable, fragile — same risk class as Phase 5, but **read-only, no account** |
| Per-service editorial feeds (Netflix Tudum, press blogs, "new on X UK" sites) | monthly announcement lists, title-level | scraping heterogeneous HTML | ❌ high effort, low structure |
| **Re-check loop on our own data** (no new source) | "appeared on your services this week" — diff the weekly `availability` snapshots we already store ([Phase 6](PHASE-6-service-optimisation.md)) | free | ✅ trivially feasible; it's "just arrived", not "coming soon" |

⚠️ Marked assumption: JustWatch partner-API inaccessibility for personal projects is inferred from its B2B framing ("for partners", integration docs aimed at streaming services); we have not applied and been refused. If access were somehow granted, it becomes the sole source and this doc simplifies dramatically.

## 2. Recommendation

Ship in three tiers, in order, each independently useful:

1. **Tier 1 — "Just arrived" (no new dependencies):** weekly diff of availability snapshots for the top-500 scored pool + entire watchlist → "New on your services this week" rail. Zero fragility. Do this first; it may be enough.
2. **Tier 2 — TMDB digital dates:** for watchlisted + top-pool films with no GB streaming offer, read cached `release_dates` type-4 GB entries; future-dated → "expected on digital ~<date>" (platform unknown). Refresh weekly.
3. **Tier 3 (optional, behind a settings flag, default off):** unofficial JustWatch GraphQL query per subscribed provider (`upcoming`, country GB), matched to TMDB ids (JustWatch exposes them). Same engineering discipline as Phase 5: isolated client module, kill switch, graceful absence when it breaks. Accept it may die at any time.

## 3. Design sketch

```
weekly job:
  snapshot_diff  = availability(t) − availability(t−1)        # Tier 1
  digital_dates  = future type-4 GB dates for pool+watchlist   # Tier 2
  upcoming_jw    = if enabled: JustWatch upcoming per provider # Tier 3
  → table coming_soon(film_id, provider_id NULL-able, kind
    'arrived'|'digital_date'|'announced', date, source, first_seen_at)
    (added by migration in this phase)

UI: "Coming soon" rail on the home screen, grouped:
  This week on your services (Tier 1, posters + provider logo)
  Dated (Tier 2/3: "≈ 12 Aug · digital" / "14 Aug · Netflix")
  Watchlist alerts: a watchlisted film gaining a date/arriving → toast + prompt card
Recommender hook: candidate scoring adds a small 'imminent' boost so
  "arrives Friday" films can headline date-night planning.
```

Attribution: Tier 1/3 are JustWatch-derived → the rail carries the JustWatch line ([ARCHITECTURE.md](../ARCHITECTURE.md) §6).

## 4. Acceptance criteria

- [ ] Tier 1: a film newly appearing on a subscribed service shows in "new this week" after the weekly job (fixture-tested on synthetic snapshots).
- [ ] Tier 2: a watchlisted film with a future GB digital date renders "expected ~date"; past dates never show.
- [ ] Tier 3 (if enabled): upcoming titles for one provider fetched, TMDB-matched ≥90 %, and the feature silently disappears (no errors surfaced) when the endpoint breaks.
- [ ] Watchlist alert prompt fires once per film per event kind.
- [ ] Rail hidden entirely when all tiers are empty — no zombie UI.
