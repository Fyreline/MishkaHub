# Phase 6 — Streaming-Service Optimisation

Purpose: turn the recommender's output into money advice: aggregate where the couple's high-scoring recommendations actually live, then suggest **subscribing** to services that would unlock a lot ("many great films for you on X you don't pay for") and **dropping** services that earn their keep poorly ("Y holds almost nothing you'd watch"). Depends on [Phase 3](PHASE-3-recommender.md) scores + the `availability` cache and `subscriptions` table ([DATA_MODEL.md](../DATA_MODEL.md)); serves `GET /api/insights/services` ([API.md](../API.md)).

**Status: planned**

---

## 1. Inputs

| Input | Source |
|---|---|
| Household services + optional monthly cost | `subscriptions` (`monthly_cost_pence`, user-editable in settings UI) |
| Scored candidate pool (availability-**unfiltered**) | Phase 3 scoring run over the *wide* pool: for this feature the nightly job also scores films available on **any** major GB provider, not just subscribed ones — otherwise we could never see what we're missing |
| Film × provider × kind rows | `availability` cache (flatrate/free/ads only; rent/buy excluded — you don't subscribe to rentals) |
| Watch history recency | `watches` (for the "are we using it?" signal) |

The wide pool = union of discover sweeps for the subscribed set **plus** the candidate set of suggestible providers (default: Netflix 8, Amazon Prime Video 9, Disney Plus 337, Apple TV+ 350, Now TV 39, MUBI 11, Paramount+ 531, BFI Player 224 — configurable; free services are never "suggested subscriptions", they're just on). Provider ids verified 2026-07, see [PHASE-3 §3](PHASE-3-recommender.md).

## 2. Aggregation

Nightly, after scoring (profile = `together` primarily, plus per-user views):

```
top_N = top 100 candidates by score, availability-unfiltered, MMR-diversified
for each provider p in (subscribed ∪ suggestible):
    recs(p)      = top_N films with a flatrate/free/ads offer on p
    exclusive(p) = recs(p) not available on ANY currently-subscribed service
    metrics(p):
        n_recs       = |recs(p)|
        n_exclusive  = |exclusive(p)|
        mean_score   = mean score of exclusive(p)   (what p uniquely adds)
        cost         = monthly_cost_pence (if provided)
        cpgr         = cost / max(n_exclusive, 1)   # "cost per good rec", pence
        recent_use   = watches on films that were (at watch time) on p,
                       trailing 90 days               # approximation, see §5
```

## 3. Suggestion rules

Thresholds live in `settings` (`service_insight_thresholds`) so they're tunable without code:

| Suggestion | Rule (defaults) |
|---|---|
| **Consider subscribing to p** | p not subscribed ∧ `n_exclusive ≥ 8` ∧ `mean_score ≥ 0.6` — headline: "23 films you'd probably love are on Now TV; 19 of them are nowhere you already pay for." |
| **Consider dropping p** | p subscribed ∧ costs money (`cost > 0`) ∧ `n_exclusive ≤ 2` over the last **3 consecutive weekly snapshots** ∧ `recent_use = 0` in 90 days — persistence requirement stops churn-flapping from catalogue rotation. |
| **Rotation hint** (nice-to-have) | subscribe-candidate p has `n_exclusive ≥ 15`: "…enough for roughly a month — subscribe for a month, binge the list, cancel." |
| No-op | Free services (iPlayer, Channel 4, ITVX, STV Player) are never drop-suggested; anything failing both rules renders as plain stats, no advice. |

Weekly snapshots of `metrics(p)` are appended to `settings` (`service_insights_history`, capped 26 weeks) — the drop rule and the trend sparkline both read from it.

## 4. `GET /api/insights/services` response

```json
{ "generated_at": "2026-07-03T02:20:00Z", "profile": "together",
  "attribution": "Streaming availability by JustWatch",
  "subscribed": [
    { "provider_id": 8, "name": "Netflix", "monthly_cost_pence": 1299,
      "n_recs": 31, "n_exclusive": 14, "mean_score": 0.71,
      "cost_per_good_rec_pence": 93, "recent_use_90d": 11,
      "trend": [12, 14, 14], "suggestion": null },
    { "provider_id": 531, "name": "Paramount Plus", "monthly_cost_pence": 799,
      "n_recs": 4, "n_exclusive": 1, "mean_score": 0.44,
      "cost_per_good_rec_pence": 799, "recent_use_90d": 0,
      "trend": [2, 1, 1],
      "suggestion": { "kind": "drop", "saving_pence_pa": 9588,
        "headline": "1 film in your top 100 is only on Paramount+ — and you haven't watched it in 3 months." } } ],
  "suggestions": [
    { "provider_id": 39, "name": "Now TV", "kind": "subscribe",
      "n_exclusive": 19, "mean_score": 0.68,
      "headline": "19 strong picks are only on Now TV.",
      "preview_film_ids": [578, 949, 27205] } ] }
```

## 5. Honesty & caveats (documented in the UI's info popover)

- Availability is a 7-day-TTL snapshot of JustWatch data via TMDB — catalogues rotate; suggestions say "as of this week".
- `recent_use` is approximate: we know what was watched and what was on p *around* that time, not which service actually played it. Phrase as "we haven't seen anything from X lately", never as fact.
- Scores are the model's opinion; the panel always shows the underlying film lists so the humans can overrule the maths.
- **JustWatch attribution required** on this panel (availability-derived — [ARCHITECTURE.md](../ARCHITECTURE.md) §6).

## 6. Insights panel UI spec

Route `/insights` (tokens per [DESIGN.md](../DESIGN.md)):

- **Header row:** total monthly spend (mono), count of services, profile toggle (Me / Partner / Together).
- **Service cards** (one per subscribed service, `paper-mid` cards): logo + name + cost; big number = `n_exclusive` ("films only here, from your top 100"); `mean_score` as a small bar; 3-point trend sparkline; `recent_use` line; **drop suggestion** renders as a `kraft/oat` banner inside the card with the headline + "show the 1 film" expander (poster row) — advice in calm words, never red alarm styling.
- **Suggestion cards** (subscribe): clay-accented border, headline, poster strip of the top 5 exclusive picks (tap → detail drawer), "£/month" if known, rotation hint when applicable, dismiss ("not interested in Now TV" — suppresses 90 days via `settings`).
- **Empty/quiet state:** "Your line-up looks right: every service you pay for is earning its keep." (serif line per DESIGN voice).
- Footer: JustWatch attribution + "availability checked weekly" timestamp.

## 7. Acceptance criteria

- [ ] Nightly job produces `metrics(p)` for every subscribed + suggestible provider and appends the weekly snapshot (visible in `GET /api/insights/services.trend`).
- [ ] Wide-pool scoring includes films on non-subscribed suggestible providers (verify a known Now TV-only title appears when unsubscribed).
- [ ] Subscribe rule fires against a fixture pool crossing the thresholds; drop rule requires 3 consecutive qualifying snapshots (unit-tested on synthetic history).
- [ ] Free services never receive drop suggestions; providers with no cost entered show stats but no `cpgr`.
- [ ] Dismissing a subscribe suggestion suppresses it for 90 days.
- [ ] Panel renders per DESIGN tokens with JustWatch attribution and the caveats popover.
- [ ] Cost maths: `saving_pence_pa` and `cost_per_good_rec_pence` correct for fixture data.
