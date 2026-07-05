import { useEffect, useState } from 'react'
import { api, type ServiceInsightEntry, type ServiceInsightsResponse } from '../api'
import { DetailDrawer } from './Catalogue'
import { AnimatePresence } from 'motion/react'

function ServiceRow({ entry, kind }: { entry: ServiceInsightEntry; kind: 'add' | 'drop' }) {
  const [overlayFilmId, setOverlayFilmId] = useState<number | null>(null)
  const count = kind === 'add' ? entry.unlocked_count : entry.exclusive_count
  const countLabel =
    kind === 'add'
      ? `${count} recommendation${count === 1 ? '' : 's'} you can't get any other way you pay for`
      : `${count} recommendation${count === 1 ? ' relies' : 's rely'} on this alone`

  return (
    <div className="rounded-lg border border-line-strong bg-paper-mid p-4">
      <div className="flex items-center gap-2.5">
        {entry.logo ? (
          <img src={entry.logo} alt="" className="h-8 w-8 rounded-md object-cover" />
        ) : (
          <div className="h-8 w-8 rounded-md bg-paper-deep" />
        )}
        <div>
          <div className="text-sm font-medium text-ink">{entry.provider_name}</div>
          <div className="text-xs text-ink-soft">{countLabel}</div>
        </div>
      </div>
      {entry.films.length > 0 && (
        <div className="mt-3 grid grid-cols-4 gap-2 sm:grid-cols-8">
          {entry.films.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setOverlayFilmId(f.id)}
              className="aspect-2/3 overflow-hidden rounded-sm border border-line bg-paper text-left"
            >
              {f.poster ? (
                <img src={f.poster} alt={f.title} loading="lazy" className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full w-full items-center justify-center p-1 text-center text-[10px] text-ink-soft">
                  {f.title}
                </div>
              )}
            </button>
          ))}
        </div>
      )}
      <AnimatePresence>
        {overlayFilmId != null && (
          <DetailDrawer
            key="detail-drawer"
            filmId={overlayFilmId}
            onClose={() => setOverlayFilmId(null)}
            onNavigate={setOverlayFilmId}
          />
        )}
      </AnimatePresence>
    </div>
  )
}

/** "Services" tab (Phase 6 — docs/phases/PHASE-6-service-optimisation.md):
 * which unsubscribed services would unlock the most good recommendations
 * ("worth adding"), and which subscribed services contribute the least
 * exclusive value to the household's current top recommendations ("worth
 * dropping"). Ranked by the same taste-score model /recommendations
 * already uses — not a separate system. */
export function ServiceInsightsPage() {
  const [data, setData] = useState<ServiceInsightsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .getServiceInsights('together')
      .then((res) => {
        if (!cancelled) setData(res)
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
      <h2 className="font-serif text-2xl text-ink sm:text-3xl">Services</h2>
      <p className="mt-0.5 text-sm text-ink-soft">
        Ranked by the same taste model behind "Something new to watch" — which services would
        earn their keep, and which of your current ones barely do.
      </p>

      {loading && (
        <div className="mt-6 space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-28 animate-pulse rounded-lg bg-paper-deep" />
          ))}
        </div>
      )}

      {error && !loading && (
        <div className="mt-6 rounded-lg border border-fig/30 bg-fig/10 px-4 py-3 text-sm text-fig">{error}</div>
      )}

      {!loading && !error && data && (
        <>
          <div className="mt-6">
            <h3 className="text-sm font-medium text-ink">Worth adding</h3>
            <p className="mt-0.5 text-xs text-ink-soft">
              Services you don't subscribe to, ranked by how many good recommendations they'd
              unlock that nothing you already pay for has.
            </p>
            <div className="mt-3 space-y-3">
              {data.add.length === 0 && (
                <p className="text-sm text-ink-soft">Nothing stands out right now.</p>
              )}
              {data.add.map((entry) => (
                <ServiceRow key={entry.provider_id} entry={entry} kind="add" />
              ))}
            </div>
          </div>

          <div className="mt-8">
            <h3 className="text-sm font-medium text-ink">Worth dropping</h3>
            <p className="mt-0.5 text-xs text-ink-soft">
              Your subscriptions, ranked by how little of your current top recommendations would
              actually disappear if you cancelled — the ones at the top contribute almost
              nothing you couldn't get elsewhere.
            </p>
            <div className="mt-3 space-y-3">
              {data.drop.length === 0 && (
                <p className="text-sm text-ink-soft">No active subscriptions to evaluate yet.</p>
              )}
              {data.drop.map((entry) => (
                <ServiceRow key={entry.provider_id} entry={entry} kind="drop" />
              ))}
            </div>
          </div>

          <p className="mt-6 font-mono text-[11px] text-cloud">{data.attribution}</p>
        </>
      )}
    </section>
  )
}
