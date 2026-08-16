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
  cacheKey: string
  /** Compact ISO-8601 UTC timestamp (e.g. "20260511T134522.123Z"). Null for
   *  legacy entries written before model versioning shipped. */
  versionId: string | null
  strategyName: string
  instrumentId: string | number | null
  instrumentSymbol: string | null
  sourceId: string | number | null
  sourceName: string | null
  timeframe: string
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

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(url, { signal })
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

export const api = {
  health: (signal?: AbortSignal) =>
    getJson<{ status: string; db: boolean }>('/api/health', signal),
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
  aggregate: async (req: AggregateRequest): Promise<AggregateResponse> => {
    const res = await fetch('/api/aggregate', {
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
    const res = await fetch('/api/imports', { method: 'POST', body: fd })
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
