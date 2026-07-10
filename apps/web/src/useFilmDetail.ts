import { useEffect, useRef, useState } from 'react'
import {
  api,
  ApiError,
  type FilmAvailability,
  type FilmDetail,
  type RematchCandidate,
  type SimilarFilm,
} from './api'

export type UserKey = 'my' | 'partner'

/** `my` is always user 1 (Luminal/Meowck), `partner` always user 2
 * (Garfield/Meowmy) for the detail endpoint — a fixed mapping, unlike the
 * list endpoint where `my`/`partner` swap with whichever `user` was
 * requested. Both people can rate/like/mark-watched here regardless of
 * which filter is active elsewhere in the app — there's no real per-device
 * "signed in as" concept, so editing isn't restricted to one side. */
export function userIdForKey(key: UserKey): 1 | 2 {
  return key === 'my' ? 1 : 2
}

export function sourceLabel(source: FilmDetail['source']): string {
  switch (source) {
    case 'letterboxd-import':
      return 'Imported from Letterboxd export'
    case 'letterboxd-scrape':
      return 'Read from public Letterboxd profile'
    case 'letterboxd-rss':
      return 'Synced via Letterboxd RSS'
    case 'in-app':
      return 'Added in app'
    default:
      return 'Unknown provenance'
  }
}

/**
 * All the stateful logic behind the film detail view — data fetching,
 * per-user rating/liked/watched editing, and the "fix the match" TMDB
 * re-match flow. Extracted out of `Catalogue.tsx`'s `DetailDrawer` so the
 * homepage's expand-in-place recommendation detail can render the exact same
 * data/behavior in a different layout, instead of duplicating ~300 lines of
 * fetch/mutation logic across two components. `DetailDrawer` itself was
 * refactored to consume this hook too — its behavior is unchanged.
 */
export function useFilmDetail(filmId: number, onNavigate: (id: number) => void) {
  const [detail, setDetail] = useState<FilmDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // Availability is fetched independently of the core detail (title/
  // synopsis/genres) so "Where to watch" can show its own skeleton and
  // resolve on its own timeline instead of blocking the rest of the card —
  // same idea already applied to "similar" below.
  const [availability, setAvailability] = useState<FilmAvailability | null>(null)
  const [availabilityError, setAvailabilityError] = useState<string | null>(null)
  const [availabilityLoading, setAvailabilityLoading] = useState(true)

  const [similar, setSimilar] = useState<SimilarFilm[]>([])
  const [similarError, setSimilarError] = useState<string | null>(null)
  const [similarLoading, setSimilarLoading] = useState(true)
  // MoreLikeThisSection measures its own row width and calls setSimilarLimit
  // once it knows how many cards would actually fill it (see its onWantCount
  // prop) — starts at a small default so the first paint doesn't wait on a
  // layout measurement.
  const [similarLimit, setSimilarLimit] = useState(8)
  const prevSimilarFilmIdRef = useRef(filmId)

  const [ratingBusy, setRatingBusy] = useState<Record<UserKey, boolean>>({ my: false, partner: false })
  const [ratingError, setRatingError] = useState<Record<UserKey, string | null>>({ my: null, partner: null })
  const [likedBusy, setLikedBusy] = useState<Record<UserKey, boolean>>({ my: false, partner: false })
  const [likedError, setLikedError] = useState<Record<UserKey, string | null>>({ my: null, partner: null })
  const [seenBusy, setSeenBusy] = useState<Record<UserKey, boolean>>({ my: false, partner: false })
  const [seenError, setSeenError] = useState<Record<UserKey, string | null>>({ my: null, partner: null })

  // "Fix the match" — hidden by default, only for the rare occasion a
  // Letterboxd import auto-matched to the wrong TMDB film.
  const [rematchOpen, setRematchOpen] = useState(false)
  const [rematchQuery, setRematchQuery] = useState('')
  const [rematchResults, setRematchResults] = useState<RematchCandidate[]>([])
  const [rematchSearching, setRematchSearching] = useState(false)
  const [rematchError, setRematchError] = useState<string | null>(null)
  const [rematchApplyingId, setRematchApplyingId] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setDetail(null)
    setRematchOpen(false)
    setRematchQuery('')
    setRematchResults([])
    setRematchError(null)
    api
      .getFilm(filmId)
      .then((d) => {
        if (cancelled) return
        setDetail(d)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [filmId])

  useEffect(() => {
    let cancelled = false
    setAvailabilityLoading(true)
    setAvailabilityError(null)
    setAvailability(null)
    api
      .getFilmAvailability(filmId)
      .then((a) => {
        if (cancelled) return
        setAvailability(a)
      })
      .catch((err) => {
        if (cancelled) return
        setAvailabilityError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setAvailabilityLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [filmId])

  useEffect(() => {
    let cancelled = false
    // Only reset to the empty/loading state on a genuine film change — a
    // limit bump (the row asking for more to fill its width) re-runs this
    // same effect but should top up quietly: keep showing the films already
    // on screen while the bigger batch loads, then swap in place, rather
    // than flashing back to a skeleton every time the row measures itself.
    const filmChanged = prevSimilarFilmIdRef.current !== filmId
    prevSimilarFilmIdRef.current = filmId
    setSimilarError(null)
    if (filmChanged) {
      setSimilar([])
      setSimilarLoading(true)
      setSimilarLimit(8)
    }
    api
      .getSimilarFilms(filmId, { limit: filmChanged ? 8 : similarLimit })
      .then((res) => {
        if (cancelled) return
        setSimilar(res.items)
      })
      .catch((err) => {
        if (cancelled) return
        setSimilarError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setSimilarLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filmId, similarLimit])

  async function handleSetRating(key: UserKey, rating: number) {
    if (!detail) return
    const userId = userIdForKey(key)
    setRatingBusy((prev) => ({ ...prev, [key]: true }))
    setRatingError((prev) => ({ ...prev, [key]: null }))
    try {
      const res = await api.setRating(detail.id, rating, userId)
      setDetail((prev) =>
        prev
          ? {
              ...prev,
              [key]: {
                ...prev[key],
                rating: res.rating,
                letterboxd_rating: res.letterboxd_rating,
              },
            }
          : prev,
      )
    } catch (err) {
      setRatingError((prev) => ({ ...prev, [key]: err instanceof ApiError ? err.message : String(err) }))
    } finally {
      setRatingBusy((prev) => ({ ...prev, [key]: false }))
    }
  }

  async function handleClearRating(key: UserKey) {
    if (!detail) return
    const userId = userIdForKey(key)
    setRatingBusy((prev) => ({ ...prev, [key]: true }))
    setRatingError((prev) => ({ ...prev, [key]: null }))
    try {
      await api.deleteRating(detail.id, userId)
      setDetail((prev) => (prev ? { ...prev, [key]: { ...prev[key], rating: null } } : prev))
    } catch (err) {
      setRatingError((prev) => ({ ...prev, [key]: err instanceof ApiError ? err.message : String(err) }))
    } finally {
      setRatingBusy((prev) => ({ ...prev, [key]: false }))
    }
  }

  async function handleToggleLiked(key: UserKey) {
    if (!detail) return
    const userId = userIdForKey(key)
    const nextLiked = !detail[key].liked
    setLikedBusy((prev) => ({ ...prev, [key]: true }))
    setLikedError((prev) => ({ ...prev, [key]: null }))
    try {
      const res = await api.setLiked(detail.id, nextLiked, userId)
      setDetail((prev) => (prev ? { ...prev, [key]: { ...prev[key], liked: res.liked } } : prev))
    } catch (err) {
      setLikedError((prev) => ({ ...prev, [key]: err instanceof ApiError ? err.message : String(err) }))
    } finally {
      setLikedBusy((prev) => ({ ...prev, [key]: false }))
    }
  }

  async function handleMarkSeen(key: UserKey) {
    if (!detail) return
    const userId = userIdForKey(key)
    setSeenBusy((prev) => ({ ...prev, [key]: true }))
    setSeenError((prev) => ({ ...prev, [key]: null }))
    try {
      const today = new Date().toISOString().slice(0, 10)
      const res = await api.markSeen(detail.id, userId, today, false)
      setDetail((prev) =>
        prev
          ? {
              ...prev,
              [key]: {
                ...prev[key],
                watch_count: prev[key].watch_count + 1,
                last_watched: res.watched_date,
              },
            }
          : prev,
      )
    } catch (err) {
      setSeenError((prev) => ({ ...prev, [key]: err instanceof ApiError ? err.message : String(err) }))
    } finally {
      setSeenBusy((prev) => ({ ...prev, [key]: false }))
    }
  }

  async function handleRematchSearch(e: React.FormEvent) {
    e.preventDefault()
    if (!detail || !rematchQuery.trim()) return
    setRematchSearching(true)
    setRematchError(null)
    try {
      const res = await api.searchRematchCandidates(detail.id, rematchQuery.trim())
      setRematchResults(res.items)
    } catch (err) {
      setRematchError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setRematchSearching(false)
    }
  }

  async function handleRematchPick(candidateId: number) {
    if (!detail) return
    setRematchApplyingId(candidateId)
    setRematchError(null)
    try {
      await api.rematchFilm(detail.id, candidateId)
      setRematchOpen(false)
      onNavigate(candidateId)
    } catch (err) {
      setRematchError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setRematchApplyingId(null)
    }
  }

  return {
    detail,
    availability,
    availabilityError,
    availabilityLoading,
    error,
    loading,
    similar,
    similarError,
    similarLoading,
    setSimilarLimit,
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
  }
}
