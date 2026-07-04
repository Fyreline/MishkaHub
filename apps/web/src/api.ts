const BASE = import.meta.env.VITE_API_BASE ?? 'http://127.0.0.1:8000'

export interface Health {
  status: string
  environment: string
  region: string
  tmdb_configured: boolean
}

export interface Movie {
  id: number
  title: string | null
  year: string | null
  overview: string | null
  poster: string | null
  vote_average: number | null
}

export interface SearchResponse {
  query: string
  count: number
  results: Movie[]
}

// ---------------------------------------------------------------------------
// Phase 2 — Films & Letterboxd import (docs/API.md "Phase 2 — Films & Letterboxd import")
// Backend routers are not live yet; these types/functions mirror the documented
// contract exactly so the UI can be built against it and plug in with zero
// changes once the server ships.
// ---------------------------------------------------------------------------

/** One member's state on a film, as embedded in `GET /api/films` and `GET /api/films/{id}`. */
export interface FilmUserState {
  rating: number | null
  liked: boolean
  watch_count: number
  last_watched: string | null
  letterboxd_rating: number | null
}

/** A row from `GET /api/films`. */
export interface FilmSummary {
  id: number
  title: string
  year: number | null
  poster: string | null
  my: FilmUserState
  partner: FilmUserState
}

export interface FilmsResponse {
  total: number
  items: FilmSummary[]
}

export type FilmSort = 'watched_desc' | 'rating_desc' | 'title' | 'year'

export interface GetFilmsParams {
  user?: 1 | 2
  seen?: boolean
  seen_by?: 'both' | 'either'
  rated?: boolean
  liked?: boolean
  min_rating?: number
  year_from?: number
  year_to?: number
  genre?: string
  q?: string
  sort?: FilmSort
  limit?: number
  offset?: number
}

/** Provenance of a watch/rating/like row — see DATA_MODEL.md `source` columns. */
export type FilmSource = 'letterboxd-import' | 'letterboxd-scrape' | 'letterboxd-rss' | 'in-app'

/** Full detail from `GET /api/films/{id}` — shape inferred from DATA_MODEL/API conventions;
 * extends FilmSummary's per-user state with the metadata the detail drawer needs. */
export interface FilmDetail {
  id: number
  title: string
  year: number | null
  poster: string | null
  backdrop: string | null
  overview: string | null
  genres: string[]
  runtime_min: number | null
  source: FilmSource | null
  my: FilmUserState
  partner: FilmUserState
}

export interface AvailabilityOffer {
  provider_id: number
  provider_name: string
  kind: 'flatrate' | 'rent' | 'buy' | 'free' | 'ads'
  logo?: string
  subscribed?: boolean
}

export interface FilmAvailability {
  film_id: number
  region: string
  fetched_at: string
  attribution: string
  offers: AvailabilityOffer[]
  tmdb_watch_page: string
}

// --- Re-match a wrongly-matched film ("fix the match") -------------------

/** One candidate from `GET /api/films/{id}/rematch/search`. */
export interface RematchCandidate {
  id: number
  title: string
  year: number | null
  poster: string | null
  overview: string | null
}

export interface RematchSearchResponse {
  query: string
  items: RematchCandidate[]
}

export interface RematchResult {
  old_film_id: number
  new_film_id: number
  new_film: { id: number; title: string; year: number | null; poster: string | null }
  moved: { watches: number; ratings: number; likes: number; reviews: number }
  dropped: { ratings: number; likes: number; reviews: number }
  old_film_deleted: boolean
}

// --- Similar films -------------------------------------------------------

/** One of the six vibe tags `GET /api/films/{id}/similar` can filter on. */
export type VibeTag = 'slow_burn' | 'feel_good' | 'sad' | 'tense' | 'dark' | 'quick_watch'

export interface SimilarFilm {
  film: { id: number; title: string; year: number | null; poster: string | null; runtime_min: number | null }
  score: number
  vibe_tags: string[]
  why: { top_shared_genres: string[]; shared_keywords_sample: string[] }
}

export interface SimilarFilmsResponse {
  seed: { id: number; title: string; year: number | null; poster: string | null }
  items: SimilarFilm[]
}

export interface GetSimilarFilmsParams {
  limit?: number
  maxRuntime?: number
  vibe?: string
}

// --- Personalised recommendations ----------------------------------------

export type RecommendationProfile = 'me' | 'partner' | 'together'

export interface RecommendationOffer {
  provider_id: number
  kind: string
  provider_name?: string | null
  logo?: string | null
}

export interface RecommendationItem {
  film: { id: number; title: string; year: number | null; poster: string | null; runtime_min: number | null }
  score: number
  providers: RecommendationOffer[]
  why: Record<string, number>
}

export interface RecommendationsResponse {
  profile: string
  model_version: string
  generated_at: string
  attribution: string
  items: RecommendationItem[]
}

export interface GetRecommendationsParams {
  profile?: RecommendationProfile
  limit?: number
  genres?: string
  runtime_buckets?: string
  vibe?: string
}

// --- Import & sync -----------------------------------------------------

export type ImportSource = 'auto' | 'export' | 'scrape' | 'rss'

export interface ImportRunTrigger {
  job_id: string
  user_id: number
  source_requested: ImportSource | 'export-upload'
  status: string
  source_used?: string
}

export type ImportJobStatus = 'running' | 'done' | 'done_unchanged' | 'failed'

export interface ImportCascadeStep {
  source: 'export' | 'scrape' | 'rss'
  outcome: 'ok' | 'failed' | 'running'
  code: string | null
}

export interface ImportCounts {
  watched: number
  diary: number
  ratings: number
  likes: number
  reviews: number
  matched: number
  unmatched: number
  skipped_duplicates: number
}

export interface ImportJob {
  job_id: string
  user_id: number
  source_requested: string
  source_used: string | null
  status: ImportJobStatus
  stage: string | null
  cascade: ImportCascadeStep[]
  counts: ImportCounts | null
}

export type UnmatchedStatus = 'pending' | 'matched' | 'ignored'

export interface UnmatchedImport {
  id: number
  name: string
  year: number | null
  source: FilmSource
  status: UnmatchedStatus
}

export interface UnmatchedResponse {
  items: UnmatchedImport[]
}

export type ResolveUnmatchedAction = { tmdb_id: number } | { action: 'ignore' }

export interface ResolveUnmatchedResult {
  id: number
  status: string
  film_id?: number
}

// --- Letterboxd credentials ---------------------------------------------

export interface CredentialStatus {
  configured: boolean
  tos_acknowledged: boolean
  backend: string
}

export interface CredentialSetResult {
  configured: boolean
}

// --- Rating / liked / seen writes ---------------------------------------

export interface SetRatingResult {
  rating: number
  letterboxd_rating: number | null
  source: string
}

export interface DeleteRatingResult {
  deleted: boolean
}

export interface SetLikedResult {
  liked: boolean
}

export interface MarkSeenResult {
  id: number
  watched_date: string | null
  rewatch: boolean
  source: string
}

// --- Settings: subscriptions & provider catalogue ------------------------

/** A row from `GET /api/settings/subscriptions` — a currently-active household subscription. */
export interface Subscription {
  provider_id: number
  provider_name: string
  logo: string | null
  monthly_cost_pence: number | null
  active: boolean
}

export interface SubscriptionsResponse {
  subscriptions: Subscription[]
}

/** One entry in a `PUT /api/settings/subscriptions` body — anything not included gets deactivated. */
export interface SubscriptionInput {
  provider_id: number
  monthly_cost_pence?: number
}

/** A row from `GET /api/providers` — the full TMDB catalogue for a region, for the user to pick from. */
export interface Provider {
  provider_id: number
  provider_name: string
  logo: string | null
  display_priority: number | null
}

export interface ProvidersResponse {
  region: string
  count: number
  providers: Provider[]
}

// ---------------------------------------------------------------------------

class ApiError extends Error {
  code?: string
  status?: number
  constructor(message: string, opts?: { code?: string; status?: number }) {
    super(message)
    this.name = 'ApiError'
    this.code = opts?.code
    this.status = opts?.status
  }
}

async function parseErrorBody(res: Response): Promise<{ detail: string; code?: string }> {
  let detail = `${res.status} ${res.statusText}`
  let code: string | undefined
  try {
    const body = await res.json()
    if (body?.detail) detail = body.detail
    if (body?.code) code = body.code
  } catch {
    /* non-JSON error body (e.g. connection-refused proxies, plain 404 pages) */
  }
  return { detail, code }
}

// Interim bearer-token guard (docs/API.md closing note) — every endpoint
// except /api/health requires this until Phase 4 JWTs replace it.
const DEV_TOKEN = import.meta.env.VITE_DEV_TOKEN ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (DEV_TOKEN && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${DEV_TOKEN}`)
  }
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers })
  } catch (err) {
    // Network error / connection refused — the backend isn't up yet.
    throw new ApiError(err instanceof Error ? err.message : 'Network error', { code: 'network_error' })
  }
  if (!res.ok) {
    const { detail, code } = await parseErrorBody(res)
    throw new ApiError(detail, { code, status: res.status })
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

function get<T>(path: string): Promise<T> {
  return request<T>(path)
}

function toQuery<T extends object>(params: T): string {
  const usp = new URLSearchParams()
  for (const [key, value] of Object.entries(params as Record<string, unknown>)) {
    if (value === undefined || value === null || value === '') continue
    usp.set(key, String(value))
  }
  const qs = usp.toString()
  return qs ? `?${qs}` : ''
}

export const api = {
  base: BASE,
  health: () => get<Health>('/api/health'),
  search: (q: string) => get<SearchResponse>(`/api/tmdb/search?q=${encodeURIComponent(q)}`),

  // Films
  getFilms: (params: GetFilmsParams = {}) => get<FilmsResponse>(`/api/films${toQuery(params)}`),
  getFilm: (id: number) => get<FilmDetail>(`/api/films/${id}`),
  getFilmAvailability: (id: number) => get<FilmAvailability>(`/api/films/${id}/availability`),
  getSimilarFilms: (tmdbId: number, opts: GetSimilarFilmsParams = {}) =>
    get<SimilarFilmsResponse>(
      `/api/films/${tmdbId}/similar${toQuery({ limit: opts.limit, max_runtime: opts.maxRuntime, vibe: opts.vibe })}`,
    ),
  searchRematchCandidates: (filmId: number, q: string) =>
    get<RematchSearchResponse>(`/api/films/${filmId}/rematch/search${toQuery({ q })}`),
  rematchFilm: (filmId: number, correctTmdbId: number) =>
    request<RematchResult>(`/api/films/${filmId}/rematch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ correct_tmdb_id: correctTmdbId }),
    }),
  getRecommendations: (params: GetRecommendationsParams = {}) =>
    get<RecommendationsResponse>(`/api/recommendations${toQuery(params)}`),

  // Import
  runImport: (user: 1 | 2, source: ImportSource = 'auto') =>
    request<ImportRunTrigger>('/api/import/letterboxd/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user, source }),
    }),
  uploadImportZip: (user: 1 | 2, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ImportRunTrigger>(`/api/import/letterboxd?user=${user}`, {
      method: 'POST',
      body: form,
    })
  },
  getImportJob: (jobId: string) => get<ImportJob>(`/api/import/jobs/${jobId}`),
  getUnmatched: (status: UnmatchedStatus = 'pending') =>
    get<UnmatchedResponse>(`/api/import/unmatched${toQuery({ status })}`),
  resolveUnmatched: (id: number, action: ResolveUnmatchedAction) =>
    request<ResolveUnmatchedResult>(`/api/import/unmatched/${id}/resolve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(action),
    }),

  // Credentials
  getCredentialStatus: (_user: 1 | 2) => get<CredentialStatus>('/api/letterboxd/credentials/status'),
  setCredentials: (password: string, acknowledgeTos: boolean) =>
    request<CredentialSetResult>('/api/letterboxd/credentials', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password, acknowledge_tos: acknowledgeTos }),
    }),
  deleteCredentials: () =>
    request<CredentialSetResult>('/api/letterboxd/credentials', { method: 'DELETE' }),

  // Rating / liked / seen writes
  setRating: (filmId: number, rating: number, user: 1 | 2) =>
    request<SetRatingResult>(`/api/films/${filmId}/rating`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rating, user }),
    }),
  deleteRating: (filmId: number, user: 1 | 2) =>
    request<DeleteRatingResult>(`/api/films/${filmId}/rating?user=${user}`, {
      method: 'DELETE',
    }),
  setLiked: (filmId: number, liked: boolean, user: 1 | 2) =>
    request<SetLikedResult>(`/api/films/${filmId}/like`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ liked, user }),
    }),
  markSeen: (filmId: number, user: 1 | 2, watchedDate: string | null = null, rewatch = false) =>
    request<MarkSeenResult>(`/api/films/${filmId}/seen`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ watched_date: watchedDate, rewatch, user }),
    }),

  // Settings: subscriptions & provider catalogue
  getSubscriptions: () => get<SubscriptionsResponse>('/api/settings/subscriptions'),
  putSubscriptions: (subscriptions: SubscriptionInput[]) =>
    request<SubscriptionsResponse>('/api/settings/subscriptions', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subscriptions }),
    }),
  getProviders: (region = 'GB') => get<ProvidersResponse>(`/api/providers${toQuery({ region })}`),
}

export { ApiError }
