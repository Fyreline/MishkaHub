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
    <section className="mt-16 border-t border-line pt-12">
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

/** "Something new to watch" — a single row of unseen-or-stale recommendations
 * under the homepage search bar, above the Cat-alogue. Deliberately its own
 * small profile filter (not wired to the Cat-alogue's filter bar) so this
 * stays independent of that component per the household's "leave the
 * Cat-alogue alone" instruction. Column count always matches the Cat-alogue
 * grid's current breakpoint; the row itself never wraps. */
function UnseenRecommendationsRow({ onSelect }: { onSelect: (id: number) => void }) {
  const [profile, setProfile] = useState<RecommendationProfile>('together')
  const [items, setItems] = useState<RecommendationItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const columns = useResponsiveColumns()

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .getRecommendations({ profile, limit: 8 })
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
  }, [profile])

  const visible = items.slice(0, columns)

  return (
    <section className="mt-10 border-t border-line pt-10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="font-display text-lg font-medium text-ink">Something new to watch</h2>
          <p className="mt-0.5 text-xs text-ink-soft">
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

      <div className="mt-4">
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
          <p className="text-sm text-ink-soft">
            Nothing eligible right now — check back once the corpus grows or a subscription changes.
          </p>
        )}

        {!loading && !error && visible.length > 0 && (
          <div
            className="grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8"
            style={{ perspective: '800px' }}
          >
            {visible.map((item) => (
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
                onClick={() => onSelect(item.film.id)}
              />
            ))}
          </div>
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
        <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4">
          <div className="flex items-center gap-2.5">
            <CatMark />
            <span className="font-display text-lg font-medium tracking-[-0.005em]">
              Mishka <span className="text-clay">Hub</span>
            </span>
          </div>
          <div className="flex items-center gap-3">
            <StatusPill health={health} error={healthError} />
            <SettingsButton onClick={() => setView(view === 'settings' ? 'catalogue' : 'settings')} />
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-5 pb-24">
        {view === 'settings' ? (
          <SettingsPage onBack={() => setView('catalogue')} />
        ) : (
        <>
        <section className="py-12 text-center sm:py-16">
          <h1 className="mx-auto max-w-2xl text-balance font-serif text-4xl font-normal tracking-[-0.005em] text-ink sm:text-5xl">
            Films worth your night in.
          </h1>
          <p className="mx-auto mt-4 max-w-xl text-ink-soft">
            Personalised recommendations from what you&apos;ve watched — filtered to the
            services you actually pay for. This is the early scaffold; search is wired up
            end-to-end to prove the pipeline.
          </p>

          <form onSubmit={onSearch} className="mx-auto mt-8 flex max-w-xl gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search a film to test the connection…"
              className="flex-1 rounded-full border border-line-strong bg-white px-4 py-2.5 text-sm text-ink outline-none transition placeholder:text-cloud focus:border-clay focus:ring-3 focus:ring-clay/25 dark:bg-paper-mid"
            />
            <button
              type="submit"
              disabled={loading}
              className="rounded-md bg-clay px-5 py-2.5 text-sm font-medium text-paper transition hover:bg-clay-deep disabled:opacity-50"
            >
              {loading ? 'Searching…' : 'Search'}
            </button>
          </form>

          {health && !health.tmdb_configured && (
            <p className="mx-auto mt-3 max-w-xl text-xs text-clay-deep">
              Heads up: add your TMDB key to <code className="rounded bg-oat px-1 font-mono">server/.env</code>{' '}
              and restart the server for search to return results.
            </p>
          )}
        </section>

        {error && (
          <div className="mx-auto max-w-xl rounded-lg border border-fig/30 bg-fig/10 px-4 py-3 text-sm text-fig">
            {error}
          </div>
        )}

        {loading && (
          <div className="grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8">
            {Array.from({ length: 16 }).map((_, i) => (
              <div key={i} className="aspect-2/3 animate-pulse rounded-sm bg-paper-deep" />
            ))}
          </div>
        )}

        {!loading && movies.length > 0 && (
          <div
            className="grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8"
            style={{ perspective: '800px' }}
          >
            {movies.map((m) => (
              <MovieCard key={m.id} movie={m} onClick={() => setSelectedFilmId(m.id)} />
            ))}
          </div>
        )}

        {!loading && !error && searched && movies.length === 0 && (
          <p className="text-center text-ink-soft">No films found. Try another title.</p>
        )}

        {selectedFilmId != null && (
          <FilmExplorer filmId={selectedFilmId} onSelect={setSelectedFilmId} />
        )}

        <UnseenRecommendationsRow onSelect={setSelectedFilmId} />

        <Catalogue />
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
