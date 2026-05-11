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
    params: { limit?: number; offset?: number; source?: string; instrument?: string } = {},
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
}

function qs(params: Record<string, unknown>): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') u.set(k, String(v))
  }
  return u.toString()
}
