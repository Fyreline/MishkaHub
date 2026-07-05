// Session state for the two-person login (docs/phases/PHASE-4-accounts-feedback.md).
// Access token lives in memory only (never touches localStorage — the
// refresh token is the only thing persisted, since it's what survives a
// page reload). A tab reload re-derives a fresh access token from the
// stored refresh token via `bootstrap()`.

const BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000'
const REFRESH_STORAGE_KEY = 'mishka-refresh-token'

export interface AuthUser {
  id: number
  email: string
  display_name: string
}

interface TokenPair {
  access_token: string
  refresh_token: string
  expires_in: number
  user: AuthUser
}

let accessToken: string | null = null
let accessTokenExpiresAt = 0 // epoch ms
let currentUser: AuthUser | null = null
let refreshInFlight: Promise<boolean> | null = null

type Listener = () => void
const listeners = new Set<Listener>()

function notify() {
  listeners.forEach((l) => l())
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function getUser(): AuthUser | null {
  return currentUser
}

function getStoredRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_STORAGE_KEY)
}

function storeSession(pair: TokenPair) {
  accessToken = pair.access_token
  // Refresh 30s early so a request mid-flight doesn't race an expiring token.
  accessTokenExpiresAt = Date.now() + (pair.expires_in - 30) * 1000
  currentUser = pair.user
  localStorage.setItem(REFRESH_STORAGE_KEY, pair.refresh_token)
  notify()
}

function clearSession() {
  accessToken = null
  accessTokenExpiresAt = 0
  currentUser = null
  localStorage.removeItem(REFRESH_STORAGE_KEY)
  notify()
}

export async function login(email: string, password: string): Promise<void> {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `Login failed (${res.status})`)
  }
  storeSession(await res.json())
}

export async function logout(): Promise<void> {
  const refreshToken = getStoredRefreshToken()
  clearSession()
  if (refreshToken) {
    fetch(`${BASE}/api/auth/logout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    }).catch(() => {
      /* best-effort — the client-side session is already cleared either way */
    })
  }
}

async function doRefresh(): Promise<boolean> {
  const refreshToken = getStoredRefreshToken()
  if (!refreshToken) return false
  try {
    const res = await fetch(`${BASE}/api/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
    if (!res.ok) {
      clearSession()
      return false
    }
    storeSession(await res.json())
    return true
  } catch {
    return false
  }
}

/** Called once on app start: if a refresh token is stored, try to silently
 * establish a session so a page reload doesn't force a re-login. */
export async function bootstrap(): Promise<void> {
  if (getStoredRefreshToken()) {
    await doRefresh()
  }
}

/** Returns a currently-valid access token, transparently refreshing if it's
 * expired/near-expiry or missing. Returns null if there's no session at all
 * (or refresh failed) — callers should treat that as "show the login screen." */
export async function getValidAccessToken(): Promise<string | null> {
  if (accessToken && Date.now() < accessTokenExpiresAt) {
    return accessToken
  }
  if (!refreshInFlight) {
    refreshInFlight = doRefresh().finally(() => {
      refreshInFlight = null
    })
  }
  const ok = await refreshInFlight
  return ok ? accessToken : null
}

/** Called by api.ts when a request still comes back 401 despite a
 * (supposedly) valid access token — e.g. the refresh token itself was
 * revoked server-side (reuse-detection tripwire). Forces back to login. */
export function forceLogout(): void {
  clearSession()
}
