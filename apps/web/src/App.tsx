import { useEffect, useRef, useState } from 'react'
import { AnimatePresence, animate, motion, useMotionValue, useTransform } from 'motion/react'
import {
  api,
  type FilmDetail,
  type Health,
  type Movie,
  type SimilarFilm,
  type RecommendationItem,
  type RecommendationProfile,
} from './api'
import { MovieCard } from './components/MovieCard'
import { Catalogue, DetailDrawer } from './components/Catalogue'
import { ThemeToggle } from './components/ThemeToggle'
import { SettingsPage } from './components/SettingsPage'
import { OwnedPage } from './components/OwnedPage'
import { UpcomingPage } from './components/UpcomingPage'
import { ServiceInsightsPage } from './components/ServiceInsightsPage'
import { LoginScreen } from './components/LoginScreen'
import { bootstrap, getUser, logout, subscribe, type AuthUser } from './auth'
import {
  FilmHeaderSkeleton,
  MoreLikeThisSection,
  MoreLikeThisSkeleton,
  UserRatingColumns,
  UserRatingColumnsSkeleton,
  WhereToWatchSection,
  WhereToWatchSkeleton,
} from './components/FilmDetailSections'
import { useFilmDetail } from './useFilmDetail'

type VibeOption = { value: string; label: string }

const VIBE_OPTIONS: VibeOption[] = [
  { value: '', label: 'Vibe' },
  { value: 'slow_burn', label: 'Slow burn' },
  { value: 'feel_good', label: 'Feel good' },
  { value: 'sad', label: 'Sad' },
  { value: 'tense', label: 'Tense' },
  { value: 'dark', label: 'Dark' },
  { value: 'quick_watch', label: 'Quick watch' },
]

/** Live-updating search: debounced as-you-type lookup with a poster+title/
 * year dropdown, replacing the old submit-button + results-grid flow. */
function SearchAutocomplete({ onSelect }: { onSelect: (id: number) => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<Movie[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    const q = query.trim()
    if (!q) {
      setResults([])
      setOpen(false)
      setLoading(false)
      return
    }
    setLoading(true)
    debounceRef.current = setTimeout(() => {
      api
        .search(q)
        .then((data) => {
          setResults(data.results.slice(0, 8))
          setError(null)
          setOpen(true)
        })
        .catch((err) => {
          setError(err instanceof Error ? err.message : String(err))
          setResults([])
        })
        .finally(() => setLoading(false))
    }, 300)
    return () => clearTimeout(debounceRef.current)
  }, [query])

  function handlePick(id: number) {
    onSelect(id)
    setQuery('')
    setResults([])
    setOpen(false)
  }

  return (
    <div className="relative mx-auto w-full max-w-xl">
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => {
          if (results.length) setOpen(true)
        }}
        placeholder="Search a film…"
        className="w-full rounded-full border border-line-strong bg-white px-5 py-3 text-center text-sm text-ink outline-none transition placeholder:text-cloud focus:border-clay focus:ring-3 focus:ring-clay/25 dark:bg-paper-mid sm:text-left"
      />
      <AnimatePresence>
        {open && (loading || error || results.length > 0) && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.15 }}
            className="absolute left-0 right-0 top-full z-30 mt-2 max-h-96 overflow-y-auto rounded-lg border border-line-strong bg-white shadow-float dark:bg-paper-mid"
          >
            {loading && <div className="p-3 text-sm text-ink-soft">Searching…</div>}
            {error && !loading && <div className="p-3 text-sm text-fig">{error}</div>}
            {!loading && !error && results.length === 0 && (
              <div className="p-3 text-sm text-ink-soft">No films found.</div>
            )}
            {!loading &&
              !error &&
              results.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  // Fire selection before the input's blur can close the
                  // dropdown out from under the click.
                  onMouseDown={(e) => {
                    e.preventDefault()
                    handlePick(m.id)
                  }}
                  className="flex w-full items-center gap-3 p-2 text-left transition hover:bg-oat"
                >
                  <div className="h-14 w-10 shrink-0 overflow-hidden rounded-sm bg-paper-deep">
                    {m.poster && <img src={m.poster} alt="" className="h-full w-full object-cover" />}
                  </div>
                  <div className="min-w-0">
                    <div className="truncate text-sm text-ink">{m.title}</div>
                    <div className="text-xs text-ink-soft">{m.year ?? '—'}</div>
                  </div>
                </button>
              ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** "You're looking at" + "More like this" panel — shown once a search result
 * (or a similar-film result) is selected. Recursively explorable: clicking a
 * similar-film card re-selects that film as the new focus. */
function FilmExplorer({ filmId, onSelect }: { filmId: number; onSelect: (id: number) => void }) {
  const [detail, setDetail] = useState<FilmDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailLoading, setDetailLoading] = useState(true)

  const [similar, setSimilar] = useState<SimilarFilm[]>([])
  const [similarError, setSimilarError] = useState<string | null>(null)
  const [similarLoading, setSimilarLoading] = useState(true)

  const [quickWatch, setQuickWatch] = useState(false)
  const [vibe, setVibe] = useState('')

  useEffect(() => {
    let cancelled = false
    setDetailLoading(true)
    setDetailError(null)
    setDetail(null)
    api
      .getFilm(filmId)
      .then((d) => {
        if (cancelled) return
        setDetail(d)
      })
      .catch((err) => {
        if (cancelled) return
        setDetailError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [filmId])

  useEffect(() => {
    let cancelled = false
    setSimilarLoading(true)
    setSimilarError(null)
    api
      .getSimilarFilms(filmId, { limit: 12, maxRuntime: quickWatch ? 95 : undefined, vibe: vibe || undefined })
      .then((res) => {
        if (cancelled) return
        setSimilar(res.items)
      })
      .catch((err) => {
        if (cancelled) return
        setSimilarError(err instanceof Error ? err.message : String(err))
        setSimilar([])
      })
      .finally(() => {
        if (!cancelled) setSimilarLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [filmId, quickWatch, vibe])

  return (
    <section className="mt-8 border-t border-line pt-8">
      {detailLoading && (
        <div className="grid gap-6 sm:grid-cols-[minmax(0,180px)_1fr]">
          <div className="aspect-2/3 w-full max-w-[180px] animate-pulse rounded-sm bg-paper-deep" />
          <FilmHeaderSkeleton />
        </div>
      )}
      {detailError && !detailLoading && (
        <div className="rounded-lg border border-fig/30 bg-fig/10 px-4 py-3 text-sm text-fig">
          Nothing here yet — {detailError}
        </div>
      )}

      {detail && !detailLoading && (
        <div className="grid gap-6 sm:grid-cols-[minmax(0,180px)_1fr]">
          {detail.poster && (
            <img
              src={detail.poster}
              alt={detail.title}
              className="aspect-2/3 w-full max-w-[180px] rounded-sm object-cover shadow-float"
            />
          )}
          <div>
            <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-cloud">
              You&apos;re looking at
            </span>
            <h3 className="mt-1 font-serif text-2xl text-ink">{detail.title}</h3>
            <p className="mt-0.5 text-xs text-ink-soft">
              {detail.year ?? '—'}
              {detail.runtime_min ? ` · ${detail.runtime_min} min` : ''}
            </p>
            {detail.genres.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {detail.genres.map((g) => (
                  <span key={g} className="rounded-full border border-line-strong px-2 py-0.5 text-[11px] text-ink-soft">
                    {g}
                  </span>
                ))}
              </div>
            )}
            {detail.overview && <p className="mt-3 text-sm leading-relaxed text-ink-mid">{detail.overview}</p>}
          </div>
        </div>
      )}

      <div className="mt-10">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h4 className="font-display text-lg font-medium text-ink">More like this</h4>
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => setQuickWatch((v) => !v)}
              aria-pressed={quickWatch}
              className={`min-h-11 rounded-md border px-2.5 py-1.5 text-xs font-medium transition sm:min-h-0 ${
                quickWatch ? 'border-clay bg-clay/10 text-clay-deep' : 'border-line-strong bg-white text-ink-mid hover:bg-oat dark:bg-paper-mid'
              }`}
            >
              Quick watch (≤95 min)
            </button>
            <select
              value={vibe}
              onChange={(e) => setVibe(e.target.value)}
              className="min-h-11 rounded-md border border-line-strong bg-white px-2 py-1.5 text-xs text-ink-mid outline-none focus:border-clay sm:min-h-0 dark:bg-paper-mid"
            >
              {VIBE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="mt-4">
          {similarLoading && (
            <div className="grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 xl:grid-cols-8">
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="aspect-2/3 animate-pulse rounded-sm bg-paper-deep" />
              ))}
            </div>
          )}

          {similarError && !similarLoading && (
            <div className="rounded-lg border border-fig/30 bg-fig/10 px-4 py-3 text-sm text-fig">
              Nothing here yet — {similarError}
            </div>
          )}

          {!similarLoading && !similarError && similar.length === 0 && (
            <p className="text-sm text-ink-soft">Nothing here yet. Try loosening the filters above.</p>
          )}

          {!similarLoading && similar.length > 0 && (
            <div
              className="grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8"
              style={{ perspective: '800px' }}
            >
              {similar.map((s) => (
                <MovieCard
                  key={s.film.id}
                  movie={{
                    id: s.film.id,
                    title: s.film.title,
                    year: s.film.year != null ? String(s.film.year) : null,
                    overview: null,
                    poster: s.film.poster,
                    vote_average: null,
                  }}
                  badges={undefined}
                  onClick={() => onSelect(s.film.id)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  )
}

// Mirrors Tailwind's default breakpoints AND the exact grid-cols-* steps the
// Cat-alogue/search/FilmExplorer poster grids use (3/4/5/6/8) — see
// Catalogue.tsx's grid className. Kept in sync manually since there's no
// shared constant for it yet.
const COLUMN_BREAKPOINTS: [minWidth: number, columns: number][] = [
  [1280, 8],
  [1024, 6],
  [768, 5],
  [640, 4],
  [0, 3],
]

function columnsForWidth(width: number): number {
  for (const [minWidth, columns] of COLUMN_BREAKPOINTS) {
    if (width >= minWidth) return columns
  }
  return 3
}

/** Tracks how many poster columns the Cat-alogue grid is currently showing,
 * so the homepage recommendations row can render exactly one full row of
 * cards — never wrapping, never showing a partial row. */
function useResponsiveColumns(): number {
  const [columns, setColumns] = useState(() =>
    typeof window === 'undefined' ? 3 : columnsForWidth(window.innerWidth),
  )
  useEffect(() => {
    function onResize() {
      setColumns(columnsForWidth(window.innerWidth))
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  return columns
}

const REC_PROFILE_OPTIONS: { value: RecommendationProfile; label: string }[] = [
  { value: 'together', label: 'Together' },
  { value: 'me', label: 'Meowck' },
  { value: 'partner', label: 'Meowmy' },
]

type RuntimeBucket = 'under95' | '95to120' | '121to180' | 'over180'

const RUNTIME_BUCKET_OPTIONS: { value: RuntimeBucket; label: string }[] = [
  { value: 'under95', label: '<95 min' },
  { value: '95to120', label: '95–120 min' },
  { value: '121to180', label: '121–180 min' },
  { value: 'over180', label: '180+ min' },
]

// Real TMDB genre names (not the abbreviated "Sci-Fi" the Cat-alogue's filter
// uses) so this multi-select's substring match against the backend's
// metadata_json actually resolves — see pipeline.py's AND-matched `genres`.
const GENRES = [
  'Action',
  'Adventure',
  'Animation',
  'Comedy',
  'Crime',
  'Documentary',
  'Drama',
  'Family',
  'Fantasy',
  'Horror',
  'Mystery',
  'Romance',
  'Science Fiction',
  'Thriller',
  'War',
  'Western',
]

function toggleInArray<T>(arr: T[], value: T): T[] {
  return arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value]
}

function FilterPill({
  active,
  onClick,
  children,
  size = 'md',
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
  size?: 'sm' | 'md'
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`min-h-11 shrink-0 rounded-full border font-medium transition sm:min-h-0 ${
        size === 'sm' ? 'px-2 py-0.5 text-[11px]' : 'px-2.5 py-1 text-xs'
      } ${
        active
          ? 'border-clay bg-clay/10 text-clay-deep'
          : 'border-line-strong bg-white text-ink-mid hover:bg-oat dark:bg-paper-mid'
      }`}
    >
      {children}
    </button>
  )
}

/** A wide connector spanning the recommendations row, peaking up toward the
 * clicked poster and flowing along the top of the expansion panel below it —
 * "vaguely like a curly bracket on its side," coming out of the panel to
 * meet the card it expanded from. `peakPercent` positions the peak; the two
 * arms always run the panel's full width regardless of column count. */
// Builds a real curly-brace-like path: edges dip DOWN into the panel, rise
// to a shoulder hump, then a tighter, steeper curve to a sharp point at the
// peak (mirrored on the right) — the shape an actual "{" has (two curves
// plus a distinct central tooth), not a single smooth arc.
// Coordinates below match a hand-tuned reference the household provided
// (devtools-edited to get the feel right, then generalized here so it works
// for any peak position, not just the one x=43.75 example): ends drop
// straight down into the panel, a rounded corner (duplicate control point —
// the standard bezier trick for an L-shaped rounded turn) into a flat
// shoulder, then a tighter curve up to a sharp point at the peak.
const BRACE_BOTTOM = 35
// Shoulder raised from the original 10 to 22: now that the connector is a
// SOLID filled shape rather than a thin stroke (per the household's "solid
// dark backdrop" request), the flat band between the shoulder and the
// bottom edge reads as a uniform bar — the taller that band, the more the
// whole thing looks like one flat bar instead of a shape that visibly
// peaks toward a specific poster. Shrinking that flat band (raising
// BRACE_SHOULDER closer to BRACE_BOTTOM) leaves more relative height for the
// actual taper, so the peak/notch stays legible as "pointing at one poster"
// even filled solid.
const BRACE_SHOULDER = 22
const BRACE_PEAK_Y = 1
const BRACE_EDGE = 5 // inset from each side where the corner completes
const BRACE_PEAK_OFFSET = 5.5 // x-distance from the peak where the "steep" control point sits (at full width) — widened so the taper reads as a broad wedge, not a thin sliver, once filled solid
const BRACE_SHOULDER_RATIO = 0.516 // how far along the shoulder span the gentler control point sits

/** `tooth` is how "grown" the central point is, 0..~1.2: 1 = the normal full
 * peak, 0 = fully retracted flat into the shoulder line (used while the old
 * connector is detaching, or before a new one has grown in), >1 = a springy
 * overshoot poking slightly past the poster (the liquid "wobble" as a new
 * connection lands). The neck also narrows as the tooth retracts — that's
 * the water-tension pinch.
 *
 * Peak positioning near the row's edges: rather than clamping `peakPercent`
 * away from the true edge-poster centers (which visibly shifted the
 * connector off-center under the first/last poster), the two half-widths
 * around the peak are independently clamped to whatever room actually
 * exists on that side — so a peak near x=0 gets a squashed left half and a
 * full-width right half, and the tip still lands exactly on the true
 * center instead of being pushed inward. */
function bracePath(peakPercent: number, tooth = 1): string {
  const p = Math.max(0, Math.min(100, peakPercent))
  const t = Math.max(0, Math.min(1.2, tooth))
  const maxOffset = BRACE_PEAK_OFFSET * (0.35 + 0.65 * Math.min(t, 1))
  // Independently clamp each side's control-point offset to the room
  // between the peak and that side's edge inset, so the curve never
  // reaches back past its own starting edge (which would invert the shape)
  // while still letting the peak itself sit anywhere, including x=0..100.
  const leftOffset = Math.max(0, Math.min(maxOffset, p - BRACE_EDGE))
  const rightOffset = Math.max(0, Math.min(maxOffset, 100 - BRACE_EDGE - p))
  const peakY = BRACE_SHOULDER - (BRACE_SHOULDER - BRACE_PEAK_Y) * t
  const leftSpan = Math.max(0, p - leftOffset - BRACE_EDGE)
  const rightSpan = Math.max(0, 100 - BRACE_EDGE - (p + rightOffset))
  const leftCtrl1 = BRACE_EDGE + leftSpan * BRACE_SHOULDER_RATIO
  const leftCtrl2 = p - leftOffset
  const rightCtrl1 = p + rightOffset
  const rightCtrl2 = 100 - BRACE_EDGE - rightSpan * BRACE_SHOULDER_RATIO
  return [
    `M0,${BRACE_BOTTOM}`,
    `C0,${BRACE_SHOULDER} 0,${BRACE_SHOULDER} ${BRACE_EDGE},${BRACE_SHOULDER}`,
    `C${leftCtrl1},${BRACE_SHOULDER} ${leftCtrl2},${BRACE_SHOULDER} ${p},${peakY}`,
    `C${rightCtrl1},${BRACE_SHOULDER} ${rightCtrl2},${BRACE_SHOULDER} ${100 - BRACE_EDGE},${BRACE_SHOULDER}`,
    `C100,${BRACE_SHOULDER} 100,${BRACE_SHOULDER} 100,${BRACE_BOTTOM}`,
  ].join(' ')
}

/** The connector's "switching posters" choreography, driven by the row below:
 * steady → snap (the old connection detaches AT ITS OWN OLD POSITION — no
 * lean toward the new poster first — the tooth retracts flat and a droplet
 * falls straight down) → form (the connector jumps, with zero horizontal
 * animation, to the new poster's position and grows/springs up from flat to
 * full height, "joining" the poster's own ring) → steady. There is
 * deliberately no interpolation between the old x and the new x at any
 * point — the household was explicit that switching posters should never
 * read as sliding sideways, only detach-in-place then reform fresh
 * elsewhere. Timings are exported to the row so its setTimeout sequencing
 * and the animations here can never drift apart. */
type ConnectorPhase = 'steady' | 'snap' | 'form'
const SWITCH_SNAP_MS = 150
const SWITCH_FORM_MS = 380

/** Links the expansion panel back to the poster it came from — edges drop
 * straight down into the panel, a rounded corner, a shoulder, then a sharp
 * point at the peak (see bracePath). Two stacked paths from the same data:
 * a panel-background fill (closed along the bottom, unstroked) that makes the
 * brace read as the panel's own body pinching up to the poster, and a solid
 * dark stroke (open, unfilled — closing it would draw a line across the
 * panel's borderless top edge) matching the panel's border and the expanded
 * poster's ring — a near-black tone rather than the app's clay accent, per
 * the household's reference sketch (thick dark outline, not a thin colored
 * line), so poster → neck → panel reads as one continuous solid shape.
 * `peakX` only ever jumps (no spring, no tween) — see the phase doc above;
 * only `tooth` (how "grown" the peak is) animates. Explicit
 * `overflow: visible` — the shape's ends sit right at the SVG's own
 * bounding-box edge and would get clipped there otherwise. */
function BraceConnector({ peakPercent, phase }: { peakPercent: number; phase: ConnectorPhase }) {
  const peakX = useMotionValue(peakPercent)
  // Starts at 0 so opening the panel grows the tooth up toward the poster
  // (the connection "forming") rather than popping in fully drawn.
  const tooth = useMotionValue(0)

  useEffect(() => {
    if (phase === 'steady') {
      peakX.jump(peakPercent)
      const anim = animate(tooth, 1, { type: 'spring', stiffness: 320, damping: 22 })
      return () => anim.stop()
    }
    if (phase === 'snap') {
      // Old connection detaches right where it already was — no horizontal
      // motion at all — the neck just snaps back flat, tension released.
      const anim = animate(tooth, 0, { duration: SWITCH_SNAP_MS / 1000, ease: [0.7, 0, 0.85, 0.4] })
      return () => anim.stop()
    }
    // 'form': the peak is currently flat (invisible height-wise), so jumping
    // its x to the new poster here is unnoticeable — then spring the tooth
    // back up with a deliberate overshoot wobble, growing fresh at the new
    // spot with zero tie back to the old one.
    peakX.jump(peakPercent)
    const anim = animate(tooth, 1, { type: 'spring', stiffness: 430, damping: 15 })
    return () => anim.stop()
  }, [phase, peakPercent, peakX, tooth])

  const dStroke = useTransform([peakX, tooth] as const, ([p, t]: number[]) => bracePath(p, t))
  const dFill = useTransform([peakX, tooth] as const, ([p, t]: number[]) => `${bracePath(p, t)} Z`)

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 100 ${BRACE_BOTTOM}`}
        preserveAspectRatio="none"
        aria-hidden
        style={{ overflow: 'visible' }}
        className="pointer-events-none h-9 w-full text-ink"
      >
        <motion.path d={dFill} fill="currentColor" stroke="none" />
        <motion.path d={dStroke} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
      </svg>
      {phase === 'snap' && (
        <motion.span
          key="droplet"
          aria-hidden
          className="pointer-events-none absolute top-0 h-1.5 w-1.5 rounded-full bg-ink"
          style={{ left: `${peakPercent}%`, x: '-50%' }}
          initial={{ y: 2, opacity: 0.9, scale: 1 }}
          animate={{ y: 26, opacity: 0, scale: 0.5 }}
          transition={{ duration: 0.32, ease: 'easeIn' }}
        />
      )}
    </div>
  )
}

/** The horizontal expand-in-place detail view for a clicked recommendation
 * card. Uses the film's backdrop (the same wide "frame" shown atop the
 * Cat-alogue's DetailDrawer) rather than the vertical poster, sized to
 * whatever height the title/meta/synopsis/genres block naturally needs (pure
 * CSS: the image is absolutely positioned against that block's own relative
 * container, so it always matches without any JS measurement). A gradient
 * scrim fades the image out into a *solid* panel-background colour — not
 * just an alpha mask — so text stays legible in both light and dark mode;
 * the fade starts early enough that the text area is fully opaque. Shares
 * the exact same data/behavior as Catalogue.tsx's DetailDrawer via
 * useFilmDetail + FilmDetailSections; only the shell differs. */
function RecommendationExpansionPanel({
  filmId,
  originPercent,
  onNavigate,
  onClose,
  onOpenOverlay,
}: {
  filmId: number
  /** Horizontal position (0–100) of the brace peak / clicked poster — the
   * panel's open/close scaling is anchored here so it visibly expands out
   * from the poster it belongs to, not from its own dead center. */
  originPercent: number
  onNavigate: (id: number) => void
  onClose: () => void
  /** "More like this" opens the full movie overlay on top, rather than
   * replacing what's currently expanded — unlike `onNavigate`, which is
   * still used for the "fix the match" rematch flow (that one really is
   * correcting the currently-expanded film in place, not navigating away
   * from it). */
  onOpenOverlay: (id: number) => void
}) {
  const {
    detail,
    availability,
    availabilityError,
    availabilityLoading,
    error,
    loading,
    similar,
    similarError,
    similarLoading,
    ratingBusy,
    ratingError,
    likedBusy,
    likedError,
    seenBusy,
    seenError,
    rematchOpen,
    setRematchOpen,
    rematchQuery,
    setRematchQuery,
    rematchResults,
    rematchSearching,
    rematchError,
    rematchApplyingId,
    handleSetRating,
    handleClearRating,
    handleToggleLiked,
    handleMarkSeen,
    handleRematchSearch,
    handleRematchPick,
  } = useFilmDetail(filmId, onNavigate)

  return (
    <motion.div
      // Drops down and expands open from the brace peak (transform origin is
      // the clicked poster's x, at the panel's top edge) — a compressed
      // scaleY unfolding downward plus a small drop, on a spring so it lands
      // with a hint of settle rather than a linear slide.
      initial={{ opacity: 0, y: -16, scaleY: 0.72, scaleX: 0.98 }}
      animate={{ opacity: 1, y: 0, scaleY: 1, scaleX: 1 }}
      exit={{ opacity: 0, y: -12, scaleY: 0.9, transition: { duration: 0.16, ease: 'easeIn' } }}
      transition={{
        type: 'spring',
        stiffness: 340,
        damping: 28,
        mass: 0.9,
        opacity: { duration: 0.18, ease: 'easeOut' },
      }}
      style={{ transformOrigin: `${originPercent}% 0%` }}
      // Border on the sides + bottom only (no top) — same 2px weight and
      // solid dark (ink) color as the brace's stroke/fill and the expanded
      // poster's ring, per the household's reference sketch (thick dark
      // outline, not the app's clay accent) — poster → connector → box reads
      // as one continuous solid dark shape rather than three separate
      // elements. Top corners deliberately square (rounded-b-xl, not
      // rounded-xl): the brace's fill/stroke above this panel has a flat,
      // square-cornered bottom edge (BRACE_BOTTOM), so rounding the panel's
      // top corners left a sliver of background peeking through at each
      // corner where the round curve pulled away from the brace's square
      // edge — squaring them off makes the two flush, no seam.
      className="relative overflow-hidden rounded-b-xl border-x-2 border-b-2 border-ink bg-paper-mid p-4 sm:p-6"
    >
      <button
        type="button"
        onClick={onClose}
        className="absolute right-3 top-3 z-10 rounded-md px-2 py-1 text-sm text-ink-soft transition hover:bg-oat hover:text-ink"
      >
        Close
      </button>

      {loading && (
        // Mirrors the loaded layout section-for-section (header, then the
        // ratings/where-to-watch two-column grid, then the More-like-this
        // poster grid) so the panel's full shape is stable from the first
        // frame — no bottom half popping in when the data lands.
        <div className="pr-10">
          <FilmHeaderSkeleton />
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <UserRatingColumnsSkeleton />
            <WhereToWatchSkeleton />
          </div>
          <div className="mt-4">
            <MoreLikeThisSkeleton />
          </div>
        </div>
      )}
      {error && !loading && <p className="text-sm text-fig">Couldn&apos;t load this film yet — {error}</p>}

      {detail && !loading && (
        <div>
          {/* Backdrop-as-background header: image absolutely fills this
              relative container, whose height is set purely by the in-flow
              text content — always exactly as tall as the synopsis needs. */}
          <div className="relative -m-4 mb-0 overflow-hidden rounded-t-xl sm:-m-6 sm:mb-0">
            {detail.backdrop && (
              // Keyed on the image URL + a slight delay: when switching films
              // the new backdrop fades in *after* the connector has re-formed
              // at the new poster (the detach/reform beat finishes first),
              // rather than everything changing at once.
              <motion.img
                key={detail.backdrop}
                src={detail.backdrop}
                alt=""
                aria-hidden
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.45, ease: 'easeOut', delay: 0.12 }}
                className="absolute inset-0 h-full w-full object-cover"
              />
            )}
            {detail.backdrop && (
              // A lighter touch than the first pass: a subtle uniform wash
              // (first layer) for baseline contrast everywhere, a gentler
              // gradient (second layer) toward the synopsis side — both
              // still tied to --color-paper-mid so they darken in dark mode
              // and lighten in light mode automatically. The real legibility
              // guarantee is the text-shadow glow below (same idea, applied
              // per-glyph instead of as a wash), so the wash itself can stay
              // light enough that the frame is still actually visible.
              <div
                className="absolute inset-0"
                aria-hidden
                style={{
                  // Third pass, lighter again ("still can feel a little hard
                  // to see"): the uniform wash drops 18% → 8%, and the
                  // gradient now only reaches *near*-solid (88%) and much
                  // further right (65% instead of 30%), so most of the frame
                  // actually reads as a photo. Per-glyph legibility is the
                  // textShadow glow's job below, not this wash's.
                  background: [
                    'linear-gradient(to right, color-mix(in srgb, var(--color-paper-mid) 10%, transparent) 0%, color-mix(in srgb, var(--color-paper-mid) 45%, transparent) 40%, color-mix(in srgb, var(--color-paper-mid) 88%, transparent) 65%)',
                    'color-mix(in srgb, var(--color-paper-mid) 8%, transparent)',
                  ].join(', '),
                }}
              />
            )}
            <div
              className="relative p-4 pr-12 sm:p-6 sm:pr-14"
              style={{
                // Soft glow in the panel's own background color behind every
                // glyph — reads as a gentle halo, not a wash, and (unlike the
                // background layers above) guarantees contrast right at each
                // letter regardless of what the frame looks like underneath.
                // No text color changes; --color-paper-mid already flips
                // between a light and dark tone per theme.
                textShadow:
                  '0 0 6px var(--color-paper-mid), 0 0 6px var(--color-paper-mid), 0 0 14px var(--color-paper-mid)',
              }}
            >
              <h3 className="font-serif text-xl text-ink">{detail.title}</h3>
              <p className="mt-0.5 text-xs text-ink-mid">
                {detail.year ?? '—'}
                {detail.runtime_min ? ` · ${detail.runtime_min} min` : ''}
              </p>
              <button
                type="button"
                onClick={() => setRematchOpen((v) => !v)}
                className="mt-1 text-[11px] text-ink-soft underline decoration-dotted underline-offset-2 transition hover:text-ink"
              >
                Wrong film?
              </button>

              {rematchOpen && (
                <div className="mt-2 rounded-md border border-line-strong bg-paper p-3">
                  <p className="text-[11px] text-ink-soft">
                    Search TMDB for the film this should actually be, then pick it to move all
                    watch/rating/like/review history over.
                  </p>
                  <form onSubmit={handleRematchSearch} className="mt-2 flex gap-1.5">
                    <input
                      type="text"
                      value={rematchQuery}
                      onChange={(e) => setRematchQuery(e.target.value)}
                      placeholder="Search by title…"
                      className="min-w-0 flex-1 rounded-md border border-line-strong bg-white dark:bg-paper-mid px-2 py-1.5 text-sm text-ink placeholder:text-cloud"
                    />
                    <button
                      type="submit"
                      disabled={rematchSearching || !rematchQuery.trim()}
                      className="min-h-11 rounded-md border border-line-strong bg-white dark:bg-paper-mid px-2.5 py-1 text-[11px] font-medium text-ink-mid transition hover:bg-oat disabled:opacity-50 sm:min-h-0"
                    >
                      {rematchSearching ? 'Searching…' : 'Search'}
                    </button>
                  </form>

                  {rematchError && <p className="mt-1.5 text-[11px] text-fig">{rematchError}</p>}

                  {rematchResults.length > 0 && (
                    <ul className="mt-2 space-y-1">
                      {rematchResults.map((c) => (
                        <li key={c.id}>
                          <button
                            type="button"
                            disabled={rematchApplyingId !== null}
                            onClick={() => handleRematchPick(c.id)}
                            className="flex w-full items-center gap-2 rounded-md px-1.5 py-1.5 text-left transition hover:bg-oat disabled:opacity-50"
                          >
                            <div className="h-12 w-8 shrink-0 overflow-hidden rounded-sm bg-paper-deep">
                              {c.poster && <img src={c.poster} alt="" className="h-full w-full object-cover" />}
                            </div>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-sm text-ink-mid">{c.title}</span>
                              <span className="block text-[11px] text-cloud">{c.year ?? '—'}</span>
                            </span>
                            {rematchApplyingId === c.id && (
                              <span className="shrink-0 text-[11px] text-ink-soft">Applying…</span>
                            )}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}

              {detail.overview && <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-mid">{detail.overview}</p>}

              {detail.genres.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {detail.genres.map((g) => (
                    // Solid chip, not just an outline — sits over the
                    // backdrop image, so it needs its own real background
                    // (paper-mid, theme-aware) rather than relying on
                    // whatever happens to be behind a transparent pill.
                    <span
                      key={g}
                      className="rounded-full bg-paper-mid px-2 py-0.5 text-[11px] text-ink-mid shadow-sm"
                      style={{ textShadow: 'none' }}
                    >
                      {g}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <UserRatingColumns
              detail={detail}
              ratingBusy={ratingBusy}
              ratingError={ratingError}
              likedBusy={likedBusy}
              likedError={likedError}
              seenBusy={seenBusy}
              seenError={seenError}
              onSetRating={handleSetRating}
              onClearRating={handleClearRating}
              onToggleLiked={handleToggleLiked}
              onMarkSeen={handleMarkSeen}
            />
            <WhereToWatchSection
              availability={availability}
              filmTitle={detail.title}
              loading={availabilityLoading}
              error={availabilityError}
            />
          </div>

          <div className="mt-4">
            <MoreLikeThisSection
              similar={similar}
              similarLoading={similarLoading}
              similarError={similarError}
              onNavigate={onOpenOverlay}
            />
          </div>
        </div>
      )}
    </motion.div>
  )
}

/** "Something new to watch" — the homepage's main section: a single row of
 * unwatched/stale recommendations with its own profile toggle + filter bar
 * (genres on one row, runtime + vibe on the next), independent of the
 * Cat-alogue's own filters per the household's "leave the Cat-alogue alone"
 * instruction. Clicking a card expands a horizontal detail panel in place,
 * right below the row; clicking the same card again (or its own Close
 * button) collapses it. */
function UnseenRecommendationsRow() {
  const [profile, setProfile] = useState<RecommendationProfile>('together')
  const [runtimeBuckets, setRuntimeBuckets] = useState<RuntimeBucket[]>([])
  const [genres, setGenres] = useState<string[]>([])
  const [vibe, setVibe] = useState('')
  const [items, setItems] = useState<RecommendationItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const columns = useResponsiveColumns()

  const [expanded, setExpanded] = useState<{ filmId: number; index: number } | null>(null)
  const [overlayFilmId, setOverlayFilmId] = useState<number | null>(null)

  // Poster-switch choreography (see BraceConnector): while a switch is in
  // flight, `expanded` still points at the OLD film (its panel + connector
  // stay up through the stretch and snap beats) and `switchTarget` holds the
  // new one; the timers below advance the phases and finally commit the
  // target. Any new click cancels the in-flight sequence first.
  const [switchTarget, setSwitchTarget] = useState<{ filmId: number; index: number } | null>(null)
  const [connectorPhase, setConnectorPhase] = useState<ConnectorPhase>('steady')
  const switchTimers = useRef<number[]>([])

  function clearSwitchTimers() {
    for (const t of switchTimers.current) window.clearTimeout(t)
    switchTimers.current = []
  }
  useEffect(() => clearSwitchTimers, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .getRecommendations({
        profile,
        limit: 8,
        runtime_buckets: runtimeBuckets.length ? runtimeBuckets.join(',') : undefined,
        genres: genres.length ? genres.join(',') : undefined,
        vibe: vibe || undefined,
      })
      .then((res) => {
        if (cancelled) return
        setItems(res.items)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
        setItems([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [profile, runtimeBuckets, genres, vibe])

  // Filter changes invalidate whatever was expanded (its position/relevance
  // may no longer make sense against the new result set).
  useEffect(() => {
    clearSwitchTimers()
    setSwitchTarget(null)
    setConnectorPhase('steady')
    setExpanded(null)
  }, [profile, runtimeBuckets, genres, vibe])

  const visible = items.slice(0, columns)

  function handleCardClick(filmId: number, index: number) {
    clearSwitchTimers()
    if (!expanded) {
      setConnectorPhase('steady')
      setExpanded({ filmId, index })
      return
    }
    // "Current" from the household's point of view is whatever the connector
    // is headed for — mid-switch, clicking the incoming poster again closes.
    const current = switchTarget ?? expanded
    if (current.filmId === filmId) {
      setSwitchTarget(null)
      setConnectorPhase('steady')
      setExpanded(null)
      return
    }
    // Switching to a different poster: the OLD connector detaches in place
    // (snap — no lean toward the new target beforehand), then a NEW one
    // grows in fresh at the new poster's position (form) — no horizontal
    // interpolation ties the two together at any point. `switchTarget` is
    // set immediately so the newly-clicked poster's ring/highlight appears
    // right away, ahead of the panel content swap.
    setSwitchTarget({ filmId, index })
    setConnectorPhase('snap')
    switchTimers.current.push(
      window.setTimeout(() => {
        setExpanded({ filmId, index })
        setSwitchTarget(null)
        setConnectorPhase('form')
      }, SWITCH_SNAP_MS),
    )
    switchTimers.current.push(window.setTimeout(() => setConnectorPhase('steady'), SWITCH_SNAP_MS + SWITCH_FORM_MS))
  }

  return (
    <section className="pb-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-serif text-2xl text-ink sm:text-3xl">Something new to watch</h2>
          <p className="mt-0.5 text-sm text-ink-soft">
            Unwatched (or not seen in a while) — from what&apos;s actually streaming right now.
          </p>
        </div>
        <div className="inline-flex rounded-md border border-line-strong bg-white p-0.5 dark:bg-paper-mid">
          {REC_PROFILE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setProfile(opt.value)}
              aria-pressed={profile === opt.value}
              className={`min-h-11 rounded px-2.5 text-xs font-medium transition sm:min-h-0 sm:py-1 ${
                profile === opt.value ? 'bg-clay text-paper' : 'text-ink-mid hover:bg-oat'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Row 1: genres — smaller pills + tighter gaps so the full set fits
          one row on a normal desktop width without needing to scroll. */}
      <div className="mt-4 flex flex-wrap gap-1">
        {GENRES.map((g) => (
          <FilterPill key={g} size="sm" active={genres.includes(g)} onClick={() => setGenres((prev) => toggleInArray(prev, g))}>
            {g}
          </FilterPill>
        ))}
      </div>

      {/* Row 2: runtime buckets + vibe. */}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {RUNTIME_BUCKET_OPTIONS.map((opt) => (
          <FilterPill
            key={opt.value}
            active={runtimeBuckets.includes(opt.value)}
            onClick={() => setRuntimeBuckets((prev) => toggleInArray(prev, opt.value))}
          >
            {opt.label}
          </FilterPill>
        ))}
        <select
          value={vibe}
          onChange={(e) => setVibe(e.target.value)}
          className="min-h-11 rounded-md border border-line-strong bg-white px-2 py-1.5 text-xs text-ink-mid outline-none focus:border-clay sm:min-h-0 dark:bg-paper-mid"
        >
          {VIBE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      <div className="mt-5">
        {loading && (
          <div
            className="grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8"
            aria-hidden
          >
            {Array.from({ length: columns }).map((_, i) => (
              <div key={i} className="aspect-2/3 animate-pulse rounded-sm bg-paper-deep" />
            ))}
          </div>
        )}

        {error && !loading && (
          <div className="rounded-lg border border-fig/30 bg-fig/10 px-4 py-3 text-sm text-fig">
            Nothing here yet — {error}
          </div>
        )}

        {!loading && !error && visible.length === 0 && (
          <p className="text-sm text-ink-soft">Nothing matches these filters right now — try loosening one.</p>
        )}

        {!loading && !error && visible.length > 0 && (
          <div
            // `relative z-10`: the brace connector below deliberately overshoots
            // a little past full height on landing (the "liquid wobble" —
            // BraceConnector's `tooth` briefly exceeds 1), which pokes its tip
            // above its own box for an instant. Without this, that instant
            // painted over the bottom of the poster row (later DOM = later
            // paint, same as any unstacked siblings) — a visible flash of the
            // dark connector on top of the artwork. Stacking the row above it
            // means any overshoot is simply hidden behind the posters instead.
            className="relative z-10 grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8"
            style={{ perspective: '800px' }}
          >
            {visible.map((item, index) => (
              <MovieCard
                key={item.film.id}
                movie={{
                  id: item.film.id,
                  title: item.film.title,
                  year: item.film.year != null ? String(item.film.year) : null,
                  overview: null,
                  poster: item.film.poster,
                  vote_average: null,
                }}
                onClick={() => handleCardClick(item.film.id, index)}
                expanded={(switchTarget ?? expanded)?.filmId === item.film.id}
              />
            ))}
          </div>
        )}

        <AnimatePresence>
          {expanded && (
            <div key="expansion">
              <BraceConnector peakPercent={((expanded.index + 0.5) / columns) * 100} phase={connectorPhase} />
              <RecommendationExpansionPanel
                filmId={expanded.filmId}
                originPercent={((expanded.index + 0.5) / columns) * 100}
                onNavigate={(id) => setExpanded((prev) => (prev ? { filmId: id, index: prev.index } : prev))}
                onClose={() => setExpanded(null)}
                onOpenOverlay={setOverlayFilmId}
              />
            </div>
          )}
        </AnimatePresence>

        <AnimatePresence>
          {overlayFilmId != null && (
            <DetailDrawer
              key="detail-drawer"
              filmId={overlayFilmId}
              onClose={() => setOverlayFilmId(null)}
              onNavigate={setOverlayFilmId}
            />
          )}
        </AnimatePresence>
      </div>
    </section>
  )
}

type PillTone = 'ok' | 'warn' | 'error' | 'neutral'

const PILL_TONES: Record<PillTone, string> = {
  ok: 'bg-olive/15 text-olive',
  warn: 'bg-kraft/20 text-clay-deep',
  error: 'bg-fig/15 text-fig',
  neutral: 'bg-oat text-ink-mid',
}

function StatusPill({ health, error }: { health: Health | null; error: string | null }) {
  let tone: PillTone = 'neutral'
  let label = 'Connecting…'
  if (error) {
    tone = 'error'
    label = 'Server offline'
  } else if (health && !health.tmdb_configured) {
    tone = 'warn'
    label = 'Add TMDB key'
  } else if (health) {
    tone = 'ok'
    label = `Connected · ${health.region}`
  }
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium ${PILL_TONES[tone]}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {label}
    </span>
  )
}

/** Header icon-button that switches App into the settings view — matches
 * ThemeToggle's compact pill idiom so the two sit naturally side by side. */
function SettingsButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Streaming service settings"
      title="Streaming service settings"
      className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-line-strong bg-white text-ink-mid transition hover:bg-oat hover:text-ink dark:bg-paper-mid"
    >
      <svg viewBox="0 0 20 20" aria-hidden className="h-4 w-4">
        <path
          d="M10 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
        />
        <path
          d="M16.3 11.2c.04-.4.07-.8.07-1.2s-.03-.8-.07-1.2l1.4-1.1a.6.6 0 0 0 .14-.77l-1.33-2.3a.6.6 0 0 0-.73-.26l-1.65.66a5.6 5.6 0 0 0-1.04-.6l-.25-1.75a.6.6 0 0 0-.6-.5H8.76a.6.6 0 0 0-.6.5l-.25 1.75c-.37.15-.72.35-1.04.6l-1.65-.66a.6.6 0 0 0-.73.26L3.16 6.9a.6.6 0 0 0 .14.77l1.4 1.1c-.04.4-.07.8-.07 1.2s.03.8.07 1.2l-1.4 1.1a.6.6 0 0 0-.14.77l1.33 2.3c.15.26.46.36.73.26l1.65-.66c.32.25.67.45 1.04.6l.25 1.75c.05.3.3.5.6.5h2.66c.3 0 .55-.2.6-.5l.25-1.75c.37-.15.72-.35 1.04-.6l1.65.66c.27.1.58 0 .73-.26l1.33-2.3a.6.6 0 0 0-.14-.77l-1.4-1.1Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  )
}

function SignOutButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Sign out"
      title="Sign out"
      className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-line-strong bg-white text-ink-mid transition hover:bg-oat hover:text-ink dark:bg-paper-mid"
    >
      <svg viewBox="0 0 20 20" aria-hidden className="h-4 w-4">
        <path
          d="M8 4H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3M13 13.5 16.5 10 13 6.5M7 10h9.3"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  )
}

/** A small, tasteful cat-ear mark next to the wordmark — not an emoji, part of the brand. */
function CatMark() {
  return (
    <svg viewBox="0 0 32 28" aria-hidden className="h-7 w-8 text-clay">
      <path
        d="M4,9 L2,1.5 L10,7.5 Q16,4 22,7.5 L30,1.5 L28,9 Q30.5,14.5 28,20 Q24.5,26 16,26 Q7.5,26 4,20 Q1.5,14.5 4,9 Z"
        fill="currentColor"
      />
      <circle cx="12" cy="16.5" r="1.6" fill="var(--color-paper)" />
      <circle cx="20" cy="16.5" r="1.6" fill="var(--color-paper)" />
      <path d="M15,20 L17,20 L16,21.3 Z" fill="var(--color-paper)" />
    </svg>
  )
}

type View = 'catalogue' | 'owned' | 'upcoming' | 'services' | 'settings'

const NAV_TABS: { view: View; label: string }[] = [
  { view: 'catalogue', label: 'Cat-alogue' },
  { view: 'owned', label: 'Owned' },
  { view: 'upcoming', label: 'Coming soon' },
  { view: 'services', label: 'Services' },
]

/** Gates the whole app behind the two-person login (docs/phases/PHASE-4-accounts-feedback.md).
 * `bootstrap()` tries a silent refresh from a stored refresh token on first
 * mount so a page reload doesn't force a re-login; `subscribe()` re-renders
 * this the moment auth state changes (login, logout, or a forced logout
 * from api.ts when a session dies server-side). */
export default function App() {
  const [user, setUser] = useState<AuthUser | null>(getUser())
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const unsubscribe = subscribe(() => setUser(getUser()))
    bootstrap().finally(() => {
      setUser(getUser())
      setReady(true)
    })
    return unsubscribe
  }, [])

  if (!ready) {
    return <div className="min-h-full bg-paper" />
  }
  if (!user) {
    return <LoginScreen onLoggedIn={() => setUser(getUser())} />
  }
  return <AuthenticatedApp />
}

function AuthenticatedApp() {
  const [view, setView] = useState<View>('catalogue')
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [selectedFilmId, setSelectedFilmId] = useState<number | null>(null)

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setHealthError(String(e)))
  }, [])

  // Below `sm`, the header is two rows (wordmark+controls, then nav tabs) —
  // tall enough that Catalogue's own sticky filter bar (which sticks right
  // under this header) needs real breathing room below it. Collapsing row 1
  // away on scroll-down (row 2's tabs stay put, just slide up to take its
  // place) buys that room back instead of stacking two sticky bars' full
  // height permanently. `--app-header-h` is measured live off the actual
  // header element (rather than assumed) so Catalogue's sticky offset never
  // drifts out of sync with whatever height the header currently is.
  const headerRef = useRef<HTMLElement>(null)
  const [collapseTopBar, setCollapseTopBar] = useState(false)
  const lastScrollY = useRef(0)

  useEffect(() => {
    function onScroll() {
      const y = window.scrollY
      const delta = y - lastScrollY.current
      if (y <= 12) {
        setCollapseTopBar(false)
      } else if (delta > 4) {
        setCollapseTopBar(true)
      } else if (delta < -4) {
        setCollapseTopBar(false)
      }
      lastScrollY.current = y
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    const el = headerRef.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([entry]) => {
      document.documentElement.style.setProperty('--app-header-h', `${entry.contentRect.height}px`)
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  return (
    <div className="min-h-full bg-paper text-ink">
      <header ref={headerRef} className="sticky top-0 z-20 border-b border-line bg-paper/95">
        <div className="mx-auto max-w-6xl px-5 py-3 sm:py-4">
          {/* Row 1 (wordmark + desktop nav + controls) collapses to nothing
              on mobile scroll-down via the grid-rows 1fr/0fr trick (animates
              cleanly without knowing the row's pixel height up front) — the
              `sm:grid-rows-[1fr]` override means desktop never collapses it,
              since the desktop single-row header doesn't have the height
              problem this is solving. */}
          <div
            className={`grid overflow-hidden transition-[grid-template-rows] duration-200 ease-out sm:grid-rows-[1fr] ${
              collapseTopBar ? 'grid-rows-[0fr]' : 'grid-rows-[1fr]'
            }`}
          >
            <div className="min-h-0 overflow-hidden">
              <div className="flex items-center justify-between gap-3 pb-3 sm:pb-0">
                <div className="flex shrink-0 items-center gap-2.5">
                  <CatMark />
                  <span className="font-display text-lg font-medium tracking-[-0.005em]">
                    Mishka <span className="text-clay">Hub</span>
                  </span>
                </div>
                {/* Desktop/tablet: tabs sit inline, centered between the wordmark
                    and the controls. Below `sm`, there's not enough width for
                    logo + 4 tabs + 4 controls on one line without either
                    truncating text or squeezing the tabs down to nothing — so
                    they move to their own full-width row instead (below). */}
                <nav className="hidden items-center gap-1 sm:flex">
                  {NAV_TABS.map((tab) => (
                    <button
                      key={tab.view}
                      type="button"
                      onClick={() => setView(tab.view)}
                      aria-pressed={view === tab.view}
                      className={`rounded-md px-2.5 py-1.5 text-xs font-medium transition ${
                        view === tab.view ? 'bg-clay/10 text-clay-deep' : 'text-ink-mid hover:bg-oat'
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </nav>
                <div className="flex shrink-0 items-center gap-2 sm:gap-3">
                  <SettingsButton onClick={() => setView(view === 'settings' ? 'catalogue' : 'settings')} />
                  <ThemeToggle />
                  <SignOutButton onClick={logout} />
                </div>
              </div>
            </div>
          </div>
          {/* Mobile-only second row: full viewport width to scroll within,
              rather than fighting the logo/controls for leftover space. */}
          <nav className="-mx-5 flex items-center gap-1 overflow-x-auto px-5 sm:hidden">
            {NAV_TABS.map((tab) => (
              <button
                key={tab.view}
                type="button"
                onClick={() => setView(tab.view)}
                aria-pressed={view === tab.view}
                className={`min-h-11 shrink-0 rounded-md px-2.5 text-xs font-medium transition ${
                  view === tab.view ? 'bg-clay/10 text-clay-deep' : 'text-ink-mid hover:bg-oat'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-5 pb-24 pt-8">
        {view === 'settings' ? (
          <SettingsPage onBack={() => setView('catalogue')} />
        ) : view === 'owned' ? (
          <OwnedPage />
        ) : view === 'upcoming' ? (
          <UpcomingPage />
        ) : view === 'services' ? (
          <ServiceInsightsPage />
        ) : (
          <>
            <div className="pb-8 text-center">
              <h1 className="mx-auto max-w-2xl text-balance font-serif text-4xl font-normal tracking-[-0.005em] text-ink sm:text-5xl">
                Films worth your night in.
              </h1>
              <div className="mt-6">
                <SearchAutocomplete onSelect={setSelectedFilmId} />
              </div>
            </div>

            {health && !health.tmdb_configured && (
              <p className="mb-4 text-xs text-clay-deep">
                Heads up: add your TMDB key to <code className="rounded bg-oat px-1 font-mono">server/.env</code>{' '}
                and restart the server for search to return results.
              </p>
            )}

            <div className="border-t border-line pt-6">
              {selectedFilmId != null && <FilmExplorer filmId={selectedFilmId} onSelect={setSelectedFilmId} />}

              <UnseenRecommendationsRow />
            </div>

            <div className="mt-10 border-t border-line pt-10">
              <Catalogue />
            </div>
          </>
        )}
      </main>

      <footer className="border-t border-line py-6 text-center font-mono text-[11px] text-ink-soft">
        <div className="mb-3 flex justify-center">
          <StatusPill health={health} error={healthError} />
        </div>
        <p>This product uses the TMDB API but is not endorsed or certified by TMDB.</p>
        <p>Streaming availability by JustWatch. Mishka Hub is a private, non-commercial project.</p>
      </footer>
    </div>
  )
}
