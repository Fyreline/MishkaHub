import { useEffect, useState } from 'react'
import { api, type UpcomingItem } from '../api'

function formatDate(iso: string | null): string {
  if (!iso) return 'Date TBC'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

/** "Coming soon" tab (PHASE-8-coming-soon.md, v1 subset). TMDB doesn't
 * reliably expose future *streaming* arrival dates (that needs a weekly
 * snapshot-diff job + a new table — PHASE-8's Tiers 1-3, not yet built), so
 * this shows cinema release dates instead, clearly labelled as such. */
export function UpcomingPage() {
  const [items, setItems] = useState<UpcomingItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .getUpcoming()
      .then((res) => {
        if (!cancelled) setItems(res.results)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <section className="pb-4">
      <h2 className="font-serif text-2xl text-ink sm:text-3xl">Coming soon</h2>
      <p className="mt-0.5 text-sm text-ink-soft">
        Cinema release dates from TMDB — not streaming arrival dates, which TMDB doesn't reliably
        expose. Nothing to filter or personalise yet; just what's coming up.
      </p>

      {loading && (
        <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <div key={i} className="aspect-2/3 animate-pulse rounded-sm bg-paper-deep" />
          ))}
        </div>
      )}

      {error && !loading && (
        <div className="mt-6 rounded-lg border border-fig/30 bg-fig/10 px-4 py-3 text-sm text-fig">{error}</div>
      )}

      {!loading && !error && (
        <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4">
          {items.map((item) => (
            <div key={item.id} className="overflow-hidden rounded-sm border border-line bg-paper-mid">
              <div className="aspect-2/3 w-full">
                {item.poster ? (
                  <img src={item.poster} alt={item.title ?? 'Poster'} loading="lazy" className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full w-full items-center justify-center p-3 text-center text-sm text-ink-soft">
                    {item.title ?? 'Untitled'}
                  </div>
                )}
              </div>
              <div className="p-2.5">
                <div className="line-clamp-1 text-sm font-medium text-ink">{item.title}</div>
                <div className="mt-0.5 text-xs text-ink-soft">{formatDate(item.release_date)}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
