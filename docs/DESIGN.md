# Mishka Hub — Design System

Purpose: the visual + interaction contract for the Mishka Hub web app. Direction: **Anthropic's editorial warmth crossed with Letterboxd's poster density** — calm ivory surfaces, near-black ink, one clay accent, generous whitespace around **dense, tactile poster grids** that tilt and flip in 3D under your thumb. This doc defines the tokens (drop-in Tailwind v4 `@theme` for `apps/web/src/index.css`), component specs, grid specs, and the mobile poster-drag physics. UI specs in the phase docs ([Cat-alogue poster wall](phases/PHASE-2-letterboxd-import.md) §10, [insights panel](phases/PHASE-6-service-optimisation.md)) inherit from here.

**Status: planned**

> ⚠️ **This replaces the current scaffold theme.** The Phase-1 scaffold is dark indigo/glassy (`bg-indigo-500`, `bg-black/30 backdrop-blur`, white-on-black). That was placeholder styling. Adopting this doc means re-skinning `App.tsx`, `MovieCard.tsx`, and `index.css` — no indigo, no glassmorphism, light-first.

---

## 1. Anthropic aesthetic — verified source

Tokens below are extracted from Anthropic's live brand stylesheet (fetched 2026-07-03): [anthropic.com](https://www.anthropic.com) → [`ant-brand.shared.….min.css`](https://cdn.prod.website-files.com/67ce28cfec624e2b733f8a52/css/ant-brand.shared.67f55a9666b2ae2332f5f893.587c16f3e.min.css). Their system in one line: **ivory paper, slate ink, clay accent, hairline borders instead of shadows, 8px-family radii, roomy spacing, geometric sans with a serif display accent.**

### 1a. Verified palette (Anthropic swatch → Mishka Hub token)

| Anthropic swatch | Hex | Mishka Hub token | Role |
|---|---|---|---|
| `ivory-light` | `#faf9f5` | `paper` | page background |
| `ivory-medium` | `#f0eee6` | `paper-mid` | secondary surfaces, cards |
| `ivory-dark` | `#e8e6dc` | `paper-deep` | hover state of secondary surfaces |
| `slate-dark` | `#141413` | `ink` | primary text, primary buttons |
| `slate-medium` | `#3d3d3a` | `ink-mid` | subheads, icons |
| `slate-light` | `#5e5d59` | `ink-soft` | secondary text, link hover |
| `slate-faded-10` | `#1414131a` | `line` | hairline borders (1px) |
| `slate-faded-20` | `#14141333` | `line-strong` | focus-adjacent borders, dividers |
| `clay` ("book cloth") | `#d97757` | `clay` | THE accent: primary actions, active states, brand moments |
| `accent` | `#c6613f` | `clay-deep` | clay hover / pressed / links on paper |
| `kraft` | `#d4a27f` | `kraft` | warm tertiary chips, illustration |
| `oat` | `#e3dacc` | `oat` | tonal fills (pills, banners) |
| `cloud-medium` | `#b0aea5` | `cloud` | disabled text/borders |
| `olive` | `#788c5d` | `olive` | success, "on your services", positive deltas |
| `sky` | `#6a9bcc` | `sky` | informational, partner-user accent |
| `fig` | `#c46686` | `fig` | likes/hearts, "watch together" flourishes |
| `heather` `#cbcadb` · `cactus` `#bcd1ca` · `coral` `#ebcece` · `manilla` `#ebdbbc` | — | reserved | data-viz categorical ramp (Phase 6 charts) |

Semantic mapping (mirrors Anthropic's own theme vars): background=`paper`, background-secondary=`paper-mid` (hover `paper-deep`), text=`ink`, border=`line`, primary button = `ink` bg + `paper` text (their `button-primary--text: ivory-light`).

**Dark variant (optional, evening/TV mode — defined now, built later):** invert within the same family: bg `#141413`, surface `#1f1e1c`, text `#faf9f5`, secondary text `#b0aea5`, border `#faf9f51a` (their `ivory-faded-10`), clay stays `#d97757` (it reads beautifully on slate). Posters pop harder in the dark; the light theme remains the default and the brand.

### 1b. Typography

Anthropic ships proprietary faces — `"Anthropic Sans"` (Styrene-successor grotesque), `"Anthropic Serif"`, `"Anthropic Mono"`, plus (free) JetBrains Mono — all verified in the CSS above with fallbacks `Arial`/`Georgia`. We can't use the proprietary ones, so Mishka Hub uses free look-alikes, keeping their fallback discipline:

| Role | Stack | Notes |
|---|---|---|
| Display / headings | `"Schibsted Grotesk", "Space Grotesk", Arial, sans-serif` | geometric grotesque, Styrene-adjacent; weights 500/700; letter-spacing `-0.005em` (Anthropic's own display tracking) |
| Serif display accent | `"Source Serif 4", Georgia, serif` | sparingly: the hero line ("Films worth your night in."), pull-quotes, empty-state poetry |
| Body / UI | `"Inter", system-ui, sans-serif` | 400/500/600 |
| Mono (stats, timestamps) | `"JetBrains Mono", ui-monospace, monospace` | Anthropic uses it too; free |

Type scale (rem): `12 → 14 → 16 (body) → 18 → 20 → 24 → 30 → 38 → 48`. Body copy 16px/1.5 (app UI runs denser than Anthropic's 20px editorial paragraphs — poster app, not essay). Headings line-height 1.1–1.2. Sentence case everywhere; no all-caps except tiny mono labels (`RATED 4.5`, tracked +0.08em, 11–12px).

### 1c. Spacing, radius, elevation

- **Spacing scale** (Anthropic's, verified): `4, 8, 12, 16, 24, 32, 40, 48, 64, 96 px`. Section padding ≥48px; the layout breathes *around* the poster grid, which is deliberately dense (§2).
- **Radius** (verified): `sm 4px` (posters, inputs' inner elements), `md 8px` (buttons, inputs, chips), `lg 16px` (cards, drawers, modals), `full` (pills, avatars). Nothing pill-shaped except actual pills.
- **Elevation: borders, not shadows.** Default = flat + 1px `line` border. Shadows exist only for genuinely floating things: drawer/modal `0 24px 48px -12px rgb(20 20 19 / 0.18)`, and the dragged poster (§3). No glass blur anywhere.
- Container: max-width `72rem`, gutter 20px mobile / 32px desktop. Page margin feel: airy top, sticky filter bar, dense grid below.

### 1d. Tailwind v4 `@theme` tokens (drop into `apps/web/src/index.css`)

```css
@import "tailwindcss";

@theme {
  /* color — paper & ink (Anthropic-verified values) */
  --color-paper: #faf9f5;
  --color-paper-mid: #f0eee6;
  --color-paper-deep: #e8e6dc;
  --color-ink: #141413;
  --color-ink-mid: #3d3d3a;
  --color-ink-soft: #5e5d59;
  --color-line: rgb(20 20 19 / 0.10);
  --color-line-strong: rgb(20 20 19 / 0.20);
  --color-clay: #d97757;
  --color-clay-deep: #c6613f;
  --color-kraft: #d4a27f;
  --color-oat: #e3dacc;
  --color-cloud: #b0aea5;
  --color-olive: #788c5d;
  --color-sky: #6a9bcc;
  --color-fig: #c46686;

  /* type */
  --font-display: "Schibsted Grotesk", "Space Grotesk", Arial, sans-serif;
  --font-serif: "Source Serif 4", Georgia, serif;
  --font-sans: "Inter", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;

  /* radius */
  --radius-sm: 4px;
  --radius-md: 8px;
  --radius-lg: 16px;

  /* elevation */
  --shadow-float: 0 24px 48px -12px rgb(20 20 19 / 0.18);
  --shadow-poster-drag: 0 32px 64px -16px rgb(20 20 19 / 0.35);

  /* poster grid */
  --poster-gap: 8px;
  --poster-gap-lg: 12px;
}
```

Usage then reads `bg-paper text-ink border-line rounded-lg font-display` throughout the app.

### 1e. Component specs

| Component | Spec |
|---|---|
| **Primary button** | `bg-ink text-paper rounded-md px-4 py-2.5 font-medium`; hover `bg-ink-mid`; active scale 0.98; focus ring `2px clay` offset 2px. Destructive-free zone — this app deletes almost nothing. |
| **Accent button** (one per view max — "Get recommendations", "Log to Letterboxd") | `bg-clay text-paper`; hover `bg-clay-deep`. |
| **Secondary button** | `bg-paper-mid text-ink border border-line`; hover `bg-paper-deep`. |
| **Ghost/tertiary** | text-only `text-ink-soft`, hover `text-ink underline underline-offset-4`. |
| **Input / select** | `bg-white border border-line-strong rounded-md px-3.5 py-2.5 text-ink placeholder:text-cloud`; focus: border-clay + ring `3px rgb(217 119 87 / 0.25)`; error: border-fig. Search input may go `rounded-full` in the filter bar. |
| **Card** | `bg-paper-mid border border-line rounded-lg p-6`; hover (when interactive) `border-line-strong` + translate-y `-1px`; never a shadow at rest. |
| **Nav/header** | sticky, `bg-paper/95` with plain 1px `border-line` bottom (no backdrop-blur glass); left: cat-face mark (`CatMark`, replaced the earlier clapperboard 2026-07-04 — two-eared silhouette + paper-colored eyes/mouth cutout, `currentColor` fill so it follows the clay accent and the theme; the same cat-face nested as a badge on an old film camera outline for the favicon/home-screen icon, `public/film-cat-icon.svg` + `apple-touch-icon.png`, hardcoded colors since favicons don't get CSS vars) + "Mishka Hub" in `font-display` with **clay** replacing the current indigo accent span; right: status pill + user avatar dot. |
| **Pills / status** | `rounded-full px-3 py-1 text-xs font-medium` tonal: ok = `bg-olive/15 text-olive`, warn = `bg-kraft/20 text-clay-deep`, error = `bg-fig/15 text-fig`, neutral = `bg-oat text-ink-mid`. (Direct port of the existing StatusPill states to the new palette.) |
| **Rating badge** | mono 11px, `bg-ink/85 text-paper` on posters (readability over art), `★ 4.5` in clay when it's *your* rating vs `text-paper` for TMDB average. |
| **User identity** | user 1 = clay dot, user 2 = sky dot, "together" = fig; used on poster badges, filter toggles, rec profiles. Consistent everywhere. |
| **Toast** | bottom-center card (`bg-ink text-paper rounded-lg shadow-float`), 4s, one at a time. |
| **Empty states** | serif display line + one small illustration-free CTA; warmth from words, not clipart. |

## 2. Letterboxd-style poster density

Posters are the content. Chrome recedes (ivory, hairlines); the grid is tight and image-forward like Letterboxd's.

- **Grid:** CSS grid, `gap: var(--poster-gap)` (8px; 12px ≥1024px). Columns: 3 (<480px) / 4 / 5 (≥768) / 6 (≥1024) / 8 (≥1440). Mobile shows ~12 posters above the fold — that's the density target.
- **Poster card:** aspect `2/3`, `rounded-sm` (4px — Letterboxd-tight, not the scaffold's 12px), 1px inset border `rgb(20 20 19 / 0.08)` to seat light posters on ivory, `object-cover`, lazy-loaded with `paper-deep` skeleton shimmer.
- **Hover (pointer devices):** border brightens to `clay`, scale 1.03 (150ms ease-out), bottom gradient overlay (`from-ink/85`) with title + year slides up — port of the current `MovieCard` behaviour, re-toned.
- **Focus (keyboard):** 2px clay ring, same overlay as hover; cards are `<button>`s.
- **Badges (top corners, 20px):** my rating (top-right), heart in `fig` when liked, `↻` rewatch, the two user dots bottom-left on shared walls. Max 2 badges visible; rest in the detail drawer.
- **Tap (mobile):** short tap opens the detail drawer; drag = §3 physics. No hover-dependent information anywhere.
- **Detail drawer:** right sheet (desktop) / bottom sheet (mobile), `bg-paper rounded-lg shadow-float`, backdrop `ink/30` (dim, **no blur**); content per [PHASE-2 §10](phases/PHASE-2-letterboxd-import.md).

## 3. Mobile poster drag — 3D tilt/flip physics

The signature interaction: a poster under drag behaves like a physical card — it **tilts in 3D following the finger** (like Letterboxd's app), it does not stay flat/vertical, and it springs home with momentum on release.

### 3a. Model

```
container:  perspective: 800px;            /* on the grid, not per-card */
card:       transform-style: preserve-3d; will-change: transform (during drag only)

per frame, from drag delta (dx, dy px since pointerdown):
  rotateY =  clamp(dx * 0.15, -18, 18)   deg    /* drag right → right edge recedes */
  rotateX =  clamp(-dy * 0.15, -18, 18)  deg    /* drag up   → top edge recedes   */
  translate = (dx * 0.30, dy * 0.30) px          /* card lags the finger — weight  */
  scale    = 1.06
  shadow   = var(--shadow-poster-drag), opacity ramps with |rotate|
  z-index  = raised; siblings untouched (no layout shift)

order matters: translate(…) rotateX(…) rotateY(…) scale(…)
```

Release → spring every value to 0/rest with the **pointer's release velocity as the spring's initial velocity** (stiffness ≈ 300, damping ≈ 20, mass 1): the card overshoots a few degrees, wobbles once, settles. A hard fling may add one full `rotateY += 360` "flip" before settling (delight, optional v2).

### 3b. Recommended implementation: `motion` (Framer Motion successor)

Add `motion` (~thousands of a MB, tree-shakeable; [motion.dev](https://motion.dev)) rather than hand-rolling rAF springs:

```tsx
const dx = useMotionValue(0), dy = useMotionValue(0)
const rotateY = useSpring(useTransform(dx, [-120, 120], [-18, 18]), { stiffness: 300, damping: 20 })
const rotateX = useSpring(useTransform(dy, [-120, 120], [18, -18]), { stiffness: 300, damping: 20 })
// pointer handlers write dx/dy; on release set both to 0 — springs carry velocity automatically.
<motion.button style={{ rotateX, rotateY, x: useTransform(dx, v => v * 0.3), y: … }} …/>
```

Motion values bypass React re-render — transforms are written straight to style per frame. Hand-rolled fallback (no dependency): pointer events + one `requestAnimationFrame` loop writing `el.style.transform`, spring integrator ~15 lines; same math.

### 3c. Pointer & scroll etiquette (the hard part)

- Use **Pointer Events** only (`pointerdown/move/up/cancel` + `setPointerCapture`) — one code path for touch/mouse/pen.
- **Don't steal vertical scroll.** Cards sit in a scrolling grid: set `touch-action: pan-y` on cards. Engage tilt only when the gesture is clearly a drag-on-card: pointer held ≥120ms without >8px vertical travel, **or** horizontal intent (|dx| > |dy| and |dx| > 8px). Until engaged, the browser scrolls normally; once engaged, call `preventDefault` on subsequent moves (listener registered non-passive only after engagement) and freeze scroll for that gesture.
- `pointercancel` (browser took the gesture) = instant spring home.
- Long-press ≥500ms without movement = context action (quick-rate radial), not tilt — cancel tilt if it fires.

### 3d. Performance notes

- Animate **transform + shadow-opacity only** (composited); never top/left/width or filters. Shadow via a pre-rendered `::after` whose `opacity` animates — `box-shadow` itself is a paint, not a composite.
- `will-change: transform` applied on engagement, removed on settle (permanent will-change on a 1,000-poster wall eats GPU memory).
- One active card maximum; pointer capture guarantees it.
- Poster `<img>` gets `draggable={false}` and `user-select: none` (kills ghost-drag and iOS callout).
- Budget: 60fps on a mid-range Android; test with 6×200 grid + active drag in Chrome DevTools perf panel.

### 3e. Reduced motion

`@media (prefers-reduced-motion: reduce)`: no tilt, no springs — pressed card scales to 1.02 with 80ms ease and returns; all other transitions collapse to opacity ≤150ms. Gate the motion-value wiring on `useReducedMotion()` so the spring code never mounts.

## 4. Voice & microcopy

Anthropic-calm: plain sentences, no exclamation marks, no "🎉". "Nothing on your services tonight — widen the net?" not "Oops! No results!". Numbers in mono. British English (it's a Scottish household: *favourites*, *catalogue*).

## 5. Acceptance criteria

- [ ] `apps/web/src/index.css` carries the `@theme` block from §1d; indigo/dark scaffold styling fully removed from `App.tsx`/`MovieCard.tsx` (no `indigo-*`, no `backdrop-blur`, no `bg-black/*`).
- [ ] Every colour in the UI resolves to a token from §1a (spot-check computed styles); clay is the only saturated accent on any given screen.
- [ ] Header, buttons, inputs, cards, pills match §1e specs including focus rings (keyboard-only walkthrough).
- [ ] Poster grid hits density targets: 3 cols / ~12 posters above the fold on a 375px viewport; 8 cols at 1440px; gaps 8/12px.
- [ ] Drag a poster on a phone: it tilts toward the drag direction (≤±18°), lags with weight, and on release springs home with visible overshoot/wobble carrying fling velocity.
- [ ] Vertical scrolling over the grid is never hijacked — un-engaged drags scroll the page (test slow + fast flicks).
- [ ] `prefers-reduced-motion: reduce` → zero 3D motion, scale-only feedback.
- [ ] Drag stays ≥55fps on a mid-range Android profile with a 200-poster grid.
- [x] Dark variant tokens documented (this doc) even though light ships first — see §6, shipped 2026-07-04.
- [ ] Attribution lines (TMDB/JustWatch, [ARCHITECTURE.md](ARCHITECTURE.md) §6) restyled in `ink-soft` mono 11px and present in the new footer.

## 6. Dark mode (shipped 2026-07-04)

Class-based, not media-query-based — a manual toggle beats the OS preference so the two
people can choose independently of their device setting. `apps/web/src/index.css` declares
`@custom-variant dark (&:where(.dark, .dark *));` (Tailwind v4's documented class-variant
hook) and a `.dark { --color-*: ... }` block overriding every §1a token in place, so existing
utility classes (`bg-paper`, `text-ink`, `border-line`, …) repaint automatically once `.dark`
lands on `<html>` — no `dark:` variant hunting needed across the app for token-based colours.
A few non-token spots (the `DetailDrawer` scroll-blur header's inline `backgroundColor`) do
carry explicit dark-aware values since they read CSS variables directly rather than through
a Tailwind class.

`ThemeToggle` (`apps/web/src/components/ThemeToggle.tsx`) persists the choice to
`localStorage` (`mishka-theme`), defaulting to the OS `prefers-color-scheme` on first visit.

## 7. Recommendation-expansion brace connector (shipped 2026-07-04)

Clicking a poster in "Something new to watch" expands a horizontal detail panel below the
grid, and a curly-brace-shaped SVG connects the two so the relationship reads at a glance
(`BraceConnector` + `bracePath()` in `App.tsx`).

- **Shape** — generalized from a user-supplied, hand-tuned devtools path into a parametrized
  `bracePath(peakPercent)`: flat shoulders out of each side, a tight point at the peak
  (directly above the clicked poster), built from cubic beziers with *duplicate control
  points* at each corner — the trick that gives a clean, sharp "L-shaped" bend instead of a
  single smooth arc.
- **Peak position** — animates via `useSpring`/`useTransform` so the point slides smoothly to
  the newly-clicked poster's position rather than jumping.
- **Clipping gotcha** — the ends looked "clipped by the panel's rounded corners." They
  weren't: the connector's *own* `<svg>` bounding box was clipping its own path. Fixed with
  `style={{ overflow: 'visible' }}` plus a taller rendered height (`h-9`) matching the
  viewBox's `BRACE_BOTTOM` coordinate — not by changing anything on the panel.
- **Panel outline merges into the brace** — the expansion panel has a `border-clay/60`
  outline on its sides and bottom only (deliberately no top border), so the brace's stroke
  color and the panel's outline read as one continuous shape rather than two separate
  elements touching.
