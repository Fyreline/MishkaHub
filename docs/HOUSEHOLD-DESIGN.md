# Aizome — the household design language

**Canonical copy lives in the Michi repo (`learningLanguageMachine/docs/HOUSEHOLD-DESIGN.md`),
next to the canonical `apps/web/src/theme.css`. Mirrored to every sibling repo by
`scripts/sync-theme.sh` — edit it here only.** Extracted 2026-07-11 from the two
strongest apps (Michi, then Mishka Hub) with the rest of the armada as corroboration.

This is the cross-app document: what every household app shares. Each app keeps its own
`docs/DESIGN.md` for app-specific surfaces (Michi's Path, Mishka's liquid connector,
Kakeibo's charts). Where an app DESIGN.md quotes colour hexes, this doc and `theme.css`
win — some older snippets predate the Aizome repaint.

Current fleet: **Michi** (Japanese learning), **Mishka Hub** (films), **Japan 2026**
(trip), **Kakeibo** (finances), **Sukumo** (life dashboard).

---

## 1. Philosophy — ten principles

1. **Warmth from restraint.** Calm washi surfaces, generous whitespace, exactly one
   saturated accent (`clay`) per view. Warmth comes from words and one good illustration,
   never from gradients, glassmorphism, or clipart.
2. **Borders, not shadows.** Default elevation is a 1px `border-line` hairline on a flat
   `paper-mid` card. Shadows (`shadow-float`, `shadow-poster-drag`) are reserved for
   things that genuinely float: drawers, modals, a dragged object. Backdrop blur only
   where content scrolls under chrome, and sparingly.
3. **Content forward, chrome recedes.** When the app is imagery-forward (posters), go
   Letterboxd-dense: tight gaps, many columns. When it's text/data-forward, go editorial:
   one centred column, air. Either way the navigation is near-invisible until needed.
4. **No punishment mechanics, ever.** No hearts/lives, no red-alert states, no streak
   guilt, no negative-coloured deltas. Errors are plum (`fig`) or warm (`kraft`), never
   harsh red. Crimson (`clay`) is *information and identity*, not alarm. A missed streak
   "rests". Badges are invitations.
5. **The user controls tempo.** Nothing auto-advances, nothing hijacks scroll. Gestures
   engage only on clear intent (Mishka's rule: held ≥120ms with ≤8px vertical travel, or
   horizontal intent). Motion earns engagement rather than grabbing it.
6. **UI is matter with weight.** Signature interactions are physical: springs carry real
   release velocity, dragged things lag the finger, liquid detaches and reforms.
   One obsessively-tuned signature interaction per app beats ten mediocre ones.
7. **Celebration is brief, then calm again.** The biggest moment in the fleet (Michi's
   torii checkpoint) is a self-drawing SVG, ≤12 pieces of confetti, one serif line —
   then quiet. No sound effects (in Michi, speech *is* the sound design).
8. **Accessibility is a gate.** 44px (`min-h-11`) touch targets, visible `ring-clay`
   focus, keyboard operability, aria labels/live regions, and a *complete*
   reduced-motion path — springs never even mount under `prefers-reduced-motion`.
9. **Two people, one household.** Every app is built for exactly two users. Person 1 is
   always `clay`, person 2 always `sky`, shared/likes `fig` — the same identity code on
   every surface (seen-by dots, rating columns, partner tiles). Auth proxies to Mishka
   Hub; no app stores passwords.
10. **One palette, many skins.** Semantic token names are frozen fleet-wide; apps differ
    by *values never appearing in components* and by their own metaphor ("a walking
    trail", "a cinema lobby", "a desk", "a paper travel folder", "mission control").

## 2. Colour — the Aizome palette

Source of truth: canonical `theme.css` (Michi repo), synced byte-identical to every app.
Woodblock-print scheme from five base colours: `#C33C54` hanko crimson, `#254E70`
Hokusai indigo, `#37718E` steel, `#8EE3EF` pale cyan, `#AEF3E7` pale mint.

Delivered as a Tailwind v4 `@theme` block → semantic utilities (`bg-paper`, `text-clay`,
`border-line`). **Hard rule in every repo: never hardcode a hex in a component.**
(Sole sanctioned exception: favicons, which can't read CSS variables.)

| Token | Light | Dark | Grammar |
|---|---|---|---|
| `paper` / `paper-mid` / `paper-deep` | `#f7fbfa` / `#e9f3f1` / `#dcebe8` | `#0f1d2b` / `#16283a` / `#1f3549` | page / cards / tracks & hover. Never pure white, never black. |
| `ink` / `ink-mid` / `ink-soft` | `#17293a` / `#2d4a63` / `#557186` | `#ecf6f4` / `#c8dbd9` / `#8fa9b3` | text hierarchy — indigo, never black |
| `line` / `line-strong` | ink @ 12% / 24% | ink @ 10% / 22% | hairline borders / inputs & dividers |
| `clay` / `clay-deep` | `#c33c54` / `#a92e45` | `#e05a72` / `#ea7288` | **THE accent** + person 1. Buttons, active tabs, brand. (`-deep` is *lighter* in dark.) |
| `kraft` | `#d08770` | `#dd9a82` | warm tertiary — "close", warnings, "you own this" |
| `oat` | `#d3eae2` | `#24404f` | tonal fills, hover backgrounds |
| `cloud` | `#90a5ab` | `#6d8794` | disabled, placeholders |
| `olive` | `#2e8b74` | `#5fcfae` | success, streaks alive |
| `sky` | `#37718e` | `#8ee3ef` | info + **person 2** |
| `fig` | `#9c3f6d` | `#d1729c` | likes/hearts, soft errors, occasions — the "not red" |
| `liquid` | `#c5e0dd` | `#223c4e` | connector surfaces (Mishka's connector, Michi's trail, Sukumo's thread) |
| `viz-1..4` | `#8ee3ef` `#aef3e7` `#f2c7cf` `#e8dfc0` | `viz-3` `#e8a7b4`, `viz-4` `#d8cba0`, rest same | categorical chart ramp |
| `shadow-float` | `0 24px 48px -12px` ink@22% | black@50% | floating chrome only |
| `shadow-poster-drag` | `0 32px 64px -16px` ink@38% | black@60% | actively dragged objects |

**Dark mode ("night print")** is a `.dark` class overriding the same tokens in the same
file — components never hunt `dark:` variants. Mechanism per app:
`@custom-variant dark (&:where(.dark, .dark *))` + `color-scheme`, a pre-paint inline
script in `index.html` (localStorage key, fall back to OS) so there's no flash, and a
manual toggle that beats OS preference — the two people choose independently.

**Theme-independent exception:** overlays sitting on artwork (poster scrims, rating
badges, drawer backdrops) use fixed `bg-black/70` + `text-white` so they don't invert
over images.

**Extending the palette** (the Kakeibo rule): app-specific tokens (`viz-5..8`,
sequential/diverging ramps, `gain`/`spend`/`over`) live in that app's `index.css` with
their own `.dark` overrides. They are promoted into canonical `theme.css` only when a
second app wants them. New needs never justify a raw hex.

## 3. Typography

Four Latin roles plus Japanese. Fonts are **self-hosted variable builds**
(`@fontsource-variable/*`) — Michi/Sukumo already do this; treat Google Fonts CDN
(early Mishka) as legacy, self-host in anything new.

| Role | Face | Use |
|---|---|---|
| `--font-display` | Schibsted Grotesk Variable | headings, brand wordmarks; tracking −0.005em |
| `--font-serif` | Source Serif 4 Variable | *sparingly*: hero lines, film titles, celebration copy, empty-state poetry |
| `--font-sans` | Inter Variable | body default, 16px/1.5 |
| `--font-mono` | JetBrains Mono Variable | **all numbers**, stats, timestamps, labels, attribution |
| `--font-jp` | Noto Sans JP Variable + Hiragino fallbacks | Japanese text (only in apps that render JP) |

- Scale: `12 · 14 · 16 · 18 · 20 · 24 · 30 · 38 · 48`. Headings lh 1.1–1.2.
- **Sentence case everywhere.** ALL-CAPS exists only as the kicker pattern:
  `font-mono text-[11px] uppercase tracking-[0.08em]` in `ink-soft`/`cloud`
  (e.g. `UNIT 4 · RESTAURANT`, `WHAT WE'VE WATCHED`).
- Big stats are bare mono: `font-mono text-[34px] leading-none` + kicker label.
- **Japanese rules** (Michi/Japan): JP is always *larger* than surrounding UI text,
  `leading-[1.4]` minimum, **never letter-spaced**. Furigana via real `<ruby>/<rt>`
  (`rt` 50% size, `ink-soft`). Romaji is sans *italic* `ink-soft`, physically below the
  kana — the eye must meet Japanese first.

## 4. Layout, spacing, shape

- Spacing scale: `4 8 12 16 24 32 40 48 64 96` px.
- Radii — exactly three + full: `sm 4px` (posters, dense imagery), `md 8px` (buttons,
  inputs, chips), `lg 16px` (cards, drawers, mats), `rounded-full` for pills/avatars
  only. Nested corners stay concentric (outer 2xl → inner xl).
- Container `max-w-6xl` centred, `px-5` gutter; focused flows narrow to `max-w-2xl`;
  auth/dialogs `max-w-sm/md`.
- The card: `bg-paper-mid border border-line rounded-lg p-6` (sections `p-5`, showpieces
  `p-8`). No shadow.
- Imagery-forward grids: 8px gaps (12px at `lg`), `3/4/5/6/8` columns by breakpoint,
  ~12 items above the fold on a 375px viewport.
- Charts are **hand-rolled SVG in palette tokens** — no chart libraries, fleet-wide.
- Skeletons mirror the loaded layout section-for-section (`animate-pulse bg-paper-deep`);
  reserve heights so content arrival never jumps the page.

## 5. Component grammar

- **Primary button**: `rounded-lg bg-clay px-4 py-3 text-sm font-medium text-paper
  transition hover:bg-clay-deep disabled:opacity-50`, `min-h-11`. Secondary: paper-mid +
  `border-line-strong`, hover `bg-oat`. Press feedback `active:scale-95`.
- **Inputs**: `min-h-11 rounded-md border border-line-strong bg-white px-3.5 py-2.5
  text-sm focus:border-clay` (+ `ring-3 ring-clay/25` where used), `dark:bg-paper-mid`.
  Errors annotate in `fig`. Search fields may go `rounded-full`.
- **Pills/badges**: `rounded-full px-2.5 py-1 font-mono text-[11px] tracking-[0.08em]`
  on 15–20% tonal fills — ok `olive/15`, warn `kraft/20`, error `fig/15`, neutral `oat`,
  accent `clay/15 text-clay`. Count badges cap ("20+") and render nothing at zero.
- **App shell**: sticky header `bg-paper/95 border-b border-line` (no blur) — mark +
  wordmark left (accent syllable in `clay`), inline tabs centre on desktop (active
  `text-ink border-b-2 border-clay` or `bg-clay/10 text-clay-deep`), toggles right.
  **Mobile: fixed 64px bottom tab bar**, `pb-[env(safe-area-inset-bottom)]`, one flat
  `currentColor` stroke glyph per tab, active = `text-clay`. 4–5 tabs maximum.
- **Progress**: `h-full rounded-full bg-clay transition-[width] duration-700` on a
  `paper-deep` track. Dashed `olive` line for goals.
- **Drawers/modals**: right sheet on desktop, full-screen mobile; `bg-black/30` dim (no
  blur), `shadow-float`, slide `x:24→0` 200ms easeOut.
- **Iconography & illustration**: flat monochrome `currentColor` SVG icons; scene art is
  flat SVG in palette colours only — no gradients (beyond scoped scene skies), ≤3
  colours per element. Mascots are `currentColor`/`var()` SVGs so they re-skin with the
  theme (MichiMark cat-in-torii, Mishka's film-camera cat, Sukumo's vat glyph).

## 6. Motion

Library: `motion/react`, driving motion-values (no re-render per frame).

- House springs: interactions `{stiffness: 300–400, damping: 20–30}`; drop-open panels
  `{stiffness: 340, damping: 28, mass: 0.9}` with `transform-origin` pinned to the
  trigger. Micro-transitions 150–200ms ease-out.
- Release velocity carries: on pointer-up, set targets and let springs overshoot, wobble
  once, settle. `will-change: transform` only while dragging.
- Scroll etiquette (principle 5): Pointer Events + `setPointerCapture`, intent
  thresholds before `preventDefault`, `pointercancel` springs home instantly, one active
  gesture at a time.
- Ambience (drifting clouds, twinkles, sways) is endless, gentle, 20s+, and gated behind
  `prefers-reduced-motion: no-preference`.
- Every repo carries the global reduced-motion collapse (`animation/transition →
  0.01ms`) *and* gates spring mounting via `useReducedMotion()` — colour must carry any
  meaning motion carries (e.g. wrong-answer shake).

## 7. Voice

**British English, calm, no exclamation marks, no emoji-as-excitement, no guilt.**
Encouraging without being a golden retriever: *"Nice — that one's sticking."* not
*"AMAZING!! 🎉"*.

- Serif one-liners set the register: *"The path to Japan starts with sumimasen."* /
  *"Films worth your night in."*
- Empty states are soft and slightly poetic: *"The path is mist right now."* /
  *"Nothing matches these filters right now — try loosening one."*
- Errors are gentle and factual: *"Couldn't load this film yet — {error}"*. Feedback on
  mistakes: *"Not quite —"* / *"Close — worth another look."* (kraft, never red).
- Buttons are verbs with personality where it fits: *"Set off"*, *"Watch now"*.
- Naming lore is load-bearing but rationed — one running joke per app (Cat-alogue,
  Meowck/Meowmy, Michi 道, kakeibo terms), never scattergun puns in body copy.
- Attribution/legal in mono 11px `ink-soft`.

## 8. Architecture (the skeleton every app shares)

- **Frontend**: React 19 + Vite + TypeScript + **Tailwind CSS v4**. `index.css` does
  `@import "tailwindcss"; @custom-variant dark(...); @import "./theme.css";` then a
  small local `@theme` for fonts/radii/app-only tokens. No `tailwind.config.js` tokens,
  no styled-components, near-zero hand-written CSS.
- **theme.css is a synced mirror** — edited only in the Michi repo, pushed by
  `scripts/sync-theme.sh` (see the `theme-sync` skill). Token names are frozen.
- **Backend**: FastAPI + SQLAlchemy + SQLite (or a thin proxy where a BaaS fits better);
  monorepo `apps/web` + `apps/server`.
- **Auth**: every app proxies sign-in to Mishka Hub. No password columns anywhere else.
- Favicons hardcode `clay` + `paper` hexes (documented exception) — update them on any
  repaint: `michi-icon.svg`, `cat-icon.svg`, `torii-icon.svg`, plus newer siblings.

**App icons (home screen).** The rules that make iOS 26 give the layered "liquid
glass" treatment instead of a flat tile — learned by comparing the fleet on a real
home screen (2026-07-11):

- `apple-touch-icon.png`: 180×180, **full-bleed opaque square, alpha channel
  stripped, no baked rounded corners** — iOS applies its own mask and lighting.
  Transparent backgrounds or pre-rounded corners render as a flat slab. Glyph centred
  at ~65–70% of the canvas on a `paper` ground.
- Generate it with a committed `scripts/generate-*.mjs` (sharp as devDependency —
  Japan_website's `generate-pwa-icons.mjs` is the reference), never by hand-export;
  hardcoded `clay`/`paper` values in icon scripts are part of the favicon exception.
- **Subpath-safe hrefs**: apps deployed as GitHub Pages project sites serve under
  `/<repo>/` — icon/manifest `<link>`s must use Vite's `%BASE_URL%` prefix, never
  absolute `/…` paths (an absolute path 404s in prod and iOS silently falls back to a
  flat screenshot tile).
- Manifest icons: `pwa-192`/`pwa-512` may keep the favicon's rounded ground;
  `maskable` variants are full-bleed with the glyph inside the central 80% safe zone.

## 9. New-app checklist

1. Scaffold `apps/web` + `apps/server`; copy `index.css` structure from Michi; add the
   repo to `sync-theme.sh` and run it (theme + this doc arrive together).
2. Self-host the four Latin fonts (`@fontsource-variable`); add Noto Sans JP only if the
   app renders Japanese.
3. Dark-mode pre-paint script in `index.html` with an app-specific localStorage key;
   manual toggle wins over OS.
4. Pick the app's metaphor (one sentence: "Michi is a walking trail") and its **one**
   signature interaction — budget the tuning time there.
5. Person 1 = clay, person 2 = sky, auth proxies to Mishka Hub.
6. Sticky header + mobile bottom bar shell; flat `currentColor` mascot mark; favicon
   with hardcoded clay/paper; apple-touch-icon per the §8 app-icon rules (opaque
   full-bleed square via a committed sharp script, `%BASE_URL%`-safe hrefs).
7. Write the app's own `docs/DESIGN.md` for its unique surfaces; colours by token name
   only. British microcopy, no exclamation marks, no red.
8. Gate: reduced-motion path, 44px targets, keyboard focus rings, Lighthouse a11y ≥95.
