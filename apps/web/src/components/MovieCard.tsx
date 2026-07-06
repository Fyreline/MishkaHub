import { useRef } from 'react'
import { AnimatePresence, motion, useMotionValue, useSpring, useTransform, useReducedMotion } from 'motion/react'
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
   * solid ink halo behind/around the poster (the top "bulb" of the liquid
   * shape), so the poster reads as sitting comfortably in the same dark
   * backdrop that pinches down through App.tsx's LiquidConnector neck into
   * the detail panel's mat. The halo's side padding, bottom overhang and
   * bottom corner radius must stay in step with HALO_PAD (8px ==
   * -inset-x-2), HALO_OVERHANG (8px == -bottom-2) and HALO_CORNER (16px ==
   * rounded-b-2xl) there; the neck tucks up behind the halo's solid lower
   * reach so the two fills merge with no seam. */
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
        className="group relative block aspect-2/3 w-full rounded-sm text-left transition-transform duration-75 ease-out active:scale-[1.02] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-clay focus-visible:ring-offset-2"
      >
        {expanded && (
          <span
            aria-hidden
            className="absolute -inset-x-2 -top-2 -bottom-2 rounded-t-xl rounded-b-2xl bg-gradient-to-t from-liquid from-25% to-transparent to-70%"
          />
        )}
        <div
          className={`relative h-full w-full overflow-hidden rounded-sm border bg-paper-mid ${
            expanded ? 'border-liquid' : 'border-line'
          }`}
        >
          <PosterContent movie={movie} rating={rating} ratingIsMine={ratingIsMine} badges={badges} />
        </div>
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
      className="group relative z-0 block aspect-2/3 w-full origin-center rounded-sm text-left [transform-style:preserve-3d] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-clay focus-visible:ring-offset-2 hover:z-10 focus-visible:z-10"
    >
      {/* The halo — extends past the poster's sides, hangs a visible ledge
          below its bottom edge, and dissolves into the page background
          toward the top (fully faded by ~70% up; the lower quarter stays
          solid because the neck tucks up behind it — any transparency there
          and the seam shows through). Its rounded-b-2xl bottom corners are
          load-bearing: the neck's top opening starts exactly where the
          corner arc turns horizontal (HALO_CORNER in App.tsx), so
          silhouette-wise the corner flows straight into the neck's curve.
          Rendered before the (positioned) content wrapper so document order
          keeps it underneath. */}
      {/* AnimatePresence so the halo FADES out (matching the neck's 0.12s
          exit fade below the grid) instead of unmounting instantly when
          `expanded` flips off — an instant vanish left the still-fading
          neck visible alone for a beat, its flat seam-top reading as a
          stray rectangle where the halo used to be. */}
      <AnimatePresence>
        {expanded && (
          <motion.span
            aria-hidden
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0, transition: { duration: 0.12 } }}
            transition={{ duration: 0.15 }}
            className="absolute -inset-x-2 -top-2 -bottom-2 rounded-t-xl rounded-b-2xl bg-gradient-to-t from-liquid from-25% to-transparent to-70%"
          />
        )}
      </AnimatePresence>
      <div
        className={`relative h-full w-full overflow-hidden rounded-sm border bg-paper-mid ${
          expanded ? 'border-liquid' : 'border-line'
        }`}
      >
        <PosterContent movie={movie} rating={rating} ratingIsMine={ratingIsMine} badges={badges} />
      </div>
      <motion.span
        aria-hidden
        style={{ opacity: shadowOpacity }}
        className="pointer-events-none absolute inset-0 -z-10 rounded-sm shadow-[var(--shadow-poster-drag)]"
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
