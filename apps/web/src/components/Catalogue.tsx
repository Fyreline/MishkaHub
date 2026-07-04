import { useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence, motion } from 'motion/react'
import {
  api,
  ApiError,
  type FilmSort,
  type FilmSummary,
  type GetFilmsParams,
  type ImportJob,
  type UnmatchedImport,
} from '../api'
import { MovieCard, type CatalogueBadgeInfo } from './MovieCard'
import {
  FilmHeaderSkeleton,
  MoreLikeThisSection,
  UserRatingColumns,
  UserRatingColumnsSkeleton,
  WhereToWatchSection,
} from './FilmDetailSections'
import { sourceLabel, useFilmDetail } from '../useFilmDetail'

const PAGE_SIZE = 60
const DECADES = [1980, 1990, 2000, 2010, 2020] as const
const GENRES = [
  'Action',
  'Adventure',
  'Animation',
  'Comedy',
  'Crime',
  'Documentary',
  'Drama',
  'Fantasy',
  'Horror',
  'Mystery',
  'Romance',
  'Sci-Fi',
  'Thriller',
]

type UserFilter = 'me' | 'partner' | 'both' | 'either'

const USER_FILTER_LABELS: Record<UserFilter, string> = {
  me: 'Meowck',
  partner: 'Meowmy',
  both: 'Both',
  either: 'Either',
}

/** Resolve a filter mode to the numeric user id the API's `my` block should
 * represent. 'both'/'either' have no single-user API equivalent, so they fall
 * back to user 1 (Meowck) as the base fetch — see the `params` useMemo below. */
function resolveUserId(filter: UserFilter): 1 | 2 {
  return filter === 'partner' ? 2 : 1
}

/** Turn card-level watch state into the two-dot indicator + badges MovieCard
 * expects. `requestedUserId` is whichever user the API's `my` block actually
 * represents (see `resolveUserId`) — `my`/`partner` swap depending on which
 * user was requested, so this must NOT hardcode my=Luminal/partner=Garfield. */
function toBadges(film: FilmSummary, requestedUserId: 1 | 2): CatalogueBadgeInfo {
  const luminal = requestedUserId === 1 ? film.my : film.partner
  const garfield = requestedUserId === 1 ? film.partner : film.my
  return {
    myRating: film.my.rating,
    liked: luminal.liked || garfield.liked,
    rewatched: luminal.watch_count > 1 || garfield.watch_count > 1,
    seenBy: {
      luminal: luminal.watch_count > 0,
      garfield: garfield.watch_count > 0,
    },
  }
}

// ---------------------------------------------------------------------------
// Filter bar
// ---------------------------------------------------------------------------

function FilterBar({
  userFilter,
  onUserFilter,
  ratedOnly,
  onRatedOnly,
  likedOnly,
  onLikedOnly,
  decade,
  onDecade,
  genre,
  onGenre,
  minRating,
  onMinRating,
  search,
  onSearch,
  sort,
  onSort,
}: {
  userFilter: UserFilter
  onUserFilter: (v: UserFilter) => void
  ratedOnly: boolean
  onRatedOnly: (v: boolean) => void
  likedOnly: boolean
  onLikedOnly: (v: boolean) => void
  decade: number | null
  onDecade: (v: number | null) => void
  genre: string
  onGenre: (v: string) => void
  minRating: number
  onMinRating: (v: number) => void
  search: string
  onSearch: (v: string) => void
  sort: FilmSort
  onSort: (v: FilmSort) => void
}) {
  // On narrow screens the full control set (user toggle, Rated, Liked, 5
  // decade chips, genre select, min-rating slider, search, sort) is too dense
  // to lay out well even with wrapping — it was pushing 500+px of sticky
  // chrome above the fold. Keep the two most-used controls (user toggle,
  // search) always visible on mobile, and tuck the rest behind a "Filters"
  // disclosure. Desktop (sm:+) keeps the original always-expanded row
  // layout untouched.
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false)
  const activeFilterCount =
    (ratedOnly ? 1 : 0) + (likedOnly ? 1 : 0) + (decade ? 1 : 0) + (genre ? 1 : 0) + (minRating > 0 ? 1 : 0)

  const userToggle = (
    <div className="flex overflow-hidden rounded-md border border-line-strong text-xs font-medium">
      {(['me', 'partner', 'both', 'either'] as UserFilter[]).map((v) => (
        <button
          key={v}
          type="button"
          onClick={() => onUserFilter(v)}
          aria-pressed={userFilter === v}
          className={`px-2.5 py-3.5 transition sm:py-1.5 ${
            userFilter === v ? 'bg-clay text-paper' : 'bg-white dark:bg-paper-mid text-ink-mid hover:bg-oat'
          }`}
        >
          {USER_FILTER_LABELS[v]}
        </button>
      ))}
    </div>
  )

  const ratedToggle = (
    <button
      type="button"
      onClick={() => onRatedOnly(!ratedOnly)}
      aria-pressed={ratedOnly}
      className={`min-h-11 rounded-md border px-2.5 py-1.5 text-xs font-medium transition sm:min-h-0 ${
        ratedOnly ? 'border-clay bg-clay/10 text-clay-deep' : 'border-line-strong bg-white dark:bg-paper-mid text-ink-mid hover:bg-oat'
      }`}
    >
      Rated
    </button>
  )

  const likedToggle = (
    <button
      type="button"
      onClick={() => onLikedOnly(!likedOnly)}
      aria-pressed={likedOnly}
      className={`min-h-11 rounded-md border px-2.5 py-1.5 text-xs font-medium transition sm:min-h-0 ${
        likedOnly ? 'border-fig bg-fig/10 text-fig' : 'border-line-strong bg-white dark:bg-paper-mid text-ink-mid hover:bg-oat'
      }`}
    >
      ♥ Liked
    </button>
  )

  const decadeChips = (
    <div className="flex flex-wrap gap-1.5 sm:gap-1">
      {DECADES.map((d) => (
        <button
          key={d}
          type="button"
          onClick={() => onDecade(decade === d ? null : d)}
          aria-pressed={decade === d}
          className={`min-h-11 rounded-full border px-3 py-1 text-xs font-medium transition sm:min-h-0 sm:px-2.5 ${
            decade === d ? 'border-clay bg-clay text-paper' : 'border-line-strong bg-white dark:bg-paper-mid text-ink-mid hover:bg-oat'
          }`}
        >
          {d}s
        </button>
      ))}
    </div>
  )

  const genreSelect = (
    <select
      value={genre}
      onChange={(e) => onGenre(e.target.value)}
      className="min-h-11 w-full rounded-md border border-line-strong bg-white dark:bg-paper-mid px-2 py-1.5 text-xs text-ink-mid outline-none focus:border-clay sm:min-h-0 sm:w-auto"
    >
      <option value="">All genres</option>
      {GENRES.map((g) => (
        <option key={g} value={g}>
          {g}
        </option>
      ))}
    </select>
  )

  const minRatingSlider = (
    <label className="flex items-center gap-2 text-xs text-ink-soft">
      Min ★
      <input
        type="range"
        min={0}
        max={5}
        step={0.5}
        value={minRating}
        onChange={(e) => onMinRating(Number(e.target.value))}
        className="h-6 flex-1 accent-clay sm:h-auto sm:flex-none"
      />
      <span className="font-mono text-[11px] text-ink-mid">{minRating.toFixed(1)}</span>
    </label>
  )

  const sortSelect = (
    <select
      value={sort}
      onChange={(e) => onSort(e.target.value as FilmSort)}
      className="min-h-11 rounded-md border border-line-strong bg-white dark:bg-paper-mid px-2 py-1.5 text-xs text-ink-mid outline-none focus:border-clay sm:min-h-0"
    >
      <option value="watched_desc">Recently watched</option>
      <option value="rating_desc">Rating</option>
      <option value="year">Year</option>
      <option value="title">Title</option>
    </select>
  )

  return (
    <div className="sticky top-[65px] z-10 -mx-5 border-y border-line bg-paper/95 px-5 py-3 backdrop-saturate-150">
      {/* Mobile layout (< sm): always-visible user toggle + search, rest
          behind a "Filters" disclosure. Hidden entirely at sm:+ in favour of
          the always-expanded desktop row below. */}
      <div className="flex flex-col gap-3 sm:hidden">
        <div className="flex items-center justify-between gap-3">
          {userToggle}
          <button
            type="button"
            onClick={() => setMobileFiltersOpen((v) => !v)}
            aria-expanded={mobileFiltersOpen}
            className="flex min-h-11 items-center gap-1.5 rounded-md border border-line-strong bg-white dark:bg-paper-mid px-3 text-xs font-medium text-ink-mid transition hover:bg-oat"
          >
            Filters
            {activeFilterCount > 0 && (
              <span className="rounded-full bg-clay px-1.5 py-0.5 font-mono text-[10px] text-paper">
                {activeFilterCount}
              </span>
            )}
            <span aria-hidden className={`transition-transform ${mobileFiltersOpen ? 'rotate-180' : ''}`}>
              ▾
            </span>
          </button>
        </div>

        <input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search title…"
          className="min-h-11 w-full rounded-md border border-line-strong bg-white dark:bg-paper-mid px-3 py-1.5 text-xs text-ink outline-none placeholder:text-cloud focus:border-clay"
        />

        {mobileFiltersOpen && (
          <div className="flex flex-col gap-3 border-t border-line pt-3">
            <div className="flex flex-wrap gap-2">
              {ratedToggle}
              {likedToggle}
            </div>
            {decadeChips}
            <div className="grid grid-cols-2 gap-2">
              {genreSelect}
              {sortSelect}
            </div>
            <div className="rounded-md border border-line-strong bg-white dark:bg-paper-mid px-3 py-2">{minRatingSlider}</div>
          </div>
        )}
      </div>

      {/* Desktop layout (sm:+): original always-expanded single/second row. */}
      <div className="hidden flex-col gap-3 sm:flex">
        <div className="flex flex-wrap items-center justify-between gap-3">
          {userToggle}
          {ratedToggle}
          {likedToggle}
          {decadeChips}
          {genreSelect}
          {minRatingSlider}
        </div>

        <div className="flex items-center gap-3">
          <input
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search title…"
            className="min-w-[10rem] flex-1 rounded-md border border-line-strong bg-white dark:bg-paper-mid px-3 py-1.5 text-xs text-ink outline-none placeholder:text-cloud focus:border-clay"
          />
          {sortSelect}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Detail drawer
// ---------------------------------------------------------------------------

export function DetailDrawer({
  filmId,
  onClose,
  onNavigate,
}: {
  filmId: number
  onClose: () => void
  onNavigate: (id: number) => void
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

  // Progressive scroll-blur on the sticky header: 0 at the top, ramping to 1
  // over MAX_BLUR_SCROLL px, then capped (a "little bit" of scroll per spec).
  const [scrollProgress, setScrollProgress] = useState(0)
  const MAX_BLUR_SCROLL = 140

  function handleScroll(e: React.UIEvent<HTMLDivElement>) {
    const top = e.currentTarget.scrollTop
    setScrollProgress(Math.max(0, Math.min(1, top / MAX_BLUR_SCROLL)))
  }

  // Lock the main page's scroll while the drawer is open — only the drawer
  // itself should scroll, not the Cat-alogue behind it.
  useEffect(() => {
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previousOverflow
    }
  }, [])

  return (
    <div className="fixed inset-0 z-30">
      {/* Backdrop dimmer — pinned to a fixed dark scrim (not the `ink` token,
          which flips to a light color in dark mode and would brighten the
          page behind the drawer instead of dimming it). */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.18 }}
        className="absolute inset-0 bg-black/30"
        onClick={onClose}
        aria-hidden
      />
      <motion.div
        initial={{ opacity: 0, x: 24 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: 24 }}
        transition={{ duration: 0.2, ease: 'easeOut' }}
        role="dialog"
        aria-modal="true"
        onScroll={handleScroll}
        className="fixed inset-0 overflow-y-auto bg-paper shadow-float sm:inset-x-auto sm:right-0 sm:top-0 sm:h-full sm:w-full sm:max-w-md"
      >
        <div
          className="sticky top-0 z-20 flex items-center justify-between px-5 pb-4 transition-colors"
          style={{
            paddingTop: 'max(1rem, env(safe-area-inset-top))',
            // `--color-paper`/`--color-ink` already flip for dark mode (see
            // .dark overrides in index.css) — mix against `transparent`
            // instead of hardcoding a light-mode RGB triple, so this
            // progressive scroll-blur header stays theme-aware without
            // duplicating the palette here.
            backgroundColor: `color-mix(in srgb, var(--color-paper) ${scrollProgress * 92}%, transparent)`,
            backdropFilter: `blur(${scrollProgress * 18}px)`,
            WebkitBackdropFilter: `blur(${scrollProgress * 18}px)`,
            borderBottom:
              scrollProgress > 0.05
                ? 'color-mix(in srgb, var(--color-ink) 10%, transparent) 1px solid'
                : '1px solid transparent',
          }}
        >
          <span className="font-display text-sm font-medium text-ink-mid">Detail</span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md px-2 py-1 text-sm text-ink-soft transition hover:bg-oat hover:text-ink"
          >
            Close
          </button>
        </div>

        {loading && (
          <div className="space-y-4 p-5">
            <FilmHeaderSkeleton />
            <UserRatingColumnsSkeleton />
          </div>
        )}
        {error && !loading && (
          <div className="p-5 text-sm text-fig">Couldn&apos;t load this film yet — {error}</div>
        )}

        {detail && !loading && (
          <div>
            {detail.backdrop && (
              <img src={detail.backdrop} alt="" className="aspect-video w-full object-cover" />
            )}
            {/* Bottom padding accounts for the iPhone home-indicator safe area so the
                final "More like this" row isn't flush against it. */}
            <div
              className="space-y-4 p-5"
              style={{ paddingBottom: 'max(1.25rem, env(safe-area-inset-bottom))' }}
            >
              <div>
                <h3 className="font-serif text-xl text-ink">{detail.title}</h3>
                <p className="mt-0.5 text-xs text-ink-soft">
                  {detail.year ?? '—'}
                  {detail.runtime_min ? ` · ${detail.runtime_min} min` : ''}
                </p>
                <button
                  type="button"
                  onClick={() => setRematchOpen((v) => !v)}
                  className="mt-1 text-[11px] text-cloud underline decoration-dotted underline-offset-2 transition hover:text-ink-soft"
                >
                  Wrong film?
                </button>
              </div>

              {rematchOpen && (
                <div className="rounded-md border border-line-strong bg-paper-mid p-3">
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
                      className="min-w-0 flex-1 rounded-md border border-line-strong bg-white dark:bg-paper px-2 py-1.5 text-sm text-ink placeholder:text-cloud"
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
                              {c.poster && (
                                <img src={c.poster} alt="" className="h-full w-full object-cover" />
                              )}
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

              {detail.source && (
                <span className="inline-flex w-fit items-center rounded-full bg-oat px-2.5 py-1 font-mono text-[11px] text-ink-mid">
                  {sourceLabel(detail.source)}
                </span>
              )}

              {detail.overview && <p className="text-sm leading-relaxed text-ink-mid">{detail.overview}</p>}

              {detail.genres && detail.genres.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {detail.genres.map((g) => (
                    <span key={g} className="rounded-full border border-line-strong px-2 py-0.5 text-[11px] text-ink-soft">
                      {g}
                    </span>
                  ))}
                </div>
              )}

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

              <MoreLikeThisSection
                similar={similar}
                similarLoading={similarLoading}
                similarError={similarError}
                onNavigate={onNavigate}
              />
            </div>
          </div>
        )}
      </motion.div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Import status banner
// ---------------------------------------------------------------------------

function ImportBanner({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<ImportJob | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    async function poll() {
      try {
        const j = await api.getImportJob(jobId)
        if (cancelled) return
        setJob(j)
        if (j.status === 'running') {
          timer = setTimeout(poll, 3000)
        }
      } catch {
        // Backend not up yet — stop polling quietly, banner stays in its last known state.
      }
    }
    poll()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [jobId])

  if (!job) {
    return (
      <div className="rounded-lg border border-line bg-paper-mid px-4 py-3 text-sm text-ink-soft">
        Starting import…
      </div>
    )
  }

  const failedStep = job.cascade.find((s) => s.outcome === 'failed')
  const fellThrough =
    failedStep &&
    job.source_used &&
    job.source_used !== failedStep.source &&
    job.cascade.some((s) => s.outcome === 'ok')

  return (
    <div className="rounded-lg border border-line bg-paper-mid px-4 py-3 text-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="font-medium text-ink">
          {job.status === 'running' ? 'Importing…' : job.status === 'failed' ? 'Import failed' : 'Import complete'}
        </span>
        <span className="font-mono text-[11px] uppercase tracking-wide text-ink-soft">
          {job.source_used ?? job.source_requested}
        </span>
      </div>
      {job.stage && <p className="mt-1 text-ink-soft">{job.stage}</p>}
      {fellThrough && (
        <p className="mt-1 text-clay-deep">
          {failedStep.source} failed, reading public profile instead.
        </p>
      )}
      {job.counts && (
        <p className="mt-1 font-mono text-[11px] text-ink-soft">
          watched {job.counts.watched} · ratings {job.counts.ratings} · likes {job.counts.likes} · matched{' '}
          {job.counts.matched} · unmatched {job.counts.unmatched}
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Empty state — Connect Letterboxd
// ---------------------------------------------------------------------------

function ConnectLetterboxd({ onImportStarted }: { onImportStarted: (jobId: string) => void }) {
  const [password, setPassword] = useState('')
  const [tosAck, setTosAck] = useState(false)
  const [credError, setCredError] = useState<string | null>(null)
  const [credBusy, setCredBusy] = useState(false)

  const [zipFile, setZipFile] = useState<File | null>(null)
  const [zipError, setZipError] = useState<string | null>(null)
  const [zipBusy, setZipBusy] = useState(false)

  async function onCredSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!password || !tosAck) return
    setCredBusy(true)
    setCredError(null)
    try {
      await api.setCredentials(password, tosAck)
      const run = await api.runImport(1, 'auto')
      onImportStarted(run.job_id)
    } catch (err) {
      setCredError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setCredBusy(false)
    }
  }

  async function onZipChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0] ?? null
    setZipFile(file)
    setZipError(null)
    if (!file) return
    setZipBusy(true)
    try {
      const run = await api.uploadImportZip(1, file)
      onImportStarted(run.job_id)
    } catch (err) {
      setZipError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setZipBusy(false)
    }
  }

  return (
    <div className="mt-6 rounded-lg border border-line bg-paper-mid p-8">
      <h3 className="text-center font-serif text-xl text-ink">Connect Letterboxd</h3>
      <p className="mx-auto mt-2 max-w-md text-center text-sm text-ink-soft">
        Nothing here yet. Bring your watch history home one of two ways.
      </p>

      <div className="mt-8 grid gap-6 sm:grid-cols-2">
        <form onSubmit={onCredSubmit} className="rounded-md border border-line bg-white dark:bg-paper-mid p-5">
          <h4 className="text-sm font-medium text-ink">Automatic import</h4>
          <p className="mt-1 text-xs text-ink-soft">
            We use your Letterboxd password once, to fetch your export, then never store it in the clear.
          </p>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Letterboxd password"
            className="mt-3 w-full rounded-md border border-line-strong bg-white dark:bg-paper-mid px-3 py-2 text-sm outline-none placeholder:text-cloud focus:border-clay"
          />
          <label className="mt-3 flex items-start gap-2 text-xs text-ink-soft">
            <input
              type="checkbox"
              checked={tosAck}
              onChange={(e) => setTosAck(e.target.checked)}
              className="mt-0.5 accent-clay"
            />
            I acknowledge this reads my Letterboxd account per its Terms of Service.
          </label>
          <button
            type="submit"
            disabled={!password || !tosAck || credBusy}
            className="mt-3 w-full rounded-md bg-clay px-4 py-2 text-sm font-medium text-paper transition hover:bg-clay-deep disabled:opacity-50"
          >
            {credBusy ? 'Connecting…' : 'Connect & import'}
          </button>
          {credError && <p className="mt-2 text-xs text-fig">{credError}</p>}
        </form>

        <div className="rounded-md border border-line bg-white dark:bg-paper-mid p-5">
          <h4 className="text-sm font-medium text-ink">Upload your export</h4>
          <p className="mt-1 text-xs text-ink-soft">
            Settings → Import &amp; Export → Export your data on Letterboxd, then drop the ZIP here.
          </p>
          <label className="mt-3 flex cursor-pointer flex-col items-center justify-center rounded-md border border-dashed border-line-strong px-4 py-6 text-center text-xs text-ink-soft transition hover:border-clay hover:bg-oat/40">
            <input type="file" accept=".zip" onChange={onZipChange} className="hidden" />
            {zipFile ? zipFile.name : 'Choose or drop a .zip file'}
          </label>
          {zipBusy && <p className="mt-2 text-xs text-ink-soft">Uploading…</p>}
          {zipError && <p className="mt-2 text-xs text-fig">{zipError}</p>}
          <p className="mt-3 text-[11px] text-cloud">
            Server admins can also drop exports directly into the watched folder.
          </p>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Unmatched queue chip
// ---------------------------------------------------------------------------

function UnmatchedQueue() {
  const [items, setItems] = useState<UnmatchedImport[]>([])
  const [expanded, setExpanded] = useState(false)
  const [dismissed, setDismissed] = useState(false)
  const [tmdbInputs, setTmdbInputs] = useState<Record<number, string>>({})
  const [busyId, setBusyId] = useState<number | null>(null)

  useEffect(() => {
    api
      .getUnmatched('pending')
      .then((res) => setItems(res.items))
      .catch(() => {
        // Backend not live yet — the chip simply stays hidden.
      })
  }, [])

  async function resolve(id: number) {
    const raw = tmdbInputs[id]
    const tmdbId = raw ? Number(raw) : NaN
    if (Number.isNaN(tmdbId)) return
    setBusyId(id)
    try {
      await api.resolveUnmatched(id, { tmdb_id: tmdbId })
      setItems((prev) => prev.filter((i) => i.id !== id))
    } catch {
      // Leave the row in place; user can retry.
    } finally {
      setBusyId(null)
    }
  }

  async function ignore(id: number) {
    setBusyId(id)
    try {
      await api.resolveUnmatched(id, { action: 'ignore' })
      setItems((prev) => prev.filter((i) => i.id !== id))
    } catch {
      // Leave the row in place; user can retry.
    } finally {
      setBusyId(null)
    }
  }

  if (dismissed || items.length === 0) return null

  return (
    <div className="rounded-lg border border-line bg-paper-mid px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-2 text-sm font-medium text-ink-mid"
        >
          <span className="rounded-full bg-clay px-2 py-0.5 font-mono text-[11px] text-paper">
            {items.length}
          </span>
          Unmatched imports need a nudge
        </button>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          className="text-xs text-ink-soft transition hover:text-ink"
        >
          Dismiss
        </button>
      </div>

      {expanded && (
        <ul className="mt-3 space-y-2">
          {items.map((item) => (
            <li key={item.id} className="flex flex-wrap items-center gap-2 rounded-md bg-white dark:bg-paper-mid px-3 py-2 text-sm">
              <span className="flex-1 text-ink-mid">
                {item.name} {item.year ? `(${item.year})` : ''}
              </span>
              <input
                type="number"
                placeholder="TMDB id"
                value={tmdbInputs[item.id] ?? ''}
                onChange={(e) => setTmdbInputs((prev) => ({ ...prev, [item.id]: e.target.value }))}
                className="w-24 rounded-md border border-line-strong px-2 py-1 text-xs outline-none focus:border-clay"
              />
              <button
                type="button"
                disabled={busyId === item.id}
                onClick={() => resolve(item.id)}
                className="rounded-md bg-clay px-2.5 py-1 text-xs font-medium text-paper transition hover:bg-clay-deep disabled:opacity-50"
              >
                Resolve
              </button>
              <button
                type="button"
                disabled={busyId === item.id}
                onClick={() => ignore(item.id)}
                className="rounded-md border border-line-strong px-2.5 py-1 text-xs text-ink-soft transition hover:bg-oat disabled:opacity-50"
              >
                Ignore
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main Catalogue view
// ---------------------------------------------------------------------------

export function Catalogue() {
  const [userFilter, setUserFilter] = useState<UserFilter>('me')
  const [ratedOnly, setRatedOnly] = useState(false)
  const [likedOnly, setLikedOnly] = useState(false)
  const [decade, setDecade] = useState<number | null>(null)
  const [genre, setGenre] = useState('')
  const [minRating, setMinRating] = useState(0)
  const [searchInput, setSearchInput] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [sort, setSort] = useState<FilmSort>('watched_desc')

  const [films, setFilms] = useState<FilmSummary[]>([])
  const [total, setTotal] = useState<number | null>(null)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [selectedFilmId, setSelectedFilmId] = useState<number | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)

  const debounceTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  // Debounce the title search input -> `q=`.
  useEffect(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current)
    debounceTimer.current = setTimeout(() => setDebouncedSearch(searchInput.trim()), 350)
    return () => clearTimeout(debounceTimer.current)
  }, [searchInput])

  const params = useMemo<GetFilmsParams>(() => {
    const p: GetFilmsParams = { sort, limit: PAGE_SIZE, offset: 0 }
    // Every response carries BOTH users' state (`my` + `partner`) regardless
    // of which id we request — that id only decides which side is labelled
    // `my` vs `partner`. For 'me'/'partner' the Cat-alogue should show ONLY
    // that person's watched films (this is their diary, not the whole
    // library), so filter server-side via `seen=true`. 'both'/'either' use
    // the household-wide `seen_by` param instead (2026-07-04 fix — these
    // used to fall through to an unfiltered fetch narrowed client-side,
    // which desynced `total`/pagination from what was actually shown and
    // made "load more" add only a handful of visible cards per page).
    p.user = resolveUserId(userFilter)
    if (userFilter === 'me' || userFilter === 'partner') p.seen = true
    if (userFilter === 'both' || userFilter === 'either') p.seen_by = userFilter
    if (ratedOnly) p.rated = true
    if (likedOnly) p.liked = true
    if (minRating > 0) p.min_rating = minRating
    if (decade) {
      p.year_from = decade
      p.year_to = decade + 9
    }
    if (genre) p.genre = genre
    if (debouncedSearch) p.q = debouncedSearch
    return p
  }, [userFilter, ratedOnly, likedOnly, minRating, decade, genre, debouncedSearch, sort])

  const filtersActive =
    userFilter !== 'either' || ratedOnly || likedOnly || !!decade || !!genre || minRating > 0 || !!debouncedSearch

  // Refetch from the top whenever filters/sort change.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setOffset(0)
    api
      .getFilms(params)
      .then((res) => {
        if (cancelled) return
        setFilms(res.items)
        setTotal(res.total)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
        setFilms([])
        setTotal(0)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params])

  async function loadMore() {
    const nextOffset = offset + PAGE_SIZE
    setLoading(true)
    try {
      const res = await api.getFilms({ ...params, offset: nextOffset })
      setFilms((prev) => [...prev, ...res.items])
      setTotal(res.total)
      setOffset(nextOffset)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  // userFilter (both/either) and minRating are now applied server-side via
  // `params` (seen_by / min_rating) so `total`/pagination stay accurate —
  // see the params useMemo above. `films` is already the filtered set.
  const visibleFilms = films

  const isEmpty = total === 0 && !loading && !error
  const showConnectCard = isEmpty && !filtersActive

  return (
    <section className="mt-20 border-t border-line pt-12">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="font-display text-2xl font-medium tracking-[-0.005em] text-ink">Cat-alogue</h2>
        <span className="font-mono text-[11px] uppercase tracking-[0.08em] text-cloud">
          what we&apos;ve watched
        </span>
      </div>

      <div className="mt-6 space-y-4">
        {jobId && <ImportBanner jobId={jobId} />}
        <UnmatchedQueue />
      </div>

      <FilterBar
        userFilter={userFilter}
        onUserFilter={setUserFilter}
        ratedOnly={ratedOnly}
        onRatedOnly={setRatedOnly}
        likedOnly={likedOnly}
        onLikedOnly={setLikedOnly}
        decade={decade}
        onDecade={setDecade}
        genre={genre}
        onGenre={setGenre}
        minRating={minRating}
        onMinRating={setMinRating}
        search={searchInput}
        onSearch={setSearchInput}
        sort={sort}
        onSort={setSort}
      />

      <div className="mt-6">
        {error && (
          <div className="rounded-lg border border-fig/30 bg-fig/10 px-4 py-3 text-sm text-fig">
            Nothing here yet — {error}
          </div>
        )}

        {loading && films.length === 0 && (
          <div className="grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8">
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="aspect-2/3 animate-pulse rounded-sm bg-paper-deep" />
            ))}
          </div>
        )}

        {!error && showConnectCard && <ConnectLetterboxd onImportStarted={setJobId} />}

        {!error && isEmpty && !showConnectCard && (
          <div className="rounded-lg border border-line bg-paper-mid p-10 text-center">
            <p className="font-serif text-lg text-ink-mid">Nothing here yet.</p>
            <p className="mx-auto mt-2 max-w-md text-sm text-ink-soft">
              Try loosening a filter or two — nothing matches this combination.
            </p>
          </div>
        )}

        {visibleFilms.length > 0 && (
          <>
            <div
              className="grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8"
              style={{ perspective: '800px' }}
            >
              {visibleFilms.map((film) => (
                <MovieCard
                  key={film.id}
                  movie={film}
                  badges={toBadges(film, resolveUserId(userFilter))}
                  onClick={() => setSelectedFilmId(film.id)}
                />
              ))}
            </div>

            {total != null && films.length < total && (
              <div className="mt-8 flex justify-center">
                <button
                  type="button"
                  onClick={loadMore}
                  disabled={loading}
                  className="rounded-md border border-line-strong bg-white dark:bg-paper-mid px-5 py-2.5 text-sm font-medium text-ink-mid transition hover:bg-oat disabled:opacity-50"
                >
                  {loading ? 'Loading…' : `Load more (${films.length} of ${total})`}
                </button>
              </div>
            )}
          </>
        )}
      </div>

      <AnimatePresence>
        {selectedFilmId != null && (
          <DetailDrawer
            key="detail-drawer"
            filmId={selectedFilmId}
            onClose={() => setSelectedFilmId(null)}
            onNavigate={setSelectedFilmId}
          />
        )}
      </AnimatePresence>
    </section>
  )
}
