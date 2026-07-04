# Phase 3 — Recommender v1 (content-based → hybrid)

Purpose: the heart of Mishka Hub. Classical ML (no generative AI) that turns TMDB metadata + the couple's Letterboxd history into ranked, **availability-filtered** recommendations per user and for "watch together", with novelty, diversity, cold-start behaviour, an honest evaluation plan, and artefact/retrain mechanics. Depends on [Phase 2 data](PHASE-2-letterboxd-import.md); serves the endpoints in [API.md](../API.md) §Phase 3.

**Status: §1-2 (feature engineering + cosine similarity) shipped early as "recommender v0" — real, live, verified against the real household library — serving `GET /api/films/{id}/similar` and `GET /api/films/lucky` ("Feeling Lucky", weighted by watch-staleness; contract in [API.md](../API.md) §Phase 3).**

**Recommender v1 (2026-07-04) — §3-5, §7 now SHIPPED and live, verified against the real household DB.** `GET /api/recommendations` (`profile=me|partner|together`), `GET /api/recommendations/{film_id}/why`, `POST /api/model/retrain`, `GET /api/model/status` are all real. Implementation: `app/recommender/candidates.py` (§3 candidate gen), `taste.py` (§4 prototype+RidgeCV blend), `scoring.py` (§5 formula + together blend + MMR + eligibility), `artifacts.py` (§6 storage, scoped), `pipeline.py` (orchestration), `app/routers/recommendations.py` (§7 serving). One new `TMDBClient.discover_movies`/`movie_recommendations` method pair added for §3. Recommendations respect the SAME "not seen, or ≥365 days since last watch" eligibility rule as `/lucky` (task #18) — verified zero violations for me/partner/together.

**Corpus expansion + `/similar` eligibility fix (2026-07-04, later same day).** Two real gaps found in live use: (1) `GET /api/films/{id}/similar` (v0, `features.py`) had **zero** watched-status filtering — it compared against the whole local library regardless of whether either person had already seen the result, so "more like this" surfaced mostly-already-watched films. Fixed: `lucky.py` now exposes a shared `eligible_film_ids_for_user()` (same canonical staleness rule `pick_lucky_film` uses), and `films.py:get_similar_films` filters its comparison corpus to films eligible for at least one of the two users before scoring (the seed film itself is always kept as the comparison anchor, even if already watched). (2) The candidate pool was still too small for real use — user 2 had already watched 947 of the 1471 films in the corpus, leaving "together" recommendations thin. `candidates.py`'s `DISCOVER_PAGES` raised **12 → 60**. Real before/after, verified via `sqlite3`/`curl` against the production DB: corpus **1471 → 3735 films**, user 1 now 135 ratings / 216 distinct films watched (Ridge+prototype, λ=0.8), user 2 now 23 ratings / 990 distinct films watched (prototype-only cold-start path, λ=0). Spot-checked `/films/155/similar` (The Dark Knight) post-fix: every returned film has zero watch rows for either user in the real `watches` table.

**Deferred from v1 (documented scope-down, not silently skipped):**
- **§3 depth** — candidate gen now sweeps `DISCOVER_PAGES=60` (~1200 discover films) + top-10-rated `/recommendations` per user — still short of the doc's ~40-pages-plus-a-second-`vote_average.desc`-sweep target, but a real 5x jump from the original 12-page cap. `candidates.py` constants; bump further for the nightly job.
- **§6 versioning/scheduler** — one `model_artifacts` row (`kind='taste_model'`, `is_active=1`) + one joblib bundle per retrain under `data/models/<version>/taste.joblib`; the atomic is_active flip is real. NOT done: separate `feature_matrix`/`vocab` artefact rows, keep-last-5 pruning, nightly retrain scheduler, debounced feedback-triggered retrain. Recompute-per-request is used instead (fast enough at ~3.7k films). `recommendations_cache` table is unused — recs compute synchronously per request.
- **§8 evaluation harness** — temporal-holdout hit-rate@k eval NOT built. `GET /api/model/status` reports real counts (ratings/user, corpus size, blend, last-trained) instead of eval metrics. Real follow-up.

**Homepage surface (2026-07-04).** `GET /api/recommendations` is now also consumed directly by the homepage's "Something new to watch" row (`apps/web/src/App.tsx`'s `UnseenRecommendationsRow`) — a single row of unwatched/stale picks with its own `me`/`partner`/`together` toggle, rendered at the same responsive column counts as the Cat-alogue grid. No new backend surface was needed for this; it's a pure frontend consumer of the existing endpoint.

What's below describes the full target design; the v1 implementation lives in the modules named above (v0's cosine-similarity code stays in `features.py` + `vibes.py`).**

## §0. What v0/v0.5 actually built (read this first — it's real, not aspirational)

- `features.py`: `extract_features()` pulls genres/keywords/cast/director/decade/runtime/language from `films.metadata_json` exactly per §2's block design below, with one deviation: keyword TF-IDF uses `min_df=1` (not the `min_df=3` below) since v0's candidate pool is the local library (~1,000 films after the real household backfill), not the ~50k-film pool §3 describes — `min_df=3` would gut the vocabulary at this size. Revisit once §3's candidate pool ships.
- `similar_films()`: cosine similarity against every other locally-known film, no personalisation, no availability filtering yet (that's `/availability`'s job, called separately by the UI) — this is deliberately §1-2 only, not §4/§5's scoring formula.
- `vibes.py`: `vibe_tags()` — an honest, partial-coverage heuristic (TMDB keyword substring matches for slow_burn/feel_good/sad/tense/dark; `quick_watch` is exact, from `runtime_min<=95`) used to filter both `/similar` and `/lucky`.
- `GET /api/films/lucky`: implemented in `app/recommender/lucky.py` (`pick_lucky_film()`, kept separate from `features.py` since it needs no cosine similarity/FeatureSpace — it reuses only `vibe_tags()` for the `vibe` filter). Weighted-random single-film pick, eligibility = never-watched-by-this-user OR ≥365 days since last watch (hard filter below that), weight ramping with staleness — see [API.md](../API.md) for the exact formula. Edge case: if a user's only watch record(s) for a film are undated (`watched_date IS NULL`, common in the real Letterboxd backfill — 1,053 of 1,158 watch rows in the live household DB are undated), that film is excluded from the pool rather than guessed as never-watched or stale — see API.md. This is the "watch it again" rule the household asked for, applied without needing §4's taste model at all (it's a staleness/randomness mechanic, not a personalisation one).
- Both endpoints auto-hydrate an unknown TMDB id into `films` on first access (shared with `GET /films/{id}`), so the pool organically grows as the household searches/browses.

---

## 1. Library decision: scikit-learn primary; `implicit` optional; LightFM ruled out

With **two users**, classical user-user/item-item collaborative filtering has no signal to mine — you cannot factorise a 2×N matrix into meaningful latent user taste beyond what two independent per-user models already give you. The value of "hybrid" here is: **rich item features + a per-user preference model over those features**, plus (optionally) item-item latent structure learned from the items themselves. That reframing drives the library choice:

| | LightFM | `implicit` (benfred) | scikit-learn |
|---|---|---|---|
| Model | hybrid MF with user/item features | ALS/BPR on interaction matrix only | anything: linear models, SVD, neighbours |
| Fit for 2 users | its hybrid mode degenerates to per-user linear scoring of item features — i.e. what Ridge gives us directly | needs many users for meaningful factors → **no value here** | per-user Ridge/Logistic over item features is exactly the right shape |
| Python 3.12 (our locked runtime) | **install fails**, [issue #709](https://github.com/lyst/lightfm/issues/709) open since 2024 | ✅ cp312 wheels ([PyPI](https://pypi.org/project/implicit/)) | ✅ first-class |
| Maintenance (2026-07) | last release 1.17 (2023), effectively abandoned ([PyPI](https://pypi.org/project/lightfm/)) | low activity but releases exist (0.7.3) | very active |
| **Verdict** | ❌ ruled out | ⚠️ optional later (item-item factors from external data) | ✅ **primary** |

**Decision:** scikit-learn end-to-end. Feature extraction (`TfidfVectorizer`, `FeatureHasher`), per-user taste models (`Ridge` on centred ratings + prototype cosine fallback), optional `TruncatedSVD` for dense item embeddings. `implicit` is reserved as a future enhancement only if we ever import an external interaction dataset (e.g. MovieLens) to learn item-item factors as *additional item features* — explicitly out of scope for v1.

## 2. Feature engineering (from `films.metadata_json`)

One sparse row vector per film, built from the TMDB payload fetched in Phase 2 (`/movie/{id}?append_to_response=credits,keywords,release_dates`). Blocks, each L2-normalised then scaled by a block weight (weights are hyperparameters, tuned in evaluation §8):

| Block | Encoding | Dim | Weight (start) |
|---|---|---|---|
| Genres | one-hot over TMDB's ~19 movie genres | ~19 | 1.0 |
| Keywords | TF-IDF, vocab capped at **2,000** terms (min_df=3, sublinear tf) | ≤2000 | 1.0 |
| Cast (top 5 billed) | `FeatureHasher` (signed) on `cast:{person_id}` | 256 | 0.5 |
| Director(s) | `FeatureHasher` on `director:{person_id}` | 128 | 0.7 |
| Decade | one-hot (`1950s`…`2020s`, clamp ends) | ~10 | 0.4 |
| Runtime bucket | one-hot: <90, 90–110, 110–140, 140–180, >180 min | 5 | 0.3 |
| Original language | one-hot top-15 languages + `other` | 16 | 0.4 |
| Quality/popularity priors | 2 scalars: Bayesian-smoothed vote mean `(v·R + m·C)/(v+m)` with m=500, C=global mean; and `log10(1+popularity)`, both min-max scaled | 2 | kept **outside** similarity — used only as the ranking prior (§5) |

Notes:
- Hashing (not vocab) for people: the people space is huge and open-ended; 256/128 signed dims keep the matrix small and stable across retrains. Collisions are acceptable noise at this scale.
- Keywords vocab is rebuilt at each full retrain from films *the household has interacted with plus the current candidate pool*, then frozen into the artefact (`vocab` JSON) so scoring is reproducible.
- Full matrix ≈ 2.4k dims sparse — trivial for scipy/sklearn at 50k films.

## 3. Candidate generation

Candidates = films the couple **hasn't seen**, **available on subscribed services**, region GB.

1. Read household provider ids from `subscriptions` (TMDB GB ids verified 2026-07 from TMDB's own GB provider list — full enumeration endpoint: [`GET /watch/providers/movie?watch_region=GB`](https://developer.themoviedb.org/reference/watch-providers-movie-list)):

   | Provider | id | | Provider | id |
   |---|---|---|---|---|
   | Netflix | 8 | | ITVX | 41 |
   | Amazon Prime Video | 9 | | ITVX Premium | 2300 |
   | Disney Plus | 337 | | STV Player | 593 |
   | Apple TV+ (subscription, listed as "Apple TV") | 350 | | Now TV | 39 |
   | BBC iPlayer | 38 | | Sky Go | 29 |
   | Channel 4 | 103 | | MUBI | 11 |
   | *(rent/buy, excluded from "subscribed" filtering)* | | | Amazon Video 10 · Apple TV Store 2 · Rakuten TV 35 · Sky Store 130 | |

2. Page through [`GET /discover/movie`](https://developer.themoviedb.org/reference/discover-movie) with `with_watch_providers=8|337|38|…` (pipe = OR), `watch_region=GB`, `sort_by=popularity.desc`, `vote_count.gte=50`, `include_adult=false` — first ~40 pages (≈800 films), plus a second sweep sorted by `vote_average.desc` with `vote_count.gte=500` for catalogue depth.
3. Add "similar to loves": [`GET /movie/{id}/recommendations`](https://developer.themoviedb.org/reference/movie-recommendations) for each user's top-20 rated films (catches low-popularity gems discover misses); intersect with availability afterwards via `/movie/{id}/watch/providers`.
4. Drop anything in `watches` for the target profile (for `together`: seen by **either** user, so date night is new to both — films one partner has seen can be resurfaced later behind a UI toggle `include_unavailable`-style flag if wanted); drop active `not_interested` events (snoozes expire after 90 days).
5. Hydrate metadata + `availability` cache rows (TTL 7 days) for survivors.

Pool refresh: nightly job; typical pool 1–2k films.

## 4. Per-user taste model

Two models per user, blended by data volume:

**(a) Prototype vectors (works from ~1 rating; cold-start backbone).**
- Positive prototype `p⁺ᵤ` = weighted mean of feature vectors of films rated ≥ user's own mean (weight = rating − mean + 0.5, likes add +0.75, recency decay `exp(−Δdays/1095)` on watched date so taste drifts with you).
- Negative prototype `p⁻ᵤ` from films rated ≤ 2.0 (weight = 2.5 − rating).
- `proto_score(u, i) = cos(xᵢ, p⁺ᵤ) − 0.5·cos(xᵢ, p⁻ᵤ)`.

**(b) Ridge regression (kicks in ≥ 30 ratings).**
- Target `y = rating − user_mean`; features = the film vector; `Ridge(alpha)` with alpha from leave-one-out CV (`RidgeCV`, efficient closed form).
- `ridge_score(u, i) = ŷᵤ(xᵢ)` squashed to [0,1] via min-max over the pool.
- Why Ridge over Logistic: ratings are ordinal, data is tiny; L2 keeps the 2.4k-dim weights sane; coefficients are directly inspectable ("you overweight: A24-era horror keywords, 1970s, Gene Hackman") which powers the `why` endpoint.

**Blend:** `taste(u,i) = λ·ridge + (1−λ)·proto`, `λ = clip((n_ratings − 30)/120, 0, 0.8)` — smooth handover, prototypes never fully switched off (they encode likes + recency that Ridge's snapshot misses).

## 5. Scoring & ranking

For user u and candidate i:

```
score(u,i) = 0.55 · taste(u,i)                         # personal fit (§4)
           + 0.20 · quality_prior(i)                   # Bayesian vote mean (§2)
           + 0.15 · novelty(u,i)
           + 0.10 · availability_boost(i)

novelty(u,i)         = 1 − max_{j ∈ seen(u)} cos(xᵢ, xⱼ)   # capped at the 95th pctile
                       (pushes beyond comfort-zone lookalikes; UI 'novelty' param
                        rescales this weight 0–2×)
availability_boost(i) = 1.00 flatrate/free on a subscribed service
                        0.60 ads tier · 0.0 otherwise (hard-filtered anyway unless
                        include_unavailable=true, where rent/buy score 0.2)
```

**"Watch together" blend:** least-misery with a mean nudge —
`score(together, i) = 0.7·min(score(u₁,i), score(u₂,i)) + 0.3·mean(...)`.
Min-dominant because one bored partner ruins date night; the mean term breaks ties toward mutual enthusiasm. Both per-user scores are exposed in the `why` payload so the UI can show "92 % you / 78 % them".

**Diversity (MMR):** greedy max-marginal-relevance over the top-200 by score:
`MMR = argmaxᵢ [ 0.7·score(i) − 0.3·max_{j∈selected} cos(xᵢ,xⱼ) ]` → final top-50 per profile, written to `recommendations_cache` with per-term components for the UI.

**Cold start (day one, zero in-app feedback):** prototypes built purely from imported Letterboxd ratings; if a user somehow has none, fall back to `quality_prior + popularity` within subscribed services with genre diversity enforced by MMR — still useful, clearly labelled "getting to know you" in the UI.

## 6. Model artefacts & persistence

```
data/models/
└── 2026-07-03T0200Z/            # version = retrain timestamp
    ├── features.npz             # scipy sparse matrix, rows aligned to film_ids.json
    ├── film_ids.json
    ├── vocab.json               # tfidf vocab + idf, hashing config, block weights
    ├── user_1/ridge.joblib      # + prototypes.npz, meta.json (n_ratings, λ, user_mean)
    ├── user_2/…
    └── metrics.json             # eval results at train time (§8)
```

Each retrain writes a **new version directory**, then flips `model_artifacts.is_active` in one transaction (see [DATA_MODEL.md](../DATA_MODEL.md)); serving code loads active-version artefacts at startup and after retrain (in-process reload). Old versions: keep last 5, prune the rest (nightly).

**Retrain triggers:**
1. Nightly at 02:00 local (with candidate-pool + availability refresh).
2. Debounced: ≥5 new `feedback_events` since active version → retrain within 10 min (fast: whole pipeline is seconds at this scale).
3. Manual: `POST /api/model/retrain`.
Taste models retrain on every trigger; the feature matrix/vocab rebuild only nightly (vocab churn intra-day isn't worth it).

## 7. Serving

`GET /api/recommendations` reads `recommendations_cache` (already availability-filtered and MMR-diversified), applies request-time narrowing (`providers=`, `genre`, `max_runtime`), and returns instantly. Only `include_unavailable=true` or an empty cache triggers synchronous scoring. Contract + examples: [API.md](../API.md).

## 8. Evaluation plan

Honest offline eval on the couple's own history — small data, so treat metrics as directional, not gospel:

- **Temporal holdout:** train on all ratings/watches with log date < T (e.g. 6 months ago), test on what was actually watched after T. No random splits — leakage lies.
- **Metrics per user + together:**
  - `hit-rate@k` (k = 10, 20, 50): fraction of test-period watched films that appeared in the top-k recs generated from train-period data (availability filter **off** for eval — we can't reconstruct historical catalogues).
  - `mean test rating of hits` vs `mean test rating overall` (are we surfacing the ones they *liked*?).
  - rating prediction MAE of the Ridge model (sanity check, not a target).
- **Baselines to beat:** popularity-only (quality prior), and genre-matched popularity. If content features don't beat these, the block weights need work before shipping.
- **Harness:** `python -m app.cli evaluate --holdout-months 6` prints a table and writes `metrics.json`; run at every retrain so `GET /api/model/status` exposes drift.
- **Online proxy (post-launch):** weekly count of `clicked` / `watchlisted` / `seen` events attributable to `context='rec'`, shown in the settings page — the couple's actual behaviour is the real metric.

## 9. New dependencies

`scikit-learn>=1.5`, `scipy`, `numpy`, `pandas` (eval/reporting only), `joblib`. All pure-wheel installs on Python 3.12/macOS.

## 10. Acceptance criteria

- [ ] Nightly job builds feature matrix over pool + history (≥95 % of films featurised without error) and writes a versioned artefact dir with metrics.json.
- [ ] `GET /api/recommendations?profile=me` returns 50 ranked films: none seen by me, all flatrate/free/ads on a subscribed service, region GB.
- [ ] `profile=together` returns films neither partner has seen; `why` payload carries both per-user scores and the blend formula.
- [ ] `why` breakdown sums (within rounding) to the score, and the top contributing features are human-readable.
- [ ] `not_interested` feedback removes the film from the next cache build; a new 5-star rating visibly shifts recs after the debounced retrain (≤10 min).
- [ ] Temporal-holdout eval runs via CLI; content model beats the popularity baseline on hit-rate@20 for at least one user (document result either way).
- [ ] MMR: no more than 3 of any top-10 share a primary genre pair (spot-check).
- [ ] Cold-start path (user with zero ratings) returns labelled quality-prior recs, no errors.
- [ ] Retrain flips versions atomically; serving never reads a half-written artefact (kill-test during retrain).
