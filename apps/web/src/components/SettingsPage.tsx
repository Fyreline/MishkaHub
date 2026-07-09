import { useEffect, useMemo, useState } from 'react'
import { api, ApiError, type Provider } from '../api'
import { getUser, logout } from '../auth'

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

/** Small placeholder shown when a provider has no logo (the subscriptions
 * table doesn't always have `logo_path` populated, and some catalogue
 * entries lack artwork too) — first two letters on a clay-tinted chip,
 * matching the warm ivory/clay palette rather than a broken-image icon. */
function ProviderLogo({ name, logo }: { name: string; logo: string | null }) {
  if (logo) {
    return (
      <img
        src={logo}
        alt=""
        aria-hidden
        className="h-9 w-9 shrink-0 rounded-md object-cover shadow-sm"
      />
    )
  }
  const initials = name
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join('')
  return (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-clay/15 font-display text-xs font-medium text-clay-deep">
      {initials || '?'}
    </div>
  )
}

/** Settings page: full TMDB provider catalogue as a checklist, pre-checked
 * for whatever the household currently pays for. Saving PUTs the whole
 * selected set to /api/settings/subscriptions, which replaces the active
 * list wholesale (anything unchecked gets deactivated server-side). */
export function SettingsPage({ onBack }: { onBack: () => void }) {
  const [providers, setProviders] = useState<Provider[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [initialSelected, setInitialSelected] = useState<Set<number>>(new Set())
  const [search, setSearch] = useState('')

  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [confirmEmpty, setConfirmEmpty] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    Promise.all([api.getProviders('GB'), api.getSubscriptions()])
      .then(([providersRes, subsRes]) => {
        if (cancelled) return
        setProviders(providersRes.providers)
        const active = new Set(subsRes.subscriptions.map((s) => s.provider_id))
        setSelected(active)
        setInitialSelected(active)
      })
      .catch((err) => {
        if (cancelled) return
        setLoadError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const sortedFiltered = useMemo(() => {
    const q = search.trim().toLowerCase()
    const filtered = q
      ? providers.filter((p) => p.provider_name.toLowerCase().includes(q))
      : providers.slice()
    filtered.sort((a, b) => {
      // Selected first when no search is active — keeps "what we already pay
      // for" at the top of a long catalogue. While searching, just rank by
      // TMDB's display_priority (popularity), name as tiebreak.
      if (!q) {
        const aSel = selected.has(a.provider_id)
        const bSel = selected.has(b.provider_id)
        if (aSel !== bSel) return aSel ? -1 : 1
      }
      const ap = a.display_priority ?? Number.MAX_SAFE_INTEGER
      const bp = b.display_priority ?? Number.MAX_SAFE_INTEGER
      if (ap !== bp) return ap - bp
      return a.provider_name.localeCompare(b.provider_name)
    })
    return filtered
  }, [providers, search, selected])

  const dirty = useMemo(() => {
    if (selected.size !== initialSelected.size) return true
    for (const id of selected) {
      if (!initialSelected.has(id)) return true
    }
    return false
  }, [selected, initialSelected])

  function toggle(providerId: number) {
    setSaveState('idle')
    setSaveError(null)
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(providerId)) next.delete(providerId)
      else next.add(providerId)
      return next
    })
  }

  async function doSave() {
    setSaveState('saving')
    setSaveError(null)
    try {
      const res = await api.putSubscriptions(Array.from(selected, (provider_id) => ({ provider_id })))
      const active = new Set(res.subscriptions.map((s) => s.provider_id))
      setSelected(active)
      setInitialSelected(active)
      setSaveState('saved')
    } catch (err) {
      setSaveState('error')
      setSaveError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : String(err))
    }
  }

  function onSaveClick() {
    if (selected.size === 0 && !confirmEmpty) {
      setConfirmEmpty(true)
      return
    }
    setConfirmEmpty(false)
    void doSave()
  }

  return (
    <section className="mx-auto max-w-3xl py-10">
      <div className="flex items-center justify-between gap-4">
        <div>
          <button
            type="button"
            onClick={onBack}
            className="mb-2 inline-flex items-center gap-1 text-xs font-medium text-ink-soft transition hover:text-ink"
          >
            <svg viewBox="0 0 16 16" aria-hidden className="h-3.5 w-3.5">
              <path
                d="M9.5 3 5 8l4.5 5"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Back to films
          </button>
          <h1 className="font-serif text-3xl font-normal tracking-[-0.005em] text-ink">Streaming services</h1>
          <p className="mt-1.5 max-w-lg text-sm text-ink-soft">
            Tell us what you actually pay for, and recommendations will be filtered to it. Changes apply to the whole
            household.
          </p>
        </div>
      </div>

      <div className="mt-6 rounded-lg border border-line-strong bg-paper-mid p-4">
        <h2 className="text-sm font-medium text-ink">Letterboxd data</h2>
        <p className="mt-1 text-xs text-ink-soft">
          RSS keeps the library fresh automatically, but it only reliably carries logged/reviewed
          entries — a plain &quot;mark as watched&quot; tap on Letterboxd doesn&apos;t always show up there. For the full,
          definitive history, re-export your data from Letterboxd every so often and re-import the zip.
        </p>
        <a
          href="https://letterboxd.com/user/exportdata"
          target="_blank"
          rel="noopener noreferrer"
          className="mt-2 inline-flex min-h-11 items-center rounded-md bg-clay px-3.5 py-1.5 text-xs font-medium text-paper transition hover:bg-clay-deep sm:min-h-0"
        >
          Export my Letterboxd data →
        </a>
      </div>

      <div className="mt-6">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search services…"
          className="w-full rounded-md border border-line-strong bg-white px-3.5 py-2.5 text-sm text-ink outline-none placeholder:text-cloud focus:border-clay focus:ring-3 focus:ring-clay/25 dark:bg-paper-mid"
        />
      </div>

      {loading && (
        <div className="mt-6 grid grid-cols-1 gap-2 sm:grid-cols-2">
          {Array.from({ length: 10 }).map((_, i) => (
            <div key={i} className="h-14 animate-pulse rounded-md bg-paper-deep" />
          ))}
        </div>
      )}

      {loadError && !loading && (
        <div className="mt-6 rounded-lg border border-fig/30 bg-fig/10 px-4 py-3 text-sm text-fig">
          Couldn&apos;t load providers — {loadError}
        </div>
      )}

      {!loading && !loadError && (
        <>
          <p className="mt-4 text-xs text-ink-soft">
            {selected.size} of {providers.length} services selected
            {sortedFiltered.length !== providers.length ? ` · showing ${sortedFiltered.length} matching “${search}”` : ''}
          </p>

          <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
            {sortedFiltered.map((p) => {
              const checked = selected.has(p.provider_id)
              return (
                <li key={p.provider_id}>
                  <label
                    className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-md border px-3 py-2.5 transition ${
                      checked
                        ? 'border-clay bg-clay/10'
                        : 'border-line-strong bg-white hover:bg-oat dark:bg-paper-mid'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggle(p.provider_id)}
                      className="h-4 w-4 shrink-0 accent-clay"
                    />
                    <ProviderLogo name={p.provider_name} logo={p.logo} />
                    <span className="truncate text-sm font-medium text-ink">{p.provider_name}</span>
                  </label>
                </li>
              )
            })}
          </ul>

          {sortedFiltered.length === 0 && (
            <p className="mt-6 text-center text-sm text-ink-soft">No services match &quot;{search}&quot;.</p>
          )}

          <div className="sticky bottom-4 z-10 mt-8 flex flex-col gap-2 rounded-lg border border-line-strong bg-paper/95 p-4 shadow-float backdrop-saturate-150 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-sm">
              {saveState === 'saved' && !dirty && <span className="font-medium text-olive">Saved ✓ subscriptions updated.</span>}
              {saveState === 'error' && (
                <span className="font-medium text-fig">Couldn&apos;t save{saveError ? ` — ${saveError}` : ''}.</span>
              )}
              {saveState !== 'saved' && saveState !== 'error' && confirmEmpty && (
                <span className="font-medium text-clay-deep">
                  That will remove all streaming services — select at least one, or save again to confirm.
                </span>
              )}
              {saveState !== 'saved' && saveState !== 'error' && !confirmEmpty && dirty && (
                <span className="text-ink-soft">You have unsaved changes.</span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {dirty && saveState !== 'saving' && (
                <button
                  type="button"
                  onClick={() => {
                    setSelected(initialSelected)
                    setSaveState('idle')
                    setSaveError(null)
                    setConfirmEmpty(false)
                  }}
                  className="rounded-md border border-line-strong bg-white px-4 py-2 text-sm font-medium text-ink-mid transition hover:bg-oat dark:bg-paper-mid"
                >
                  Discard changes
                </button>
              )}
              <button
                type="button"
                onClick={onSaveClick}
                disabled={saveState === 'saving' || (!dirty && !confirmEmpty)}
                className="rounded-md bg-clay px-5 py-2 text-sm font-medium text-paper transition hover:bg-clay-deep disabled:cursor-not-allowed disabled:opacity-50"
              >
                {saveState === 'saving'
                  ? 'Saving…'
                  : confirmEmpty
                    ? 'Confirm — remove all services'
                    : 'Save changes'}
              </button>
            </div>
          </div>
        </>
      )}

      {/* account — the header's sign-out moved here (2026-07-09, matching
          Michi: slim top bars on both apps) */}
      <div className="mt-8 flex items-center justify-between rounded-lg border border-line bg-paper-mid p-5">
        <div>
          <h2 className="font-display text-base font-medium text-ink">Account</h2>
          <p className="mt-1 text-sm text-ink-soft">
            Signed in as {getUser()?.display_name ?? 'your household login'}.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void logout()}
          className="rounded-md border border-line-strong px-4 py-2 text-sm font-medium text-ink transition hover:bg-oat"
        >
          Sign out
        </button>
      </div>
    </section>
  )
}
