import { useState } from 'react'

const STAR_POSITIONS = [1, 2, 3, 4, 5] as const

/** Classic 5-star widget with left/right half-click zones and hover preview.
 * Shared between Catalogue.tsx's DetailDrawer and the homepage's
 * expand-in-place recommendation panel — extracted so both render the exact
 * same rating control rather than duplicating the half-click-zone math. */
export function StarRatingInput({
  value,
  busy,
  onSet,
  onClear,
}: {
  value: number | null
  busy: boolean
  onSet: (rating: number) => void
  onClear: () => void
}) {
  const [hover, setHover] = useState<number | null>(null)
  const display = hover ?? value ?? 0

  function halfFromEvent(e: React.MouseEvent<HTMLButtonElement>, star: number): number {
    const rect = e.currentTarget.getBoundingClientRect()
    const isLeftHalf = e.clientX - rect.left < rect.width / 2
    return isLeftHalf ? star - 0.5 : star
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <div
        className="flex items-center"
        role="radiogroup"
        aria-label="Your rating"
        onMouseLeave={() => setHover(null)}
      >
        {STAR_POSITIONS.map((star) => {
          const fill =
            display >= star ? 'full' : display >= star - 0.5 ? 'half' : 'empty'
          return (
            <button
              key={star}
              type="button"
              role="radio"
              aria-checked={value === star || value === star - 0.5}
              disabled={busy}
              title={`${star} stars`}
              onMouseMove={(e) => setHover(halfFromEvent(e, star))}
              onClick={(e) => onSet(halfFromEvent(e, star))}
              className={`flex h-11 w-8 items-center justify-center leading-none transition disabled:opacity-50 sm:h-6 sm:w-5 ${
                fill === 'empty' ? 'text-line-strong hover:text-kraft' : 'text-clay'
              }`}
            >
              {/* Inner wrapper sized to the glyph itself — the outer button
                  provides the larger tap target (44px on mobile) while this
                  stays glyph-sized so the half/full clip overlay still lines
                  up exactly on top of the outline star. */}
              <span className="relative inline-block text-lg">
                {/* Empty outline star as the base layer. */}
                <span aria-hidden>☆</span>
                {/* Filled star clipped to show a half or full overlay. */}
                {fill !== 'empty' && (
                  <span
                    aria-hidden
                    className="absolute inset-0 overflow-hidden"
                    style={{ width: fill === 'half' ? '50%' : '100%' }}
                  >
                    ★
                  </span>
                )}
              </span>
            </button>
          )
        })}
      </div>
      {value != null && (
        <button
          type="button"
          disabled={busy}
          onClick={onClear}
          className="min-h-11 px-1 text-[11px] text-ink-soft underline decoration-dotted transition hover:text-ink disabled:opacity-50 sm:min-h-0"
        >
          clear
        </button>
      )}
    </div>
  )
}
