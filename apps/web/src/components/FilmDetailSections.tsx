import { useEffect, useRef } from 'react'
import type { FilmAvailability, FilmDetail, SimilarFilm } from '../api'
import { userIdForKey, type UserKey } from '../useFilmDetail'
import { StarRatingInput } from './StarRatingInput'

/** TMDB provider_id -> a same-service search URL builder, for the household's
 * real subscriptions only (config/household.yaml). TMDB's public API doesn't
 * expose a true per-title deep link (that's JustWatch-internal), so this
 * lands one tap from the title on the actual streaming service instead of
 * routing through TMDB's watch page + an extra click on the provider icon.
 * Verified reachable (real HTTP request, 2026-07-04) except ITVX, which this
 * environment's network couldn't reach at all (not even the homepage) — its
 * URL below is a best-effort guess, not independently confirmed like the
 * rest; worth the household double-checking it once.
 */
const PROVIDER_SEARCH_URL: Record<number, (title: string) => string> = {
  8: (t) => `https://www.netflix.com/search?q=${encodeURIComponent(t)}`, // Netflix
  9: (t) => `https://www.primevideo.com/search/ref=atv_nb_sr?phrase=${encodeURIComponent(t)}`, // Amazon Prime Video
  11: (t) => `https://mubi.com/search/films?query=${encodeURIComponent(t)}`, // MUBI
  38: (t) => `https://www.bbc.co.uk/iplayer/search?q=${encodeURIComponent(t)}`, // BBC iPlayer
  41: (t) => `https://www.itv.com/search?q=${encodeURIComponent(t)}`, // ITVX — unverified, see note above
  350: (t) => `https://tv.apple.com/search?term=${encodeURIComponent(t)}`, // Apple TV+
  593: (t) => `https://player.stv.tv/search?q=${encodeURIComponent(t)}`, // STV Player
}

function watchUrlFor(providerId: number, title: string, fallback: string): string {
  return PROVIDER_SEARCH_URL[providerId]?.(title) ?? fallback
}

/** Placeholder shape for the title/meta/overview/genres block while `detail`
 * is still loading — shown instead of a plain "Loading…" so the card's
 * layout doesn't jump once real content arrives. */
export function FilmHeaderSkeleton() {
  return (
    <div className="animate-pulse space-y-3">
      <div className="h-6 w-2/3 rounded bg-paper-deep" />
      <div className="h-3 w-1/3 rounded bg-paper-deep" />
      <div className="space-y-1.5">
        <div className="h-3 w-full rounded bg-paper-deep" />
        <div className="h-3 w-full rounded bg-paper-deep" />
        <div className="h-3 w-2/3 rounded bg-paper-deep" />
      </div>
      <div className="flex gap-1.5">
        <div className="h-5 w-16 rounded-full bg-paper-deep" />
        <div className="h-5 w-16 rounded-full bg-paper-deep" />
      </div>
    </div>
  )
}

/** Placeholder shape for UserRatingColumns while `detail` is still loading. */
export function UserRatingColumnsSkeleton() {
  return (
    <div className="grid animate-pulse grid-cols-2 gap-3 border-t border-line pt-4">
      {[0, 1].map((i) => (
        <div key={i} className="space-y-2">
          <div className="h-3 w-14 rounded bg-paper-deep" />
          <div className="h-4 w-24 rounded bg-paper-deep" />
          <div className="h-3 w-20 rounded bg-paper-deep" />
        </div>
      ))}
    </div>
  )
}

/** Placeholder rows matching WhereToWatchSection's real layout (heading, two
 * provider rows, attribution line). Used by the expansion panel's full-shape
 * loading state — the section itself also reuses the row markup for its own
 * independent availability-loading state. */
export function WhereToWatchSkeleton() {
  return (
    <div className="animate-pulse border-t border-line pt-4">
      <div className="h-4 w-28 rounded bg-paper-deep" />
      <div className="mt-2 space-y-1.5">
        {[0, 1].map((i) => (
          <div key={i} className="h-9 rounded-md bg-paper-deep" />
        ))}
      </div>
      <div className="mt-2 h-3 w-44 rounded bg-paper-deep" />
    </div>
  )
}

/** Fixed poster width for the "More like this" horizontal-scroll row, shared
 * by the real row and its skeleton so they always agree. Deliberately a
 * fixed pixel width rather than a CSS-grid column fraction: the household
 * wants this row to always stay on one line (never wrap), with smaller
 * thumbnails than the old 4/5/6-column grid — a horizontally-scrolling flex
 * row of fixed-width cards does that at any viewport width, including
 * mobile, without the grid having to awkwardly shrink columns. */
const MORE_LIKE_THIS_CARD_WIDTH = 'w-20 sm:w-24'
// Real px values behind the class above (Tailwind w-20/w-24) and its gap-2 —
// read via matchMedia rather than measuring a rendered card, since on the
// very first layout pass there may be zero cards on screen yet (still
// loading, or a just-mounted panel) to measure.
const MORE_LIKE_THIS_CARD_PX = 80
const MORE_LIKE_THIS_CARD_PX_SM = 96
const MORE_LIKE_THIS_GAP_PX = 8
const MORE_LIKE_THIS_MAX_COUNT = 30 // well under the /similar endpoint's limit=50 cap

/** Placeholder row matching MoreLikeThisSection's real one-line layout. */
export function MoreLikeThisSkeleton() {
  return (
    <div className="animate-pulse border-t border-line pt-4">
      <div className="h-4 w-24 rounded bg-paper-deep" />
      <div className="mt-2 flex gap-2 overflow-x-hidden">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className={`aspect-2/3 shrink-0 rounded-sm bg-paper-deep ${MORE_LIKE_THIS_CARD_WIDTH}`} />
        ))}
      </div>
    </div>
  )
}

/** Both users' rating/liked/watched controls, side by side. Shared between
 * Catalogue.tsx's DetailDrawer and the homepage's expand-in-place
 * recommendation panel — always both interactive, regardless of which
 * person is "selected" elsewhere in the app (there's no per-device signed-in
 * concept, see useFilmDetail.ts's userIdForKey doc comment). */
export function UserRatingColumns({
  detail,
  ratingBusy,
  ratingError,
  likedBusy,
  likedError,
  seenBusy,
  seenError,
  onSetRating,
  onClearRating,
  onToggleLiked,
  onMarkSeen,
}: {
  detail: FilmDetail
  ratingBusy: Record<UserKey, boolean>
  ratingError: Record<UserKey, string | null>
  likedBusy: Record<UserKey, boolean>
  likedError: Record<UserKey, string | null>
  seenBusy: Record<UserKey, boolean>
  seenError: Record<UserKey, string | null>
  onSetRating: (key: UserKey, rating: number) => void
  onClearRating: (key: UserKey) => void
  onToggleLiked: (key: UserKey) => void
  onMarkSeen: (key: UserKey) => void
}) {
  return (
    <div className="grid grid-cols-2 gap-3 border-t border-line pt-4">
      {(['my', 'partner'] as const).map((key) => {
        const state = detail[key]
        const label = key === 'my' ? 'Luminal' : 'Garfield'
        const labelColor = key === 'my' ? 'text-clay' : 'text-sky'
        const showLetterboxdShadow =
          state.letterboxd_rating != null && state.letterboxd_rating !== state.rating

        return (
          <div key={key}>
            <div className={`text-[11px] font-medium uppercase tracking-wide ${labelColor}`}>{label}</div>

            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <StarRatingInput
                value={state.rating}
                busy={ratingBusy[key]}
                onSet={(rating) => onSetRating(key, rating)}
                onClear={() => onClearRating(key)}
              />
              {showLetterboxdShadow && (
                <span className="text-[11px] text-cloud">
                  Letterboxd: ★{state.letterboxd_rating!.toFixed(1)}
                </span>
              )}
            </div>
            {ratingError[key] && <p className="mt-0.5 text-[11px] text-fig">{ratingError[key]}</p>}

            <div className="mt-1 flex items-center gap-1.5 text-xs text-ink-soft">
              <button
                type="button"
                disabled={likedBusy[key]}
                onClick={() => onToggleLiked(key)}
                aria-pressed={state.liked}
                className={`-ml-1.5 flex min-h-11 items-center px-1.5 transition disabled:opacity-50 sm:min-h-0 ${
                  state.liked ? 'text-fig' : 'text-ink-soft hover:text-fig'
                }`}
              >
                {state.liked ? '♥ Liked' : '♡ Like'}
              </button>
              {state.watch_count > 0 && <span aria-hidden>·</span>}
              <span>{state.watch_count > 0 ? `Watched ${state.watch_count}×` : 'Not watched'}</span>
            </div>
            {likedError[key] && <p className="mt-0.5 text-[11px] text-fig">{likedError[key]}</p>}

            {state.last_watched && <div className="text-xs text-cloud">Last: {state.last_watched}</div>}

            {state.watch_count === 0 && (
              <div className="mt-1.5">
                <button
                  type="button"
                  disabled={seenBusy[key]}
                  onClick={() => onMarkSeen(key)}
                  className="min-h-11 rounded-md border border-line-strong bg-white dark:bg-paper-mid px-2 py-1 text-[11px] font-medium text-ink-mid transition hover:bg-oat disabled:opacity-50 sm:min-h-0"
                >
                  {seenBusy[key] ? 'Marking…' : 'Mark as watched'}
                </button>
                {seenError[key] && <p className="mt-0.5 text-[11px] text-fig">{seenError[key]}</p>}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** Streaming-only offers list (rent/buy already excluded server-side, but
 * this client-side kind filter is kept as a defensive belt-and-braces check
 * — matches the pre-existing DetailDrawer behavior exactly). Each offer is a
 * "Watch now" link straight to that service's own search for the title
 * (see PROVIDER_SEARCH_URL above) rather than TMDB's watch page — the
 * household's actual complaint was having to click through TMDB, then click
 * the provider icon, to get to the real site; this skips that middle step.
 * Falls back to `tmdb_watch_page` for any provider outside the household's
 * known 7 (e.g. if `include_unavailable` ever surfaces something else).
 * Loads independently of the rest of the detail view, so it gets its own
 * skeleton/error state. */
export function WhereToWatchSection({
  availability,
  filmTitle,
  loading,
  error,
}: {
  availability: FilmAvailability | null
  filmTitle: string
  loading: boolean
  error: string | null
}) {
  const streamingOffers =
    availability?.offers.filter((o) => o.kind === 'flatrate' || o.kind === 'free' || o.kind === 'ads') ?? []
  return (
    <div className="border-t border-line pt-4">
      <h4 className="text-sm font-medium text-ink">Where to watch</h4>

      {loading && (
        <div className="mt-2 space-y-1.5">
          {Array.from({ length: 2 }).map((_, i) => (
            <div key={i} className="h-9 animate-pulse rounded-md bg-paper-deep" />
          ))}
        </div>
      )}

      {error && !loading && <p className="mt-2 text-sm text-ink-soft">Nothing here yet — {error}</p>}

      {!loading && !error && (
        <>
          {availability?.owned && (
            <p className="mt-2 flex items-center gap-1.5 rounded-md bg-kraft/20 px-3 py-2 text-sm text-clay-deep">
              <span aria-hidden>🎞️</span> You own this — indexed from your local shelf.
            </p>
          )}
          {streamingOffers.length > 0 ? (
            <ul className="mt-2 space-y-1.5">
              {streamingOffers.map((o) => (
                <li
                  key={`${o.provider_id}-${o.kind}`}
                  // bg-paper-deep, not bg-paper-mid: this component renders in
                  // two shells with different backgrounds (DetailDrawer is
                  // bg-paper, the homepage expansion panel is bg-paper-mid) —
                  // paper-mid rows vanished into the panel. paper-deep is one
                  // step below both, so the rows read as their own little
                  // cards in either context, light and dark alike.
                  className="flex items-center justify-between rounded-md border border-line bg-paper-deep px-3 py-2 text-sm"
                >
                  <span className="text-ink-mid">{o.provider_name}</span>
                  {availability?.tmdb_watch_page && (
                    <a
                      href={watchUrlFor(o.provider_id, filmTitle, availability.tmdb_watch_page)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="min-h-11 rounded-md bg-clay px-2.5 py-1 text-xs font-medium text-paper transition hover:bg-clay-deep sm:min-h-0 sm:py-1"
                    >
                      Watch now
                    </a>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            !availability?.owned && (
              <p className="mt-2 text-sm text-ink-soft">Not streaming anywhere you have right now.</p>
            )
          )}
        </>
      )}
      <p className="mt-2 font-mono text-[11px] text-cloud">Streaming availability by JustWatch</p>
    </div>
  )
}

export function MoreLikeThisSection({
  similar,
  similarLoading,
  similarError,
  onNavigate,
  onWantCount,
}: {
  similar: SimilarFilm[]
  similarLoading: boolean
  similarError: string | null
  onNavigate: (id: number) => void
  /** Called with however many cards would fill this row edge-to-edge at its
   * current width — the household wanted the row to always sit snug against
   * the panel's full width (at the current poster size) rather than leaving
   * a trailing gap once the initial small batch runs out. The section only
   * asks; the caller (useFilmDetail's setSimilarLimit) decides whether/how
   * to fetch more. */
  onWantCount?: (count: number) => void
}) {
  const wrapperRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = wrapperRef.current
    const notify = onWantCount
    if (!el || !notify) return
    function measure() {
      const cardPx = window.matchMedia('(min-width: 640px)').matches ? MORE_LIKE_THIS_CARD_PX_SM : MORE_LIKE_THIS_CARD_PX
      const count = Math.floor((el!.clientWidth + MORE_LIKE_THIS_GAP_PX) / (cardPx + MORE_LIKE_THIS_GAP_PX))
      notify!(Math.max(8, Math.min(count, MORE_LIKE_THIS_MAX_COUNT)))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [onWantCount])
  return (
    <div ref={wrapperRef} className="border-t border-line pt-4">
      <h4 className="text-sm font-medium text-ink">More like this</h4>

      {similarLoading && (
        <div className="mt-2 flex gap-2 overflow-x-hidden">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className={`aspect-2/3 shrink-0 animate-pulse rounded-sm bg-paper-deep ${MORE_LIKE_THIS_CARD_WIDTH}`} />
          ))}
        </div>
      )}

      {similarError && !similarLoading && (
        <p className="mt-2 text-sm text-ink-soft">Nothing here yet — {similarError}</p>
      )}

      {!similarLoading && !similarError && similar.length === 0 && (
        <p className="mt-2 text-sm text-ink-soft">Nothing here yet.</p>
      )}

      {!similarLoading && similar.length > 0 && (
        // overflow-x-auto + no-wrap flex row: always exactly one line,
        // scrollable sideways, regardless of viewport width or item count —
        // the household explicitly didn't want this wrapping onto multiple
        // rows the way the old responsive CSS grid did once there were more
        // items than columns.
        <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
          {similar.map((s) => (
            <button
              key={s.film.id}
              type="button"
              onClick={() => onNavigate(s.film.id)}
              className={`group shrink-0 text-left ${MORE_LIKE_THIS_CARD_WIDTH}`}
            >
              <div className="aspect-2/3 w-full overflow-hidden rounded-sm bg-paper-mid">
                {s.film.poster ? (
                  <img
                    src={s.film.poster}
                    alt={s.film.title}
                    loading="lazy"
                    className="h-full w-full object-cover transition duration-150 ease-out group-hover:scale-[1.03]"
                  />
                ) : (
                  <div className="flex h-full w-full items-center justify-center p-1 text-center text-[10px] text-ink-soft">
                    {s.film.title}
                  </div>
                )}
              </div>
              {/* h-7 reserves the full 2-line height (11px * leading-tight ≈
                  13.75px/line) regardless of the actual title's length —
                  without it, a 1-line title made this card (and, since the
                  poster sits above it in normal block flow, the whole card's
                  footprint) shorter than a 2-line neighbor, and the row's
                  shared cross-axis stretch nudged posters out of alignment
                  with each other depending on wherever they landed relative
                  to the row's tallest title. */}
              <div className="mt-1 line-clamp-2 h-7 text-[11px] leading-tight text-ink-mid">{s.film.title}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// Re-exported so consumers only need to import from this one module for the
// full "detail body" set, alongside userIdForKey which callers need to map
// UserKey -> the numeric user id for other API calls.
export { userIdForKey }
export type { UserKey }
