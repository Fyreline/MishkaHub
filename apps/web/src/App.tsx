import { useEffect, useState, type FormEvent } from 'react'
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
import { Catalogue } from './components/Catalogue'
import { ThemeToggle } from './components/ThemeToggle'
import { SettingsPage } from './components/SettingsPage'
import { MoreLikeThisSection, UserRatingColumns, WhereToWatchSection } from './components/FilmDetailSections'
import { useFilmDetail } from './useFilmDetail'

type VibeOption = { value: string; label: string }

const VIBE_OPTIONS: VibeOption[] = [
  { value: '', label: 'Any vibe' },
  { value: 'slow_burn', label: 'Slow burn' },
  { value: 'feel_good', label: 'Feel good' },
  { value: 'sad', label: 'Sad' },
  { value: 'tense', label: 'Tense' },
  { value: 'dark', label: 'Dark' },
  { value: 'quick_watch', label: 'Quick watch' },
]

/** "You're looking at" + "More like this" panel — shown once a search result
 * (or a similar-film result) is selected. Recursively explorable: clicking a
 * similar-film card re-selects that film as the new focus. Search behavior
 * is unchanged by the homepage restructure — only the "Something new to
 * watch" row got a new expand-in-place interaction (see
 * UnseenRecommendationsRow below). */
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
      {detailLoading && <p className="text-sm text-ink-soft">Loading…</p>}
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
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`min-h-11 rounded-full border px-2.5 py-1 text-xs font-medium transition sm:min-h-0 ${
        active
          ? 'border-clay bg-clay/10 text-clay-deep'
          : 'border-line-strong bg-white text-ink-mid hover:bg-oat dark:bg-paper-mid'
      }`}
    >
      {children}
    </button>
  )
}

/** A small SVG connector — "vaguely like a curly bracket on its side" —
 * linking the poster card that was clicked to the expansion panel below it.
 * Positioned by percentage across the row so it tracks the clicked column
 * regardless of the current responsive column count. */
function BraceConnector({ leftPercent }: { leftPercent: number }) {
  return (
    <svg
      viewBox="0 0 48 20"
      preserveAspectRatio="none"
      aria-hidden
      className="pointer-events-none absolute top-0 h-5 w-12 -translate-x-1/2 text-clay/60"
      style={{ left: `${leftPercent}%` }}
    >
      <path
        d="M2 1 C2 12 21 5 24 17 C27 5 46 12 46 1"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  )
}

/** The horizontal expand-in-place detail view for a clicked recommendation
 * card: poster on the left, masked so it visually dissolves into the panel's
 * background toward the right instead of a hard edge, with the full detail
 * content (identical data/behavior to Catalogue.tsx's DetailDrawer, via the
 * shared useFilmDetail hook + FilmDetailSections) beginning around the
 * fade-out point. Deliberately a separate shell from DetailDrawer — the
 * Cat-alogue's own click-through is untouched — but both consume the exact
 * same underlying hook/sections, so there's no behavior drift between them. */
function RecommendationExpansionPanel({
  filmId,
  onNavigate,
  onClose,
}: {
  filmId: number
  onNavigate: (id: number) => void
  onClose: () => void
}) {
  const {
    detail,
    availability,
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
    <div className="relative mt-3 overflow-hidden rounded-xl bg-ink/[0.035] p-4 dark:bg-black/25 sm:p-6">
      <button
        type="button"
        onClick={onClose}
        className="absolute right-3 top-3 z-10 rounded-md px-2 py-1 text-sm text-ink-soft transition hover:bg-oat hover:text-ink"
      >
        Close
      </button>

      {loading && <p className="text-sm text-ink-soft">Loading…</p>}
      {error && !loading && <p className="text-sm text-fig">Couldn&apos;t load this film yet — {error}</p>}

      {detail && !loading && (
        <div className="flex flex-col gap-5 sm:flex-row sm:gap-0">
          {/* Poster, masked to fade into the panel's background on its right
              edge — a real image-level dissolve (via mask-image) rather than
              a gradient overlay guessing at the background colour, so it
              stays correct in both light and dark mode automatically. */}
          <div className="mx-auto w-40 shrink-0 self-start sm:mx-0 sm:w-64">
            <div className="aspect-2/3 w-full overflow-hidden rounded-sm shadow-float">
              {detail.poster ? (
                <img
                  src={detail.poster}
                  alt={detail.title}
                  className="h-full w-full object-cover"
                  style={{
                    WebkitMaskImage: 'linear-gradient(to right, black 50%, transparent 92%)',
                    maskImage: 'linear-gradient(to right, black 50%, transparent 92%)',
                  }}
                />
              ) : (
                <div className="flex h-full w-full items-center justify-center bg-paper-deep p-3 text-center text-sm text-ink-soft">
                  {detail.title}
                </div>
              )}
            </div>
          </div>

          {/* Content begins roughly where the poster has faded out, so the
              two visually blend into one continuous panel. */}
          <div className="min-w-0 flex-1 pr-8 sm:-ml-20 sm:pl-0">
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

            {rematchOpen && (
              <div className="mt-2 rounded-md border border-line-strong bg-paper-mid p-3">
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

            {detail.overview && <p className="mt-3 text-sm leading-relaxed text-ink-mid">{detail.overview}</p>}

            {detail.genres.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {detail.genres.map((g) => (
                  <span key={g} className="rounded-full border border-line-strong px-2 py-0.5 text-[11px] text-ink-soft">
                    {g}
                  </span>
                ))}
              </div>
            )}

            <div className="mt-4">
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
            </div>

            <div className="mt-4">
              <WhereToWatchSection availability={availability} />
            </div>

            <div className="mt-4">
              <MoreLikeThisSection
                similar={similar}
                similarLoading={similarLoading}
                similarError={similarError}
                onNavigate={onNavigate}
                columns={5}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/** "Something new to watch" — the homepage's main section: a single row of
 * unwatched/stale recommendations with its own profile toggle + filter bar
 * (runtime, genre, vibe), independent of the Cat-alogue's own filters per
 * the household's "leave the Cat-alogue alone" instruction. Clicking a card
 * expands a horizontal detail panel in place, right below the row — not the
 * Cat-alogue's full-page/side-panel DetailDrawer. */
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
    setExpanded(null)
  }, [profile, runtimeBuckets, genres, vibe])

  const visible = items.slice(0, columns)

  function handleCardClick(filmId: number, index: number) {
    setExpanded((prev) => (prev && prev.filmId === filmId ? null : { filmId, index }))
  }

  return (
    <section className="pb-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="font-serif text-2xl text-ink sm:text-3xl">Something new to watch</h1>
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

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1.5">
          {RUNTIME_BUCKET_OPTIONS.map((opt) => (
            <FilterPill
              key={opt.value}
              active={runtimeBuckets.includes(opt.value)}
              onClick={() => setRuntimeBuckets((prev) => toggleInArray(prev, opt.value))}
            >
              {opt.label}
            </FilterPill>
          ))}
        </div>
        <span className="hidden h-4 w-px bg-line-strong sm:block" aria-hidden />
        <div className="flex flex-wrap gap-1.5">
          {GENRES.map((g) => (
            <FilterPill key={g} active={genres.includes(g)} onClick={() => setGenres((prev) => toggleInArray(prev, g))}>
              {g}
            </FilterPill>
          ))}
        </div>
        <span className="hidden h-4 w-px bg-line-strong sm:block" aria-hidden />
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

      <div className="relative mt-5">
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
            className="grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8"
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
              />
            ))}
          </div>
        )}

        {expanded && (
          <>
            <BraceConnector leftPercent={((expanded.index + 0.5) / columns) * 100} />
            <RecommendationExpansionPanel
              filmId={expanded.filmId}
              onNavigate={(id) => setExpanded((prev) => (prev ? { filmId: id, index: prev.index } : prev))}
              onClose={() => setExpanded(null)}
            />
          </>
        )}
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

/** A small, tasteful cat-ear mark next to the wordmark — not an emoji, part of the brand. */
function CatMark() {
  return (
    <svg viewBox="0 0 28 24" aria-hidden className="h-6 w-7 text-clay">
      <path
        d="M4 2.5 10 11h8L24 2.5c1 5.5 1.3 9.7-1 13.3C21 19.3 17.7 21.5 14 21.5S7 19.3 5 15.8C2.7 12.2 3 8 4 2.5Z"
        fill="currentColor"
      />
      <circle cx="11" cy="15" r="1" fill="var(--color-paper)" />
      <circle cx="17" cy="15" r="1" fill="var(--color-paper)" />
    </svg>
  )
}

type View = 'catalogue' | 'settings'

export default function App() {
  const [view, setView] = useState<View>('catalogue')
  const [health, setHealth] = useState<Health | null>(null)
  const [healthError, setHealthError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [movies, setMovies] = useState<Movie[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [searched, setSearched] = useState(false)
  const [selectedFilmId, setSelectedFilmId] = useState<number | null>(null)

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setHealthError(String(e)))
  }, [])

  async function onSearch(e: FormEvent) {
    e.preventDefault()
    const q = query.trim()
    if (!q) return
    setLoading(true)
    setError(null)
    setSearched(true)
    try {
      const data = await api.search(q)
      setMovies(data.results)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setMovies([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-full bg-paper text-ink">
      <header className="sticky top-0 z-20 border-b border-line bg-paper/95">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2.5 px-5 py-3">
          <div className="flex shrink-0 items-center gap-2.5">
            <CatMark />
            <span className="font-display text-lg font-medium tracking-[-0.005em]">
              Mishka <span className="text-clay">Hub</span>
            </span>
          </div>

          <h1 className="shrink-0 font-serif text-base text-ink sm:text-lg">Films worth your night in.</h1>

          <form onSubmit={onSearch} className="flex min-w-[160px] flex-1 gap-1.5 sm:max-w-xs">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search a film…"
              className="min-w-0 flex-1 rounded-full border border-line-strong bg-white px-3.5 py-1.5 text-sm text-ink outline-none transition placeholder:text-cloud focus:border-clay focus:ring-3 focus:ring-clay/25 dark:bg-paper-mid"
            />
            <button
              type="submit"
              disabled={loading}
              className="shrink-0 rounded-md bg-clay px-3.5 py-1.5 text-sm font-medium text-paper transition hover:bg-clay-deep disabled:opacity-50"
            >
              {loading ? '…' : 'Search'}
            </button>
          </form>

          <div className="ml-auto flex shrink-0 items-center gap-3">
            <StatusPill health={health} error={healthError} />
            <SettingsButton onClick={() => setView(view === 'settings' ? 'catalogue' : 'settings')} />
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-5 pb-24 pt-8">
        {view === 'settings' ? (
          <SettingsPage onBack={() => setView('catalogue')} />
        ) : (
          <>
            {health && !health.tmdb_configured && (
              <p className="mb-4 text-xs text-clay-deep">
                Heads up: add your TMDB key to <code className="rounded bg-oat px-1 font-mono">server/.env</code>{' '}
                and restart the server for search to return results.
              </p>
            )}

            {error && (
              <div className="mb-4 rounded-lg border border-fig/30 bg-fig/10 px-4 py-3 text-sm text-fig">{error}</div>
            )}

            {loading && (
              <div className="mb-8 grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8">
                {Array.from({ length: 16 }).map((_, i) => (
                  <div key={i} className="aspect-2/3 animate-pulse rounded-sm bg-paper-deep" />
                ))}
              </div>
            )}

            {!loading && movies.length > 0 && (
              <div
                className="mb-8 grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8"
                style={{ perspective: '800px' }}
              >
                {movies.map((m) => (
                  <MovieCard key={m.id} movie={m} onClick={() => setSelectedFilmId(m.id)} />
                ))}
              </div>
            )}

            {!loading && !error && searched && movies.length === 0 && (
              <p className="mb-8 text-center text-ink-soft">No films found. Try another title.</p>
            )}

            {selectedFilmId != null && <FilmExplorer filmId={selectedFilmId} onSelect={setSelectedFilmId} />}

            <UnseenRecommendationsRow />

            <div className="mt-10 border-t border-line pt-10">
              <Catalogue />
            </div>
          </>
        )}
      </main>

      <footer className="border-t border-line py-6 text-center font-mono text-[11px] text-ink-soft">
        <p>This product uses the TMDB API but is not endorsed or certified by TMDB.</p>
        <p>Streaming availability by JustWatch. Mishka Hub is a private, non-commercial project.</p>
      </footer>
    </div>
  )
}
