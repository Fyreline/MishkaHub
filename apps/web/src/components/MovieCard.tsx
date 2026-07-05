import { useRef } from 'react'
import { motion, useMotionValue, useSpring, useTransform, useReducedMotion } from 'motion/react'
import type { FilmSummary, Movie } from '../api'

// Drag engagement thresholds (DESIGN.md §3c).
const ENGAGE_MS = 120
const ENGAGE_VERTICAL_PX = 8
const ENGAGE_HORIZONTAL_PX = 8
const SPRING = { stiffness: 300, damping: 20, mass: 1 }

/** Cat-alogue cards carry per-user watch state on top of the base poster fields;
 * search-demo cards are plain `Movie`s with only a TMDB average. */
export interface CatalogueBadgeInfo {
  /** My rating, 0.5–5.0 step 0.5 (already "my" — clay per DESIGN.md rating badge spec). */
  myRating: number | null
  liked: boolean
  rewatched: boolean
  /** Two-dot indicator: which household member(s) have seen this film. */
  seenBy: { luminal: boolean; garfield: boolean }
}

export function MovieCard({
  movie,
  badges,
  onClick,
  expanded = false,
}: {
  movie: Movie | FilmSummary
  badges?: CatalogueBadgeInfo
  onClick?: () => void
  /** True while this card's expansion panel is open below the grid — draws a
   * bold solid-dark (ink) outline matching the brace connector's stroke/fill,
   * so the poster, the connector and the panel read as one continuous solid
   * dark shape (per the household's reference sketch) rather than the app's
   * clay accent color. */
  expanded?: boolean
}) {
  const rating = badges?.myRating != null
    ? badges.myRating.toFixed(1)
    : 'vote_average' in movie && movie.vote_average
      ? movie.vote_average.toFixed(1)
      : null
  const ratingIsMine = badges?.myRating != null
  const prefersReducedMotion = useReducedMotion()

  const dx = useMotionValue(0)
  const dy = useMotionValue(0)
  const engaged = useRef(false)
  const pointerDownAt = useRef(0)
  const startX = useRef(0)
  const startY = useRef(0)

  // rotateY: drag right -> right edge recedes. rotateX: drag up -> top edge recedes.
  const rotateY = useSpring(useTransform(dx, [-120, 120], [-18, 18]), SPRING)
  const rotateX = useSpring(useTransform(dy, [-120, 120], [18, -18]), SPRING)
  const translateX = useSpring(useTransform(dx, (v) => v * 0.3), SPRING)
  const translateY = useSpring(useTransform(dy, (v) => v * 0.3), SPRING)
  const liftRaw = useMotionValue(0)
  const scale = useSpring(useTransform(liftRaw, [0, 1], [1, 1.06]), SPRING)
  const shadowOpacity = useSpring(liftRaw, SPRING)

  function reset() {
    engaged.current = false
    dx.set(0)
    dy.set(0)
    liftRaw.set(0)
  }

  function onPointerDown(e: React.PointerEvent) {
    if (prefersReducedMotion) return
    pointerDownAt.current = performance.now()
    startX.current = e.clientX
    startY.current = e.clientY
    engaged.current = false
  }

  function onPointerMove(e: React.PointerEvent) {
    if (prefersReducedMotion) return
    if (e.buttons === 0 && e.pointerType === 'mouse') return

    const elapsed = performance.now() - pointerDownAt.current
    const deltaX = e.clientX - startX.current
    const deltaY = e.clientY - startY.current

    if (!engaged.current) {
      const heldLongEnough = elapsed >= ENGAGE_MS && Math.abs(deltaY) <= ENGAGE_VERTICAL_PX
      const horizontalIntent =
        Math.abs(deltaX) > Math.abs(deltaY) && Math.abs(deltaX) > ENGAGE_HORIZONTAL_PX
      if (heldLongEnough || horizontalIntent) {
        engaged.current = true
        liftRaw.set(1)
        ;(e.target as Element).setPointerCapture?.(e.pointerId)
      } else {
        return // let the browser scroll — don't preventDefault yet
      }
    }

    // Engaged: drive the tilt and own the gesture.
    e.preventDefault()
    dx.set(Math.max(-120, Math.min(120, deltaX)))
    dy.set(Math.max(-120, Math.min(120, deltaY)))
  }

  function onPointerUp() {
    reset()
  }

  function onPointerCancel() {
    reset() // pointercancel = instant spring home
  }

  if (prefersReducedMotion) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`group relative aspect-2/3 w-full overflow-hidden border bg-paper-mid text-left transition-transform duration-75 ease-out active:scale-[1.02] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-clay focus-visible:ring-offset-2 ${
          expanded ? 'rounded-t-sm rounded-b-xl border-ink ring-2 ring-ink' : 'rounded-sm border-line'
        }`}
      >
        <PosterContent movie={movie} rating={rating} ratingIsMine={ratingIsMine} badges={badges} />
      </button>
    )
  }

  return (
    <motion.button
      type="button"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerCancel}
      onClick={onClick}
      style={{
        x: translateX,
        y: translateY,
        rotateX,
        rotateY,
        scale,
        touchAction: 'pan-y',
      }}
      className={`group relative z-0 aspect-2/3 w-full origin-center overflow-hidden border bg-paper-mid text-left [transform-style:preserve-3d] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-clay focus-visible:ring-offset-2 hover:z-10 focus-visible:z-10 ${
        // Bottom corners bend into a wider curve (matching the expansion
        // panel's own rounded-b-xl) instead of the default tight rounded-sm
        // corner, so the poster's own outline visibly bends to meet the
        // brace connector's curve below it rather than a sharp corner
        // butting up against a smooth one.
        expanded ? 'rounded-t-sm rounded-b-xl border-ink ring-2 ring-ink' : 'rounded-sm border-line'
      }`}
    >
      <PosterContent movie={movie} rating={rating} ratingIsMine={ratingIsMine} badges={badges} />
      <motion.span
        aria-hidden
        style={{ opacity: shadowOpacity }}
        className={`pointer-events-none absolute inset-0 -z-10 shadow-[var(--shadow-poster-drag)] ${
          expanded ? 'rounded-t-sm rounded-b-xl' : 'rounded-sm'
        }`}
      />
    </motion.button>
  )
}

function PosterContent({
  movie,
  rating,
  ratingIsMine,
  badges,
}: {
  movie: Movie | FilmSummary
  rating: string | null
  ratingIsMine: boolean
  badges?: CatalogueBadgeInfo
}) {
  return (
    <>
      {movie.poster ? (
        <img
          src={movie.poster}
          alt={movie.title ?? 'Poster'}
          loading="lazy"
          draggable={false}
          className="h-full w-full select-none object-cover transition-transform duration-150 ease-out group-hover:scale-[1.03] group-focus-visible:scale-[1.03]"
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center bg-paper-deep p-4 text-center text-sm text-ink-soft">
          {movie.title ?? 'Untitled'}
        </div>
      )}

      {/* Poster overlays (rating badge, like/rewatch chips, seen-by dots, hover
          scrim) sit on top of movie artwork, not the app chrome — they're
          pinned to fixed dark-scrim colors on purpose so they read the same
          regardless of site theme, rather than riding the ink/paper tokens
          (which flip meaning in dark mode and would invert these to a light
          scrim with dark text). */}
      {rating && (
        <div
          className={`absolute right-1 top-1 rounded-sm bg-black/70 px-1.5 py-0.5 font-mono text-[11px] ${ratingIsMine ? 'text-clay' : 'text-white'}`}
        >
          ★ {rating}
        </div>
      )}

      {badges && (badges.liked || badges.rewatched) && (
        <div className="absolute left-1 top-1 flex gap-1">
          {badges.liked && (
            <span
              aria-label="Liked"
              className="flex h-5 w-5 items-center justify-center rounded-sm bg-black/70 text-[11px] text-fig"
            >
              ♥
            </span>
          )}
          {badges.rewatched && (
            <span
              aria-label="Rewatched"
              className="flex h-5 w-5 items-center justify-center rounded-sm bg-black/70 text-[11px] text-white"
            >
              ↻
            </span>
          )}
        </div>
      )}

      {badges && (
        <div className="absolute bottom-1 left-1 flex gap-1">
          <span
            aria-label={badges.seenBy.luminal ? 'Luminal has seen this' : 'Luminal has not seen this'}
            className={`h-2.5 w-2.5 rounded-full border border-white/60 ${badges.seenBy.luminal ? 'bg-clay' : 'bg-transparent'}`}
          />
          <span
            aria-label={badges.seenBy.garfield ? 'Garfield has seen this' : 'Garfield has not seen this'}
            className={`h-2.5 w-2.5 rounded-full border border-white/60 ${badges.seenBy.garfield ? 'bg-sky' : 'bg-transparent'}`}
          />
        </div>
      )}

      <div className="absolute inset-x-0 bottom-0 translate-y-2 bg-gradient-to-t from-black/85 via-black/40 to-transparent p-3 opacity-0 transition duration-150 ease-out group-hover:translate-y-0 group-hover:opacity-100 group-focus-visible:translate-y-0 group-focus-visible:opacity-100">
        <div className="line-clamp-2 text-sm font-medium leading-tight text-white">
          {movie.title}
        </div>
        {movie.year && <div className="mt-0.5 text-xs text-white/70">{movie.year}</div>}
      </div>
    </>
  )
}
