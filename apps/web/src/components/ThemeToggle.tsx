import { useEffect, useState } from 'react'

export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'mishka-theme'

/** Reads the currently-applied theme — the inline script in `index.html` has
 * already set the `.dark` class (and, ideally, localStorage) before React
 * ever mounts, so this just mirrors whatever's on <html> rather than
 * re-deciding it (re-deciding here would race the OS-preference fallback and
 * could flash/flip on first render). */
function getInitialTheme(): Theme {
  if (typeof document === 'undefined') return 'light'
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark')
  window.localStorage.setItem(STORAGE_KEY, theme)
}

/** Small icon-button theme toggle matching StatusPill/CatMark's visual
 * idiom — a compact pill-shaped control, warm ivory/clay palette, sitting
 * next to the connection StatusPill in the header. Persists to localStorage
 * under `mishka-theme`; on first visit (no stored value) the pre-paint
 * script in index.html already seeded `.dark` from the OS preference, so
 * this component only needs to read and toggle it. */
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(getInitialTheme)

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  return (
    <button
      type="button"
      onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}
      aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      aria-pressed={theme === 'dark'}
      title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-line-strong bg-white text-ink-mid transition hover:bg-oat hover:text-ink dark:bg-paper-mid"
    >
      {theme === 'dark' ? (
        // Sun glyph — shown in dark mode to indicate "tap to go light".
        <svg viewBox="0 0 20 20" aria-hidden className="h-4 w-4">
          <circle cx="10" cy="10" r="4" fill="currentColor" />
          <g stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
            <line x1="10" y1="1.2" x2="10" y2="3.2" />
            <line x1="10" y1="16.8" x2="10" y2="18.8" />
            <line x1="1.2" y1="10" x2="3.2" y2="10" />
            <line x1="16.8" y1="10" x2="18.8" y2="10" />
            <line x1="4.2" y1="4.2" x2="5.6" y2="5.6" />
            <line x1="14.4" y1="14.4" x2="15.8" y2="15.8" />
            <line x1="4.2" y1="15.8" x2="5.6" y2="14.4" />
            <line x1="14.4" y1="5.6" x2="15.8" y2="4.2" />
          </g>
        </svg>
      ) : (
        // Crescent moon glyph — shown in light mode to indicate "tap to go dark".
        <svg viewBox="0 0 20 20" aria-hidden className="h-4 w-4">
          <path
            d="M16.5 12.9A7 7 0 0 1 7.1 3.5a7 7 0 1 0 9.4 9.4Z"
            fill="currentColor"
          />
        </svg>
      )}
    </button>
  )
}
