import { AnimatePresence } from 'motion/react'
import { useEffect, useState } from 'react'
import { api, type MediaFileItem, type MediaScanResult } from '../api'
import { DetailDrawer } from './Catalogue'
import { MovieCard } from './MovieCard'

/** "Owned" tab (PHASE-7 §2/§5) — the household's locally-indexed film shelf.
 * Owned-but-unwatched titles also count as recommendation candidates even
 * with zero streaming availability (see pipeline.py's `_owned_film_ids`). */
export function OwnedPage() {
  const [overlayFilmId, setOverlayFilmId] = useState<number | null>(null)
  const [media, setMedia] = useState<MediaFileItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [roots, setRoots] = useState<string[]>([])
  const [newRoot, setNewRoot] = useState('')
  const [scanning, setScanning] = useState(false)
  const [scanResult, setScanResult] = useState<MediaScanResult | null>(null)
  const [matchQuery, setMatchQuery] = useState<Record<number, string>>({})
  const [matchResults, setMatchResults] = useState<Record<number, { id: number; title: string; year: string | null }[]>>({})

  function reload() {
    setLoading(true)
    setError(null)
    Promise.all([api.getMedia(), api.getMediaRoots()])
      .then(([m, r]) => {
        setMedia(m.items)
        setRoots(r.roots)
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false))
  }

  useEffect(reload, [])

  async function addRoot() {
    if (!newRoot.trim()) return
    const next = [...roots, newRoot.trim()]
    await api.setMediaRoots(next)
    setRoots(next)
    setNewRoot('')
  }

  async function removeRoot(root: string) {
    const next = roots.filter((r) => r !== root)
    await api.setMediaRoots(next)
    setRoots(next)
  }

  async function scan() {
    setScanning(true)
    setScanResult(null)
    try {
      const result = await api.scanMedia()
      setScanResult(result)
      reload()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setScanning(false)
    }
  }

  async function searchMatch(fileId: number) {
    const q = matchQuery[fileId]?.trim()
    if (!q) return
    const res = await api.search(q)
    setMatchResults((prev) => ({
      ...prev,
      [fileId]: res.results.map((m) => ({ id: m.id, title: m.title ?? 'Untitled', year: m.year })),
    }))
  }

  async function confirmMatch(fileId: number, tmdbId: number) {
    await api.matchMedia(fileId, tmdbId)
    reload()
  }

  async function removeFile(fileId: number) {
    await api.deleteMedia(fileId)
    reload()
  }

  const matched = media.filter((m) => m.film != null)
  const unmatched = media.filter((m) => m.film == null)

  return (
    <section className="pb-4">
      <h2 className="font-serif text-2xl text-ink sm:text-3xl">Owned</h2>
      <p className="mt-0.5 text-sm text-ink-soft">
        Films indexed from your local shelf — these count as "available" in recommendations even
        with no streaming service, and (once a Jellyfin server is linked) can be played straight
        to the living-room TV.
      </p>

      <div className="mt-6 rounded-lg border border-line-strong bg-paper-mid p-4">
        <h3 className="text-sm font-medium text-ink">Media folders</h3>
        <p className="mt-1 text-xs text-ink-soft">
          Folders on this machine to scan for video files (.mkv, .mp4, .m4v, .avi, .ts).
        </p>
        <ul className="mt-3 space-y-1.5">
          {roots.map((root) => (
            <li key={root} className="flex items-center justify-between gap-2 rounded-md bg-white px-3 py-2 text-xs text-ink-mid dark:bg-paper-deep">
              <span className="truncate font-mono">{root}</span>
              <button
                type="button"
                onClick={() => removeRoot(root)}
                className="shrink-0 text-ink-soft underline underline-offset-2 hover:text-fig"
              >
                Remove
              </button>
            </li>
          ))}
          {roots.length === 0 && <li className="text-xs text-ink-soft">No folders configured yet.</li>}
        </ul>
        <div className="mt-3 flex gap-2">
          <input
            value={newRoot}
            onChange={(e) => setNewRoot(e.target.value)}
            placeholder="/Volumes/Media/Films"
            className="min-h-11 flex-1 rounded-md border border-line-strong bg-white px-3 py-2 font-mono text-xs text-ink outline-none focus:border-clay sm:min-h-0 dark:bg-paper-deep"
          />
          <button
            type="button"
            onClick={addRoot}
            className="min-h-11 shrink-0 rounded-md bg-paper-deep px-3 text-xs font-medium text-ink-mid transition hover:bg-oat sm:min-h-0"
          >
            Add
          </button>
        </div>
        <button
          type="button"
          onClick={scan}
          disabled={scanning || roots.length === 0}
          className="mt-3 min-h-11 rounded-md bg-clay px-3.5 py-1.5 text-xs font-medium text-paper transition hover:bg-clay-deep disabled:opacity-50 sm:min-h-0"
        >
          {scanning ? 'Scanning…' : 'Scan now'}
        </button>
        {scanResult && (
          <p className="mt-2 text-xs text-ink-soft">
            Found {scanResult.files_found} file(s) — {scanResult.auto_matched} auto-matched,{' '}
            {scanResult.unmatched} need a manual match.
            {scanResult.errors.length > 0 && ` (${scanResult.errors.join('; ')})`}
          </p>
        )}
      </div>

      {loading && <p className="mt-6 text-sm text-ink-soft">Loading…</p>}
      {error && !loading && (
        <div className="mt-6 rounded-lg border border-fig/30 bg-fig/10 px-4 py-3 text-sm text-fig">{error}</div>
      )}

      {!loading && !error && (
        <>
          {matched.length > 0 && (
            <div
              className="mt-6 grid grid-cols-3 gap-[var(--poster-gap)] sm:grid-cols-4 md:grid-cols-5 lg:grid-cols-6 lg:gap-[var(--poster-gap-lg)] xl:grid-cols-8"
            >
              {matched.map((m) => (
                <MovieCard
                  key={m.id}
                  movie={{
                    id: m.film!.id,
                    title: m.film!.title,
                    year: m.film!.year != null ? String(m.film!.year) : null,
                    overview: null,
                    poster: m.film!.poster,
                    vote_average: null,
                  }}
                  onClick={() => setOverlayFilmId(m.film!.id)}
                />
              ))}
            </div>
          )}

          {matched.length === 0 && unmatched.length === 0 && (
            <p className="mt-6 text-sm text-ink-soft">
              Nothing indexed yet — add a folder above and hit "Scan now."
            </p>
          )}

          {unmatched.length > 0 && (
            <div className="mt-6">
              <h3 className="text-sm font-medium text-ink">Needs a manual match</h3>
              <ul className="mt-2 space-y-2">
                {unmatched.map((m) => (
                  <li key={m.id} className="rounded-lg border border-line-strong bg-paper-mid p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-xs text-ink-mid">{m.path}</span>
                      <button
                        type="button"
                        onClick={() => removeFile(m.id)}
                        className="shrink-0 text-xs text-ink-soft underline underline-offset-2 hover:text-fig"
                      >
                        Remove
                      </button>
                    </div>
                    <div className="mt-2 flex gap-2">
                      <input
                        value={matchQuery[m.id] ?? ''}
                        onChange={(e) => setMatchQuery((prev) => ({ ...prev, [m.id]: e.target.value }))}
                        onKeyDown={(e) => e.key === 'Enter' && searchMatch(m.id)}
                        placeholder="Search title…"
                        className="min-h-11 flex-1 rounded-md border border-line-strong bg-white px-3 py-2 text-xs text-ink outline-none focus:border-clay sm:min-h-0 dark:bg-paper-deep"
                      />
                      <button
                        type="button"
                        onClick={() => searchMatch(m.id)}
                        className="min-h-11 shrink-0 rounded-md bg-paper-deep px-3 text-xs font-medium text-ink-mid transition hover:bg-oat sm:min-h-0"
                      >
                        Search
                      </button>
                    </div>
                    {matchResults[m.id] && matchResults[m.id].length > 0 && (
                      <ul className="mt-2 space-y-1">
                        {matchResults[m.id].slice(0, 5).map((r) => (
                          <li key={r.id}>
                            <button
                              type="button"
                              onClick={() => confirmMatch(m.id, r.id)}
                              className="min-h-11 w-full rounded-md border border-line-strong bg-white px-3 py-1.5 text-left text-xs text-ink-mid transition hover:border-clay sm:min-h-0 dark:bg-paper-deep"
                            >
                              {r.title} {r.year ? `(${r.year})` : ''}
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
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
    </section>
  )
}
