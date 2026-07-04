import type { FilmAvailability, FilmDetail, SimilarFilm } from '../api'
import { userIdForKey, type UserKey } from '../useFilmDetail'
import { StarRatingInput } from './StarRatingInput'

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
 * — matches the pre-existing DetailDrawer behavior exactly). */
export function WhereToWatchSection({ availability }: { availability: FilmAvailability | null }) {
  const streamingOffers =
    availability?.offers.filter((o) => o.kind === 'flatrate' || o.kind === 'free' || o.kind === 'ads') ?? []
  return (
    <div className="border-t border-line pt-4">
      <h4 className="text-sm font-medium text-ink">Where to watch</h4>
      {streamingOffers.length > 0 ? (
        <ul className="mt-2 space-y-1.5">
          {streamingOffers.map((o) => (
            <li
              key={`${o.provider_id}-${o.kind}`}
              className="flex items-center justify-between rounded-md bg-paper-mid px-3 py-2 text-sm"
            >
              <span className="text-ink-mid">{o.provider_name}</span>
              <span className="font-mono text-[11px] uppercase text-ink-soft">{o.kind}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-ink-soft">Not streaming anywhere you have right now.</p>
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
  columns = 4,
}: {
  similar: SimilarFilm[]
  similarLoading: boolean
  similarError: string | null
  onNavigate: (id: number) => void
  columns?: number
}) {
  const gridColsClass = columns >= 6 ? 'grid-cols-6' : columns >= 5 ? 'grid-cols-5' : 'grid-cols-4'
  return (
    <div className="border-t border-line pt-4">
      <h4 className="text-sm font-medium text-ink">More like this</h4>

      {similarLoading && (
        <div className={`mt-2 grid ${gridColsClass} gap-2`}>
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="aspect-2/3 animate-pulse rounded-sm bg-paper-deep" />
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
        <div className={`mt-2 grid ${gridColsClass} gap-2`}>
          {similar.map((s) => (
            <button key={s.film.id} type="button" onClick={() => onNavigate(s.film.id)} className="group text-left">
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
              <div className="mt-1 line-clamp-2 text-[11px] leading-tight text-ink-mid">{s.film.title}</div>
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
