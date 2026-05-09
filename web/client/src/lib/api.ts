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
  createdAt: string
}

export type Trade = {
  entryTime?: string
  exitTime?: string
  entryPrice?: number
  exitPrice?: number
  side?: string
  pnl?: number
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

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
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

export const api = {
  health: () => getJson<{ status: string; db: boolean }>('/api/health'),
  sources: () => getJson<{ items: Source[] }>('/api/sources'),
  instruments: () => getJson<{ items: Instrument[] }>('/api/instruments'),
  imports: (params: { limit?: number; offset?: number; source?: string; instrument?: string } = {}) =>
    getJson<Paginated<ImportRecord>>(`/api/imports?${qs(params)}`),
  results: (params: { limit?: number; offset?: number; strategy?: string; instrument?: string; source?: string } = {}) =>
    getJson<Paginated<ResultSummary>>(`/api/results?${qs(params)}`),
  result: (id: string) => getJson<ResultDetail>(`/api/results/${encodeURIComponent(id)}`),
}

function qs(params: Record<string, unknown>): string {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') u.set(k, String(v))
  }
  return u.toString()
}
