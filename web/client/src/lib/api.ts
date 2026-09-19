// Tiny fetch wrapper + typed response shapes for the Express API on :3000.
// Vite proxies /api/* there in dev (vite.config.ts).

export type Paginated<T> = {
  items: T[]
  total: number
  limit: number
  offset: number
}

export type Source = {
  id: string
  name: string
  description: string | null
  created_at: string
}

export type InstrumentSourceSlice = {
  sourceId: string
  sourceName: string
  timeframe: string
  candleCount: number
  fromDate: string
  toDate: string
}

export type Instrument = {
  id: string
  symbol: string
  name: string | null
  type: string
  pricePrecision: number
  pipSize: number
  sources: InstrumentSourceSlice[]
}

export type ImportRecord = {
  id: string
  sourceId: string
  sourceName: string
  instrumentId: string
  instrumentSymbol: string
  timeframe: string
  filePath: string
  fileName: string
  rowCount: number
  importedAt: string
  /** SHA-256 of the imported CSV's content. Null for rows imported before
   *  the CSV archive feature shipped. */
  fileHash: string | null
  /** Relative path the file is archived under, e.g. "yahoo/QQQ/2008/H1.csv".
   *  Null for legacy pre-archive rows. */
  archivePath: string | null
}

/** One slice of an upload — multi-year files are split server-side, so a
 *  single POST returns N of these (one per year present in the input). */
export type SliceOutcome =
  | { year: number; status: 'created' | 'overwritten'; import: ImportRecord }
  | { year: number; status: 'skipped'; import: ImportRecord }

/** Preview entry returned with a 409 conflict — describes what each year
 *  WOULD do if the user resubmits with force=true. */
export type SlicePreview = {
  year: number
  archivePath: string
  status: 'would-create' | 'would-skip' | 'would-overwrite' | 'conflict'
  rowCount: number
  fileHash: string
  existing: ImportRecord | null
}

/** The read-only data-cohesiveness advisory the loader attaches to a
 *  completed import. Never blocks the import — purely informational. */
export type CohesionCategory = 'ohlc' | 'duplicate' | 'order' | 'gap' | 'outlier'

export type CohesionFinding = {
  category: CohesionCategory
  timestamp: string
  detail: string
}

export type CohesionReport = {
  totalBars: number
  ok: boolean
  totalIssues: number
  counts: Record<CohesionCategory, number>
  /** Sample findings (capped server-side); counts stay exact. */
  examples: CohesionFinding[]
}

/** Discriminated union of POST /api/imports response shapes. */
export type UploadImportResponse =
  | { status: 'completed'; imports: SliceOutcome[]; cohesion?: CohesionReport }
  | { status: 'conflict'; message: string; imports: SlicePreview[] }
  | { status: 'compressed_chunk'; error: string; hint: string; detail: string }

export type UploadImportRequest = {
  file: File
  symbol: string
  type: string
  timeframe: string
  source: string
  force: boolean
}

/** What one import's undo would touch — shared by the preview and the result. */
type ImportDeleteTarget = {
  importId: number
  symbol: string
  source: string
  timeframe: string
  /** The calendar year the import's archive path encodes; it bounds the delete. */
  year: number
  fileName: string
  archivePath: string
  /** Rows the import recorded writing. */
  rowsRecorded: number
  /** Candles present in that (instrument, source, timeframe, year) window now. */
  candlesInWindow: number
}

/** DELETE /api/imports/:id?dry_run=true — what would go, with nothing removed. */
export type ImportDeletePreview = ImportDeleteTarget & {
  status: 'dry_run'
  candlesWouldDelete: number
}

/** DELETE /api/imports/:id — what went. The archived CSV is deliberately kept. */
export type ImportDeleteResult = ImportDeleteTarget & {
  status: 'deleted'
  candlesDeleted: number
  archiveKept: boolean
}

/** POST /api/aggregate — build higher-timeframe rollups from a source tf. */
export type AggregateRequest = {
  symbol: string
  source: string
  sourceTf: string
  targetTfs: string[]
  /** Missing-only: skip a target that already has rows. */
  skipExisting: boolean
}

export type AggregateTargetResult =
  | { timeframe: string; status: 'aggregated'; rowsWritten: number }
  | { timeframe: string; status: 'skipped'; existingRows: number }

export type AggregateResponse = {
  status: 'completed'
  symbol: string
  source: string
  sourceTf: string
  results: AggregateTargetResult[]
}

export type ResultSummary = {
  id: string
  instrumentSymbol: string
  strategyName: string
  timeframe: string
  dataSource: string
  startDate: string | null
  endDate: string | null
  initialCapital: number
  finalEquity: number
  totalReturnPct: number | null
  sharpeRatio: number | null
  maxDrawdownPct: number | null
  totalTrades: number
  winRate: number | null
  modelCacheKey: string | null
  modelCacheHit: boolean | null
  modelVersionId: string | null
  createdAt: string
}

export type Trade = {
  entryTime?: string
  exitTime?: string
  entryPrice?: number
  exitPrice?: number
  quantity?: number
  side?: string
  pnl?: number
  commission?: number
  returnPct?: number
  [k: string]: unknown
}

export type EquityPoint = {
  timestamp: string
  equity: number
}

export type PerformanceMetrics = {
  totalReturnPct?: number
  annualizedReturnPct?: number
  sharpeRatio?: number
  sortinoRatio?: number
  calmarRatio?: number
  maxDrawdownPct?: number
  totalTrades?: number
  winRate?: number
  avgWin?: number
  avgLoss?: number
  profitFactor?: number
  buyAndHoldReturnPct?: number
  [k: string]: unknown
}

export type TrainedModel = {
  /** Null only for a metadata.json that predates or omits it — see ModelsPage. */
  cacheKey: string | null
  /** Compact ISO-8601 UTC timestamp (e.g. "20260511T134522.123Z"). Null for
   *  legacy entries written before model versioning shipped. */
  versionId: string | null
  strategyName: string | null
  instrumentId: string | number | null
  instrumentSymbol: string | null
  sourceId: string | number | null
  sourceName: string | null
  /** The loader does not record the timeframe it trained on; Java-era entries did. */
  timeframe: string | null
  trainingFromEpochSec: number | null
  trainingToEpochSec: number | null
  trainingBarCount: number | null
  hyperparams: Record<string, string>
  dl4jVersion: string | null
  validationAccuracyPct: number | null
  trainingDurationMs: number | null
  createdAt: string | null
  backtestCount: number
  diskPath: string
}

export type ResultDetail = ResultSummary & {
  result: {
    strategyName: string
    instrumentSymbol: string
    timeframe: string
    dataSource: string
    startDate: string
    endDate: string
    initialCapital: number
    finalEquity: number
    metrics: PerformanceMetrics
    trades: Trade[]
    equityHistory: EquityPoint[]
  } | null
}

/**
 * Base URL of the API. The client is a standalone SPA served on its own origin,
 * so it has to be told where the API lives — set VITE_API_URL at build time
 * (docker compose passes it) or in a .env file for local dev. Falls back to a
 * same-origin relative path, which is what the Vite dev server's proxy expects.
 */
const API_BASE = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')

/**
 * Prefixes a path with the configured API base.
 *
 * Exported because not every API URL is fetched: the docs pages are rendered by
 * the API service and opened as ordinary links, and those must be absolute too.
 * A relative `/readme` resolves against the client's own origin, where nginx's
 * SPA fallback answers with index.html and the router redirects to home.
 */
export function apiUrl(path: string): string {
  return `${API_BASE}${path}`
}

/** One registered strategy, from GET /api/strategies (served by the Java engine). */
export type Strategy = {
  name: string
  description: string
  /** Parameter name -> default value, as strings. Drives the parameter form. */
  defaultParameters: Record<string, string>
  /** True for strategies that need a cached trained model before they can run. */
  requiresTrainedModel: boolean
}

export type RunRequest = {
  strategy: string
  instrument: string
  timeframe: string
  source: string
  from?: string
  to?: string
  capital?: number
  parameters?: Record<string, string>
  modelVersion?: string
  aggregateMissing?: boolean
}

/** The full result POST /api/run returns — same shape GET /api/results/:id nests under `result`. */
export type RunResult = {
  strategyName: string
  instrumentSymbol: string
  timeframe: string
  dataSource: string
  startDate: string
  endDate: string
  initialCapital: number
  finalEquity: number
  metrics: PerformanceMetrics
  trades: Trade[]
  equityHistory: EquityPoint[]
  modelCacheKey: string | null
  modelCacheHit: boolean | null
  modelVersionId: string | null
}

export type AuditRequest = {
  symbol?: string
  source?: string
  timeframe?: string
  checks?: string[]
  examples?: boolean
}

export type AuditSeries = {
  symbol: string
  source: string
  timeframe: string
  bars: number
  ok: boolean
  totalIssues: number
  counts: Record<string, number>
  summary: string
  examples?: { category: string; timestamp: string | null; detail: string }[]
}

export type AuditResponse = {
  status: string
  checks: string[]
  seriesAudited: number
  totalIssues: number
  ok: boolean
  items: AuditSeries[]
}

/** One dependency's verdict in the health report. */
export type ServiceHealth = {
  name: string
  ok: boolean
  /** "connected"/"reachable" when up; the failure text when not. */
  detail: string
  latencyMs: number
}

/**
 * GET /api/health — the whole system's liveness, not just the API's. Always
 * arrives with HTTP 200 when the API is alive, so a rejected promise means the
 * API itself is unreachable and `ok: false` means something behind it is.
 */
export type HealthReport = {
  status: 'ok' | 'degraded' | 'maintenance'
  ok: boolean
  /** True when the API's MAINTENANCE switch is on — services may all be fine. */
  maintenance?: boolean
  maintenanceMessage?: string
  db: boolean
  services: ServiceHealth[]
}

/**
 * Build-time maintenance switch: set VITE_MAINTENANCE=1 and the client shows
 * the maintenance page without asking anything.
 *
 * The API's own MAINTENANCE env var is the one to reach for normally — it is a
 * restart rather than a rebuild, and it can still say why. This one exists for
 * the case that one cannot cover: taking the UI down while the API itself is
 * being replaced, when there is nothing left to ask.
 */
export const FORCE_MAINTENANCE = /^(1|true|yes|on)$/i.test(
  import.meta.env.VITE_MAINTENANCE ?? '',
)

/** One entry in the doc registry, from GET /api/docs. */
export type DocSummary = {
  name: string
  label: string
  /** The segment this doc lives at under /docs/. */
  slug: string
}

/** A rendered doc. `html` is the API's markdown render, headings already anchored. */
export type DocContent = DocSummary & {
  html: string
  /** Set only when viewing a captured revision rather than the live file. */
  revision: { id: number; capturedAt: string } | null
}

export type DocRevision = {
  id: number
  capturedAt: string
  contentHash: string
  sizeBytes: number
}

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(apiUrl(url), { signal })
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body && body.error) msg = body.error
    } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json() as Promise<T>
}

/** True when an error came from an aborted fetch (caller should silently bail). */
export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

/** POST/DELETE with a JSON body, surfacing the API's `error` field as the thrown message. */
async function sendJson<T>(url: string, method: 'POST' | 'DELETE', body?: unknown): Promise<T> {
  const res = await fetch(apiUrl(url), {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  let parsed: unknown
  try { parsed = await res.json() } catch { parsed = null }
  if (!res.ok) {
    const msg = (parsed && typeof parsed === 'object' && 'error' in parsed)
      ? String((parsed as { error: string }).error)
      : `${res.status} ${res.statusText}`
    throw new Error(msg)
  }
  return parsed as T
}

export const api = {
  health: (signal?: AbortSignal) => getJson<HealthReport>('/api/health', signal),
  sources: (signal?: AbortSignal) =>
    getJson<{ items: Source[] }>('/api/sources', signal),
  instruments: (signal?: AbortSignal) =>
    getJson<{ items: Instrument[] }>('/api/instruments', signal),
  imports: (
    params: {
      limit?: number; offset?: number; source?: string; instrument?: string;
      sort?: 'imported' | 'source' | 'instrument' | 'timeframe' | 'archive' | 'file' | 'rows';
      dir?: 'asc' | 'desc';
    } = {},
    signal?: AbortSignal,
  ) => getJson<Paginated<ImportRecord>>(`/api/imports?${qs(params)}`, signal),
  results: (
    params: { limit?: number; offset?: number; strategy?: string; instrument?: string; source?: string } = {},
    signal?: AbortSignal,
  ) => getJson<Paginated<ResultSummary>>(`/api/results?${qs(params)}`, signal),
  result: (id: string, signal?: AbortSignal) =>
    getJson<ResultDetail>(`/api/results/${encodeURIComponent(id)}`, signal),
  models: (signal?: AbortSignal) =>
    getJson<{ items: TrainedModel[]; modelsDir: string }>('/api/models', signal),
  strategies: (signal?: AbortSignal) =>
    getJson<{ items: Strategy[] }>('/api/strategies', signal),
  docs: (signal?: AbortSignal) => getJson<{ items: DocSummary[] }>('/api/docs', signal),
  doc: (slug: string, rev?: string | null, signal?: AbortSignal) =>
    getJson<DocContent>(
      `/api/docs/${encodeURIComponent(slug)}${rev ? `?rev=${encodeURIComponent(rev)}` : ''}`,
      signal,
    ),
  docHistory: (slug: string, signal?: AbortSignal) =>
    getJson<{ label: string; items: DocRevision[] }>(
      `/api/docs/${encodeURIComponent(slug)}/history`,
      signal,
    ),
  /** Runs a backtest and returns the saved result. Synchronous: expect ~a second. */
  run: (req: RunRequest) => sendJson<RunResult>('/api/run', 'POST', req),
  deleteResult: (id: string | number) =>
    sendJson<{ status: string; id: number }>(`/api/results/${encodeURIComponent(String(id))}`, 'DELETE'),
  /** Preview an import undo: returns the candle count without deleting anything. */
  previewDeleteImport: (id: string | number) =>
    sendJson<ImportDeletePreview>(
      `/api/imports/${encodeURIComponent(String(id))}?dry_run=true`, 'DELETE',
    ),
  deleteImport: (id: string | number) =>
    sendJson<ImportDeleteResult>(`/api/imports/${encodeURIComponent(String(id))}`, 'DELETE'),
  audit: (req: AuditRequest) => sendJson<AuditResponse>('/api/audit', 'POST', req),
  aggregate: async (req: AggregateRequest): Promise<AggregateResponse> => {
    const res = await fetch(apiUrl('/api/aggregate'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        symbol: req.symbol,
        source: req.source,
        source_tf: req.sourceTf,
        target_tfs: req.targetTfs,
        skip_existing: req.skipExisting,
      }),
    })
    let body: unknown
    try { body = await res.json() } catch { body = null }
    if (!res.ok) {
      const msg = (body && typeof body === 'object' && 'error' in body)
        ? String((body as { error: string }).error)
        : `${res.status} ${res.statusText}`
      throw new Error(msg)
    }
    return body as AggregateResponse
  },
  uploadImport: async (req: UploadImportRequest): Promise<UploadImportResponse> => {
    const fd = new FormData()
    fd.append('file', req.file)
    fd.append('symbol', req.symbol)
    fd.append('type', req.type)
    fd.append('timeframe', req.timeframe)
    fd.append('source', req.source)
    fd.append('force', String(req.force))
    const res = await fetch(apiUrl('/api/imports'), { method: 'POST', body: fd })
    // 200 and 409 both carry a structured body (created/skipped/overwritten/conflict).
    // 4xx other than 409 surface as plain { error } — convert to a throw.
    let body: unknown
    try { body = await res.json() } catch { body = null }
    if (res.status === 200 || res.status === 409) {
      return body as UploadImportResponse
    }
    const msg = (body && typeof body === 'object' && 'error' in body)
      ? String((body as { error: string }).error)
      : `${res.status} ${res.statusText}`
    throw new Error(msg)
  },
}

function qs(params: Record<string, unknown>): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') u.set(k, String(v))
  }
  return u.toString()
}
