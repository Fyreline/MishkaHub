import { useState } from 'react'
import { login } from '../auth'

/** Same glyph as public/film-cat-icon.svg (the favicon/home-screen icon) and
 * App.tsx's own CatMark — kept in sync manually since this one needs
 * `currentColor`/CSS vars to stay theme-aware. */
function CatMark() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden className="h-9 w-9 text-clay">
      {/* +0.5 x-nudge centres the painted extents in the viewBox — keep in
          step with public/film-cat-icon.svg */}
      <g transform="translate(0.5 0)">
        <circle cx="8.75" cy="11.6" r="2.8" fill="none" stroke="currentColor" strokeWidth="2" />
        <circle cx="17.4" cy="10" r="4.4" fill="none" stroke="currentColor" strokeWidth="2" />
        <rect x="4.85" y="14.4" width="18" height="12" rx="1" fill="none" stroke="currentColor" strokeWidth="2" />
        <path d="M22.85,20.4 L26.1,17.4 L26.1,23.4 Z" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
        <g transform="translate(8.41,15.73) scale(0.34)">
          <path
            d="M4,9 L2,1.5 L10,7.5 Q16,4 22,7.5 L30,1.5 L28,9 Q30.5,14.5 28,20 Q24.5,26 16,26 Q7.5,26 4,20 Q1.5,14.5 4,9 Z"
            fill="currentColor"
          />
          <circle cx="12" cy="16.5" r="1.6" fill="var(--color-paper)" />
          <circle cx="20" cy="16.5" r="1.6" fill="var(--color-paper)" />
          <path d="M15,20 L17,20 L16,21.3 Z" fill="var(--color-paper)" />
        </g>
      </g>
    </svg>
  )
}

/** Gate shown until the household logs in — the only two accounts that will
 * ever exist are seeded once via scripts/set_password.py; there is no
 * registration path anywhere, so this form only ever has two possible
 * "correct" answers. */
export function LoginScreen({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email.trim(), password)
      onLoggedIn()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center bg-paper px-5 text-ink">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3 text-center">
          <CatMark />
          <span className="font-display text-xl font-medium tracking-[-0.005em]">
            Mishka <span className="text-clay">Hub</span>
          </span>
          <p className="text-sm text-ink-soft">Household sign-in — just the two of you.</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          <input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="Email"
            className="min-h-11 w-full rounded-md border border-line-strong bg-white px-3.5 py-2.5 text-sm text-ink placeholder:text-cloud outline-none focus:border-clay dark:bg-paper-mid"
          />
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            className="min-h-11 w-full rounded-md border border-line-strong bg-white px-3.5 py-2.5 text-sm text-ink placeholder:text-cloud outline-none focus:border-clay dark:bg-paper-mid"
          />

          {error && <p className="text-sm text-fig">{error}</p>}

          <button
            type="submit"
            disabled={busy}
            className="min-h-11 w-full rounded-md bg-clay py-2.5 text-sm font-medium text-paper transition hover:bg-clay-deep disabled:opacity-50"
          >
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </div>
  )
}
