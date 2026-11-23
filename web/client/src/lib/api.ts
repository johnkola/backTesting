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

/**
 * Whether a position can be opened in this instrument.
 *
 * Mirrors `InstrumentType.isTradeable()` on the Java side, which is where the
 * rule is actually enforced — an index is real data but not a holding, and the
 * engine refuses a backtest on one. This exists so the UI can say so before the
 * user submits, not instead of the engine check.
 */
export function isTradeable(instrument: { type: string }): boolean {
  return instrument.type !== 'INDEX'
}

/**
 * Did the strategy beat simply holding the instrument?
 *
 * The one question a backtest exists to answer, and the easiest to skip past: a
 * result showing +136% looks like a success until you see that holding returned
 * +314% over the same bars. Returns `null` when there is nothing to compare
 * against. Buy-and-hold pays the same costs as the strategy since B6.
 *
 * Both figures are measured over the period the strategy could actually trade,
 * so the comparison is like-for-like; the engine used to start the benchmark
 * before the strategy's warmup, which flattered nothing and punished the longest
 * indicator periods hardest.
 */
/**
 * Where a strategy is explained, in the docs rendered at /docs.
 *
 * Each strategy has its own section in README.md headed with its registry name,
 * so the anchor is the name itself — no mapping table to fall out of step with
 * the registry. A strategy added without a doc section links to the list, which
 * is a wrong-but-harmless destination rather than a dead anchor.
 */
export function strategyDocHref(name: string): string {
  return `/docs/readme#${encodeURIComponent(name)}`
}

export function beatBuyAndHold(
  r: { totalReturnPct?: number | null; buyAndHoldReturnPct?: number | null },
): boolean | null {
  const ret = r.totalReturnPct
  const bh = r.buyAndHoldReturnPct
  if (typeof ret !== 'number' || typeof bh !== 'number') return null
  return ret > bh
}

/** How far short of (or ahead of) holding, in percentage points. */
export function vsBuyAndHold(
  r: { totalReturnPct?: number | null; buyAndHoldReturnPct?: number | null },
): number | null {
  const ret = r.totalReturnPct
  const bh = r.buyAndHoldReturnPct
  if (typeof ret !== 'number' || typeof bh !== 'number') return null
  return ret - bh
}

/** Sort keys /api/results accepts. Anything else falls back to `created`. */
export type ResultSortKey =
  | 'created' | 'instrument' | 'strategy' | 'timeframe' | 'source'
  | 'return' | 'hold' | 'excess' | 'sharpe' | 'drawdown' | 'trades' | 'winrate'

export type Candle = {
  timestamp: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

/**
 * A price series for charting. `stride` is 1 when every bar is present and
 * higher when the server strided a long range down — worth surfacing so a chart
 * can say "1 in 3 bars" rather than implying it drew all of them.
 */
export type CandleSeries = {
  items: Candle[]
  total: number
  returned: number
  stride: number
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
  /** Set when the import's check errors were stored on an override (R12, R65). */
  checkOverride?: (CheckOverride & { by?: string | null }) | null
  /** The newest audit row of the same year slice: the version the candles
   *  hold. Equal to `id` when this row is current; only then can it be undone. */
  currentId?: string | null
}

/** One slice of an upload — multi-year files are split server-side, so a
 *  single POST returns N of these (one per year present in the input). */
export type SliceOutcome =
  | { year: number; status: 'created' | 'extended' | 'overwritten'; import: ImportRecord; diff?: SliceDiff | null }
  | { year: number; status: 'skipped'; import: ImportRecord; diff?: SliceDiff | null }

/**
 * How one year of a new file differs from the stored year, compared as bars
 * rather than text. `laterOnly` with nothing changed or removed is an update
 * (a refreshed download); anything changed or removed is a data fix.
 */
export type SliceDiff = {
  added: number
  changed: number
  removed: number
  laterOnly: boolean
  /** Up to five timestamps that changed or were removed. */
  examples: string[]
  /** Of `changed`, the bars whose open, high, low or close moved; the rest
   *  changed their volume only. Absent on imports from before it was kept. */
  priceChanged?: number
  /** Up to five timestamps whose prices changed or that were removed. */
  priceExamples?: string[]
}

/** What kind of change a file version was, against the one before it. */
export type ChangeKind = 'new' | 'update' | 'fix' | 'update+fix' | 'same'

/** The file version an upload recorded, or matched when it was already loaded. */
export type UploadedFile = {
  id: number
  hash: string
  name: string
  changeKind: ChangeKind
  importedAt: string
  /** These exact bytes are what the database holds now; nothing was parsed. */
  alreadyLoaded: boolean
  /** These bytes were recorded before (e.g. re-loaded after an undo). */
  seenBefore?: boolean
}

/** One row of GET /api/import-files: a series' file at one version. */
export type FileVersion = {
  id: string
  sourceName: string
  instrumentSymbol: string
  timeframe: string
  fileName: string
  fileHash: string
  byteSize: number
  rowCount: number
  firstDate: string | null
  lastDate: string | null
  changeKind: ChangeKind
  years: number[]
  /** Per year, keyed by the year as a string. */
  diff: Record<string, SliceDiff>
  importedAt: string
  lastSeenAt: string
  importedBy: string | null
}

/** Preview entry returned with a 409 conflict — describes what each year
 *  WOULD do if the user resubmits with force=true. */
export type SlicePreview = {
  year: number
  archivePath: string
  status: 'would-create' | 'would-skip' | 'would-extend' | 'would-overwrite' | 'conflict'
  rowCount: number
  fileHash: string
  existing: ImportRecord | null
  /** What forcing this year would change; null for a new year or a lost archive. */
  diff?: SliceDiff | null
}

/** The loader's check report on an import. A finding is an `error` or a
 *  `warning`: a file with an error is not stored unless it is overridden with
 *  a reason (R12, R65); a warning is reported and the data goes in. */
export type CohesionCategory = 'ohlc' | 'duplicate' | 'order' | 'gap' | 'outlier'

export type CohesionFinding = {
  category: CohesionCategory
  timestamp: string
  detail: string
  /** Absent on a report from a loader older than severities: a warning. */
  severity?: 'error' | 'warning'
}

/** Check errors someone chose to store anyway, as recorded on the import. */
export type CheckOverride = { reason: string, errors: number }

export type CohesionReport = {
  totalBars: number
  ok: boolean
  totalIssues: number
  /** Exact counts by severity; `examples` is capped. */
  errors?: number
  warnings?: number
  /** True when the data has errors: its bars are flagged rows (G75). */
  blocked?: boolean
  counts: Record<CohesionCategory, number>
  /** Sample findings (capped server-side); counts stay exact. */
  examples: CohesionFinding[]
}

/** Rows in the file that produced no candle (DESIGN-GAPS R6). The count is
 *  exact; examples are the first few, with the file's own line numbers. */
export type SkippedRows = {
  count: number
  examples: { line: number, reason: string, text: string }[]
  /** Readable bars left out of an intraday stock file because they start
   *  outside the regular New York session. Not a fault in the file. */
  outsideSession?: number
}

/**
 * What a file's check errors left behind (G75; R12, R65). The file is stored
 * whatever the checks say; each bar with an error is a flagged row, and while
 * `held` is true the feature build and training refuse to read the series.
 */
export type FlaggedRows = {
  /** Bars with an error in this file. */
  errors: number
  /** True until the rows are accepted or a corrected file is imported. */
  held: boolean
  /** The errors as sentences; the first few, then how many more. */
  lines: string[]
  /** Flagged rows of this file still not accepted. */
  open?: number
  hint?: string | null
}

/** A series that has flagged rows, from GET /api/checks. */
export type FlaggedSeries = {
  symbol: string, source: string, timeframe: string
  open: number, accepted: number
  from: string, to: string
  reason: string | null
}

/** Discriminated union of POST /api/imports response shapes. */
export type UploadImportResponse =
  | { status: 'completed'; imports: SliceOutcome[]; cohesion?: CohesionReport; skipped?: SkippedRows; file?: UploadedFile; checkOverride?: CheckOverride; flagged?: FlaggedRows }
  | { status: 'conflict'; message: string; imports: SlicePreview[]; file?: { hash: string, name: string } }
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
  /** The slice's newest audit row, the version the candles hold. */
  currentImportId?: number
}

/** DELETE /api/imports/:id?dry_run=true — what would go, with nothing removed.
 *  Only the current version of a year slice can be undone; an older one answers
 *  409 `superseded` instead (G46). */
export type ImportDeletePreview = ImportDeleteTarget & {
  status: 'dry_run'
  candlesWouldDelete: number
  /** Every audit row of the slice goes with it: this version and the older ones. */
  auditRowsWouldDelete?: number
  olderVersionIds?: number[]
}

/** DELETE /api/imports/:id — what went. The archived CSV is deliberately kept. */
export type ImportDeleteResult = ImportDeleteTarget & {
  status: 'deleted'
  candlesDeleted: number
  auditRowsDeleted?: number
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
  /** Benchmark over the same bars. Null on results saved before it was recorded. */
  buyAndHoldReturnPct?: number | null
  id: string
  instrumentSymbol: string
  strategyName: string
  /** `benchmark` for one of the five rule benchmarks (C8), else `model`; null
   *  when the engine did not say (down, or the name is no longer registered). */
  strategyKind?: 'model' | 'benchmark' | null
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
  /**
   * The model's training window (B4): `known`, `not recorded` or `version
   * gone`; null for a strategy with no model.
   */
  modelWindow?: string | null
  /** ISO dates of the model's training window, when known. */
  modelTrainFrom?: string | null
  modelTrainTo?: string | null
  modelTrainSymbol?: string | null
  /** True when the result covers bars its model was trained on; null unless known. */
  inSample?: boolean | null
  /**
   * The saved strategies' test windows this result scores (G48, R69), each with
   * the looks recorded before it; empty when it scores none.
   */
  testWindows?: TestWindowLook[]
  /** True when a window it scores was looked at before; null when it scores none. */
  testWindowReused?: boolean | null
  /** Percent charged on each realized gain, or null when scored before tax (G20). */
  taxRate?: number | null
}

/** One look at a saved test window (`test_window_looks`), as Results shows it. */
export type TestWindowLook = {
  from: string
  to: string
  /** Looks at this window recorded before this one: 0 is the first look. */
  looksBefore: number
  firstLookAt: string
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
  /** Mean return over the downside deviation, annualised. Absent (or null)
   *  when no bar lost: not available, shown as a dash, never 0 (G29). Results
   *  stored before 2026-11-15 carry a wrong value under `sortingRatio`. */
  sortinoRatio?: number | null
  calmarRatio?: number
  maxDrawdownPct?: number
  totalTrades?: number
  /** Over decided trades only: break-even trades are left out (G29). */
  winRate?: number
  avgWin?: number
  avgLoss?: number
  /** Gross profit over gross loss. Absent (or null) when no trade lost (G29). */
  profitFactor?: number | null
  /** Trades closed at exactly zero P&L: neither wins nor losses. */
  breakEvenTrades?: number
  totalCommissions?: number
  /** Tax paid on realized gains, in money; 0 when scored before tax (G20). */
  totalTax?: number
  /** Per fill, |fill - bar price| x quantity, summed. 0 before 2026-11-15 (G29). */
  totalSlippage?: number
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

/**
 * Buy-and-hold beside a result (B6, D6): the always-long strategy over the same
 * bars, scored with the run under the same costs and stored in it. One buy at
 * the first fill (the open after the first evaluated bar), one sale at the last
 * close. Absent on results stored before B6.
 */
/**
 * A rule benchmark over one walk-forward fold (B7): Faber's 10-month average or
 * dual momentum, with the run's costs and tax. Figures are null when it is not
 * available, and `reason` says why.
 */
export type FoldBenchmark = {
  name: 'faber' | 'dual_momentum' | string
  available: boolean
  reason: string | null
  returnPct: number | null
  sharpe: number | null
  maxdd: number | null
  /** Position changes, as the fold's own `trades` counts them. */
  trades: number | null
  barsHeld: number | null
  commissions: number | null
  gainsTax: number | null
}

/** One cost model of a run: a rate as a decimal (0.001 is 0.1%) or a fixed amount. */
export type RunCost = { type: 'percentage' | 'fixed' | 'other' | string, value: number | null, description: string | null }

/**
 * What a backtest was asked to do (G40), from `backtest_results.settings_json`.
 * The result's own `startDate` is the first bar after the warm-up; these are
 * the dates asked for.
 */
export type RunSettings = {
  requestedFrom: string | null
  requestedTo: string | null
  /** The strategy parameters as passed. */
  parameters: Record<string, string>
  /** The defaults with `parameters` laid over them: what the strategy ran with. */
  effectiveParameters: Record<string, string>
  initialCapital: number
  commission: RunCost
  slippage: RunCost
  taxRatePct: number | null
  pinnedModelVersion: string | null
}

/** Faber or dual momentum beside a backtest result (B7), traded with the run's costs. */
export type RuleBenchmark = {
  name: 'faber' | 'dual_momentum' | string
  available: boolean
  reason: string | null
  metrics: PerformanceMetrics | null
  finalEquity: number | null
  trades: Trade[] | null
  /** One value per `equityHistory` point, in the same order. */
  equity: number[] | null
}

export type BuyAndHold = {
  metrics: PerformanceMetrics
  finalEquity: number
  trades: Trade[]
  /** One value per `equityHistory` point, in the same order. */
  equity: number[]
}

export type ResultDetail = ResultSummary & {
  /**
   * Buy-and-hold equity, one value per `result.equityHistory` point and in the
   * same order: `result.buyAndHold.equity`. Null on a result stored before B6,
   * or one whose curve cannot be paired; `holdEquityNote` says why.
   */
  holdEquity?: number[] | null
  holdEquityNote?: string | null
  /** What the run was asked to do (G40); null on a result stored before 2026-11-22. */
  settings?: RunSettings | null
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
    buyAndHold?: BuyAndHold | null
    benchmarks?: RuleBenchmark[] | null
    /** Percent on realized gains, or null before tax (G20). */
    taxRatePct?: number | null
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
  /** `benchmark` for an indicator rule kept for comparison only (C8). Absent
   *  from an engine older than the kinds, which is read as `model`. */
  kind?: 'model' | 'benchmark'
}

/**
 * A training request, in the client's own vocabulary. `since`/`until` are
 * deliberately absent: the caller names the series and gets its whole stored
 * history, so a model's recorded training window always matches what the
 * Instruments page says is there. See `engineWindow` below.
 */
export type TrainRequest = {
  symbol: string
  source: string
  timeframe: string
  /** First stored candle for the series, ISO-8601 — from GET /api/instruments. */
  firstCandle: string
  /** Last stored candle for the series, ISO-8601. */
  lastCandle: string
  /** 'auto' reuses a model already cached under the same key; 'force' retrains. */
  mode: 'auto' | 'force'
  /** camelCase hyperparameter overrides, as the strategy form spells them. */
  parameters: Record<string, string>
}

export type TrainResponse = {
  status: string
  strategy: string
  cacheKey: string
  versionId: string
  symbol?: string
  source?: string
  timeframe?: string
  trainSamples?: number
  valSamples?: number
  finalTrainAcc?: number
  finalValAcc?: number
  finalTrainLoss?: number
  finalValLoss?: number
}

/**
 * The candle window to train on, given a series' first and last stored candle.
 *
 * Only `until` is adjusted, and only by a millisecond. The loader selects
 * `timestamp >= since AND timestamp < until` (`python/nn/data.py`), so an
 * `until` equal to the last candle's timestamp would exclude that candle; a
 * nudge past it includes the whole series and nothing more. `since` is the
 * first stored timestamp unchanged.
 *
 * It used to shift both bounds forward by one whole bar, to match the Ta4j
 * **bar-end** times the engine addressed candles by. Two changes removed the
 * reason and left the shift behind: the loader took the data window out of the
 * model cache key (`python/nn/nn_api.py`), so a window mismatch no longer
 * makes a model unfindable; and the engine's NN strategies now key by a bar's
 * own date rather than its end instant (`BacktestEngine.barDate`). What the
 * shift did by then was drop the series' first candle from every training run
 * — `since` landed one bar past it — while the page claimed to be learning
 * from all of them.
 *
 * Keep this in step with the Java side: both must describe the same window, or
 * a model's recorded provenance stops matching the bars it saw.
 */
export function engineWindow(
  firstCandle: string, lastCandle: string,
): { since: string; until: string } {
  const since = new Date(firstCandle)
  const until = new Date(new Date(lastCandle).getTime() + 1)
  return { since: since.toISOString(), until: until.toISOString() }
}

/** camelCase (the Java strategy's vocabulary) -> snake_case (the loader's). */
function toSnake(key: string): string {
  return key.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`)
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
  /** Score after tax: percent on each realized gain, the benchmarks alike (G20). */
  taxRate?: number
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
  /** On a fresh run of a model strategy (R82): true when the tested window
   *  overlaps the model's training window, null when that window is unknown.
   *  Absent on a stored result's `result`, whose row carries `inSample`. */
  inSample?: boolean | null
  /** The sentence naming both windows, as the CLI prints it. */
  inSampleNote?: string
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

// ---------------------------------------------------------------------------
// Feature table
// ---------------------------------------------------------------------------

export type FeatureColumn = {
  name: string
  method: string
  params: Record<string, number | string>
  enabled: boolean
  /** Why a disabled column is disabled: blocked, later-paid, undecided, … */
  status: string | null
  note: string | null
  /** Values contributed per bar. A cyclical column emits sin and cos, so 2. */
  width: number
  isDerived: boolean
  series: Record<string, unknown>
}

/**
 * A computed column checked against the vendor's own version of the same
 * number — a `[[check]]` block in features.toml.
 *
 * backTesting-02-Features.md 23: the yield curve is computed from DGS10 - DGS2 and FRED's
 * published T10Y2Y is imported "only as a cross-check: any day where computed
 * and published values differ is flagged as a possible data problem". Reading
 * the published spread straight gave one number nobody could verify.
 */
export type FeatureCheck = {
  name: string
  /** The column whose raw series is the computed side. */
  feature: string
  published: { symbol: string, source: string, field: string }
  /** How far apart the two may be, in the series' own units. */
  tolerance: number
  /** Leading days skipped, for a statistic whose start depends on where the
   *  series began (MACD, RSI). Zero for a plain subtraction. */
  warmupDays: number
  note: string | null
  /**
   * Whether the check actually runs. A check on a disabled column is skipped,
   * and the UI must not mark that column as cross-checked — that would claim
   * verification which never happens.
   */
  live: boolean
}

export type FeatureConfig = {
  schemaVersion: number
  defaults: Record<string, number>
  alignment: {
    calendarSymbol: string
    calendarSource: string
    calendarTimeframe: string
    fill: string
    maxFillDays: number
  }
  inputWidth: number
  lookbackWindow: number | null
  columns: FeatureColumn[]
  requiredSeries: { symbol: string, source: string }[]
  /** Optional: absent from an older loader that predates the checks. */
  checks?: FeatureCheck[]
}

export type FeatureSetColumn = {
  name: string
  method: string
  params: Record<string, number | string>
  columnIndex: number
  /** `input` feeds the network; `data` is stored beside it and never does. */
  role: 'input' | 'data'
}

export type FeatureSet = {
  id: string
  symbol: string
  sourceName: string
  timeframe: string
  schemaVersion: number
  configHash: string
  fillMode: string
  maxFillDays: number
  firstCompleteDate: string | null
  rowCount: number
  builtAt: string
  columnCount: number
  valueCount: number
  /** Expected at the warm-up edge of a rolling window, not a fault by itself. */
  nullCount: number
  fromDate: string | null
  toDate: string | null
  columns: FeatureSetColumn[]
}

export type FeatureColumnStats = {
  name: string
  n: number
  nulls: number
  /** NaN or infinite values: counted apart, and left out of min, max, mean and stdev. */
  nonFinite?: number
  /** Values other than 0 and 1, so a flag column can be checked for them. */
  notBinary?: number
  min: number | null
  max: number | null
  mean: number | null
  stdev: number | null
}

/**
 * One stored column before and after its method, date by date
 * (`GET /api/nn/features/preview`): the Normalization tab's preview. `before`
 * is the series the method was given, as the set recorded it; `after` is the
 * stored value, null on a warm-up day.
 */
export type FeaturePreview = {
  setId: number
  column: string
  method: string
  params: Record<string, unknown>
  series: Record<string, unknown>
  role: string
  from: string
  to: string
  /** False when today's features.toml is not the config the set was built from. */
  configMatches: boolean
  points: { date: string, before: number | null, after: number | null }[]
}

/** One saved window of a strategy and the matrix rows inside it (G37). */
export type SplitBlock = {
  block: 'train' | 'validation' | 'test' | 'prediction'
  /** The window as saved. */
  from: string
  to: string
  /** Rows of the strategy's set inside it, after its columns' gaps drop bars. */
  rows: number
  firstRow: string | null
  lastRow: string | null
}

/** One walk-forward fold as a run makes it: expanding train, purged, then one year. */
export type SplitFold = {
  fold: number
  valYear: number
  trainFrom: string
  trainTo: string
  trainRows: number
  valFrom: string
  valTo: string
  valRows: number
  /**
   * With early stopping, the network fits all but the last training year,
   * which decides when it stops; the tree engines train on every row. Null
   * without it.
   */
  earlyStopping: {
    fitFrom: string, fitTo: string, fitRows: number,
    stopFrom: string, stopTo: string, stopRows: number,
  } | null
}

/** A saved strategy's split, from the runner's own code (`nn/split.py`). */
export type StrategySplit = {
  strategy: {
    id: number, name: string, version: number, instrument: string, source: string,
    timeframe: string,
  }
  setId: number
  columns: number
  dropped: [string, string][]
  rows: number
  firstRow: string | null
  lastRow: string | null
  lookback: number
  /** Training bars dropped before each fold's scored year (the label horizon). */
  purgeBars: number
  /** No fold reads past this day (G38). */
  windowEnd: string | null
  /** How each fold's training is cut (C6): expanding, or sliding with its years. */
  window: { kind: 'expanding' | 'sliding', trainYears: number | null, stepYears: number }
  /** The network's early-stopping patience, or null when it trains a fixed number of epochs. */
  patience: number | null
  blocks: SplitBlock[]
  folds: SplitFold[]
}

/** The split, or why the strategy cannot run as saved. */
export type StrategySplitAnswer =
  | { ok: true, split: StrategySplit }
  | { ok: false, error: string, reasons: string[] }

export type FeaturesReport = {
  config: FeatureConfig | null
  /** Set when features.toml could not be read — the sets still render. */
  configError: string | null
  /** Echoed back so a stale response is recognisable. */
  timeframe: string | null
  newestSetId: string | null
  /** Per-column distribution of the newest set — the normalization gate. */
  columnStats: FeatureColumnStats[]
  sets: FeatureSet[]
}

/**
 * One slot of nn-feedforward's per-bar vector, from GET /api/nn/features/schema.
 *
 * The other NN strategy reads the stored feature table and is described by
 * `FeatureConfig` above. These 12 are computed from a single instrument's
 * candles at train time and exist nowhere in the database, which is why they
 * need an endpoint of their own — and why the formulas live in Python
 * (`nn/features.py`) rather than being restated here.
 */
export type BarFeature = {
  index: number
  name: string
  formula: string
  note: string
}

export type FeaturesSchema = {
  strategy: string
  featuresPerBar: number
  schemaVersion: number
  /** Bars consumed before the first complete vector: longest indicator + lookback. */
  minBars: number
  indicatorPeriods: Record<string, number>
  columns: BarFeature[]
}

/** A bound the preset form renders and the API enforces. */
export type ParamSpec = {
  kind: 'int' | 'float'
  min: number
  max: number
  label: string
}

/**
 * What the model is trained to want and what a run is charged. Unlike
 * `hyperparams`, never partial: the API fills every default on save, and a row
 * saved before this existed reads back as the defaults with
 * `objectiveDefaulted` set.
 */
export type StrategyObjective = {
  loss: 'ce' | 'return' | 'sharpe' | 'calmar'
  classWeights: boolean
  /** Only for a money-trained loss; null for ce. */
  positionThreshold: number | null
  verdict: 'return' | 'sharpe' | 'drawdown'
  costBps: number
  park: 'cash' | 'bills' | 'bonds'
  /**
   * Score after tax: realized gains taxed at `taxRatePct`, buy-and-hold on the
   * same rule. Scoring only -- the model and its signals are unchanged, so a
   * strategy published as a signal can still show a taxable account's view.
   */
  taxScore: boolean
  /** Percent on realized gains; null unless `taxScore`. */
  taxRatePct: number | null
  /**
   * How the signals are acted on (Design Notes, Trading Strategies). 'signals'
   * is Strategy 1, long unless SELL. 'margin' is Strategy 2: a QQQ core that is
   * never sold, margin QQQ on an up signal, margin PSQ on a down one.
   */
  execution: 'signals' | 'margin'
  /** Strategy 2: margin QQQ opened on an up signal, % of the core. Null under Strategy 1. */
  leveragePct: number | null
  /** Strategy 2: margin PSQ opened on a down signal, % of the core. */
  hedgePct: number | null
  /** Strategy 2: points over Fed funds the margin rate charges. */
  marginSpreadPct: number | null
  /** Strategy 2: equity / assets below which the margin position is force-closed. */
  maintenancePct: number | null
  /** Strategy 2, money-trained loss: position needed to go long on margin; null for ce. */
  longThreshold: number | null
}

/** The rules the form draws the objective section from, served by the API. */
export type ObjectiveSpec = {
  defaults: StrategyObjective
  losses: StrategyObjective['loss'][]
  verdicts: StrategyObjective['verdict'][]
  parks: StrategyObjective['park'][]
  maxCostBps: number
  maxTaxRatePct: number
  defaultPositionThreshold: number
  executions: StrategyObjective['execution'][]
  marginDefaults: { leveragePct: number, hedgePct: number, marginSpreadPct: number, maintenancePct: number }
  defaultLongThreshold: number
  /** The design's hard rule: a margin position never exceeds this % of the holding. */
  marginCeilingPct: number
}

/** One setting of an engine, as the builder shows it and the API checks it (B9). */
export type EngineSetting = {
  key: string
  kind: 'int' | 'float'
  default: number
  min: number
  max: number
  label: string
}

/**
 * One engine the runner registers, with its catalogue entry (B9, R22, R74).
 *
 * Served from the loader's one registry (`nn/engines.py`) through
 * GET /api/strategies/saved, so the builder offers exactly what the runner can
 * run and nothing the client lists itself. The network (`mlp`) has no settings
 * of its own here: its settings are the strategy's hyperparameters.
 */
export type EngineEntry = {
  name: 'mlp' | 'xgboost' | 'lightgbm' | string
  title: string
  kind: 'engine'
  library: string
  /** The installed library's version; null when the loader cannot import it. */
  version: string | null
  description: string
  /** What checks this engine's results, in plain words. */
  checkedBy: string
  /** True when the engine fits a scaler on its training rows (R68); trees get none. */
  scaled: boolean
  /** The losses it can train on. A tree engine trains on the class label only. */
  losses: string[]
  settings: EngineSetting[]
}

/**
 * A strategy version's engines by name (B9): switched on or off, with its
 * settings. Absent from a request, the API stores the network alone.
 */
export type StrategyEngines = Record<string, { enabled: boolean, settings: Record<string, number> }>

/** An inclusive date range, YYYY-MM-DD at both ends. */
export type DateWindow = { from: string, to: string }

/**
 * A walk-forward run started from the web: the runner process, not the run.
 * The run itself is `walkforward_runs` (`runId` once the runner opens it); a
 * job that never gets one was refused or crashed, and `output` says which.
 */
export type WalkforwardJob = {
  id: number
  strategyId: number
  label: string
  command: string
  startedAt: string
  finishedAt: string | null
  running: boolean
  exitCode: number | null
  runId: number | null
  /** The runner's last 40 lines. */
  output: string[]
}

export type WalkforwardJobs = { items: WalkforwardJob[], running: number | null }

/**
 * What a label kind predicts (G49, R71): BUY/HOLD/SELL against ± return
 * thresholds, the probability the close is up, or the return itself. Each has
 * its own naive baseline (R72) and its own score: accuracy, hit rate, or the
 * mean absolute error of a return forecast, which is better LOWER.
 */
export type LabelTarget = 'class' | 'direction' | 'return'

/** One threshold a label kind carries, with the bounds the API enforces. */
export type LabelParamSpec = { min: number, max: number, default: number, label: string }

/**
 * One registered label kind (G49). The 3-class kind has no params: its ±
 * thresholds are the strategy's `buyThreshold` / `sellThreshold`
 * hyperparameters. A direction's thresholds are probabilities of up; a
 * return's `minReturn` is the forecast a call must clear.
 */
export type LabelKindSpec = { target: LabelTarget, params: Record<string, LabelParamSpec> }

/**
 * A strategy's label as saved (G49): the kind, the bars ahead it looks, and
 * the kind's own thresholds. `buyThreshold` / `sellThreshold` here are P(up)
 * for a direction label, never the 3-class label's return thresholds.
 */
export type StrategyLabel = {
  kind: string
  horizonBars: number
  buyThreshold?: number
  sellThreshold?: number
  minReturn?: number
}

/**
 * A saved strategy: every choice a training run makes, named and versioned
 * (backTesting-04-Architecture.md §8.2a).
 *
 * `version` matters. Saving never overwrites — an edit stores a new version —
 * so a run can keep pointing at exactly the configuration it was trained from.
 * Absent hyperparameter keys mean "use the model default", which is why the
 * record is a set of deliberate choices rather than a full parameter dump.
 */
export type SavedStrategy = {
  id: string
  name: string
  version: number
  instrument: string
  source: string
  timeframe: string
  modelType: string
  validationScheme: 'single' | 'kfold_time' | 'walkforward'
  /** Ordered feature names, as picked from the catalogue. */
  features: string[]
  windows: Partial<Record<'train' | 'validation' | 'test' | 'prediction', DateWindow>>
    /** How each fold's training is cut (C6); absent is the expanding window. */
    & { window?: { kind: 'sliding', trainYears: number, stepYears: number } }
  /** What the model predicts (G49); null on a row saved before labels were recorded. */
  label: StrategyLabel | null
  hyperparams: Record<string, number>
  objective: StrategyObjective
  /** True for a row saved before `objective` existed: its values are the defaults. */
  objectiveDefaulted: boolean
  /** Each registered engine, on or off, with its settings (B9). */
  engines?: StrategyEngines
  /** True for a version saved before engines existed: `engines` is the network alone. */
  enginesDefaulted?: boolean
  notes: string | null
  createdAt: string
  createdBy: string
  updatedAt: string
  updatedBy: string
}

/** What the builder needs to render itself, alongside the saved strategies. */
export type SavedStrategiesResponse = {
  items: SavedStrategy[]
  /** The editable fields and bounds, so the form matches what the API enforces. */
  paramSpec: Record<string, ParamSpec>
  objectiveSpec: ObjectiveSpec
  /**
   * The registered label kinds in the registry's order, each with its target
   * and the thresholds that turn its prediction into calls (G49, R71). Absent
   * from an API older than G49: the form then offers the 3-class label alone.
   */
  labelKinds?: Record<string, LabelKindSpec>
  /** Each target's naive baseline in words (R72), e.g. `direction: 'always up'`. */
  naiveBaselines?: Partial<Record<LabelTarget, string>>
  /**
   * §8.2a: populated from what the model layer registers, not hard-coded. With
   * `validationSchemes` and `unrunnableTimeframes` it is the runner's own list
   * (G54), read from the loader; null when the loader did not answer, with the
   * reason in `runnerError`. A save is refused then, so the form guesses nothing.
   */
  modelTypes: string[] | null
  /** Timeframes no run can use (weekly and monthly calendars are never built). */
  unrunnableTimeframes?: string[] | null
  runnerError?: string | null
  /**
   * The engines the runner registers, in its order (B9, R22); null when the
   * loader did not answer, with the reason in `enginesError`.
   */
  engines?: EngineEntry[] | null
  enginesError?: string | null
  validationSchemes: string[] | null
  windows: string[]
  requiredWindows: string[]
}

/** The body POST /api/strategies/saved accepts. */
export type SaveStrategyRequest = {
  name: string
  instrument: string
  source: string
  timeframe: string
  modelType: string
  validationScheme: string
  features: string[]
  windows: Partial<Record<'train' | 'validation' | 'test' | 'prediction', DateWindow>>
    & { window?: { kind: 'sliding', trainYears: number, stepYears: number } }
  /** The kind, its horizon and its own thresholds; a missing threshold takes its default. */
  label: StrategyLabel
  hyperparams: Record<string, string | number>
  objective: StrategyObjective
  /** B9: each engine on or off with its settings. Absent: the network alone. */
  engines?: StrategyEngines
  notes?: string | null
}

/**
 * One row of the feature catalogue (backTesting-04-Architecture.md §8.1a/§8.2a).
 *
 * A projection of `features.toml` into `feature_definitions`, plus the two
 * fields that are facts about stored data rather than config: `rangeStart` and
 * `rangeEnd`, which are null until the column has been built once.
 */
export type CatalogueFeature = {
  name: string
  /** Provider name for a stored series, or 'computed' for a derived one. */
  source: string
  cadence: 'daily' | 'weekly' | 'monthly' | 'quarterly'
  rangeStart: string | null
  rangeEnd: string | null
  /** `instrument` columns follow the chosen instrument; `market` ones are shared. */
  scope: 'instrument' | 'market'
  /** `active` columns are offered ticked in the strategy builder, `selectable`
   *  ones unticked. `data-only` columns are stored for charts and rules, never
   *  a network input (none today). */
  status: 'active' | 'selectable' | 'data-only' | 'undecided' | 'blocked'
  cost: 'free' | 'paid'
  /** Of the RAW series. Drives normalization and the chart axis. */
  units: 'percent' | 'level' | 'count' | 'ratio'
  /** Fingerprint of how it is computed: method, params and series. */
  codeVersion: string
  method: string
  enabled: boolean
  note: string | null
  /** Values per bar. A cyclical column emits sin and cos, so 2. */
  width: number
  createdAt: string
  createdBy: string
  updatedAt: string
  updatedBy: string
}

/** A built feature set, which is what coverage is measured against. */
export type CatalogueSet = {
  id: string
  symbol: string
  sourceName: string
  timeframe: string
  rowCount: number
  builtAt: string
  firstCompleteDate: string | null
}

export type FeatureCatalogue = {
  items: CatalogueFeature[]
  sets: CatalogueSet[]
}

/**
 * How much of a window one feature actually covers.
 *
 * The three counts are deliberately separate. `withValue` has a number;
 * `storedNull` has a row that says "not computable yet" (a rolling window not
 * full); `absent` has no row at all. Collapsing them would hide the difference
 * between a short history and a feature that was never built.
 */
export type FeatureCoverage = {
  name: string
  status: string
  scope: string
  units: string
  bars: number
  withValue: number
  storedNull: number
  absent: number
  /** withValue / bars, or null when the window holds no bars. */
  share: number | null
  firstValue: string | null
  lastValue: string | null
}

export type CoverageReport = {
  setId: string
  /** Bars the SET has in the window — trading days, not calendar days. */
  bars: number
  windowFrom: string | null
  windowTo: string | null
  items: FeatureCoverage[]
}

export type FeatureGaps = {
  featureName: string
  totalGaps: number
  missingBars: number
  /** The longest gaps, capped server-side. */
  items: { from: string, to: string, bars: number }[]
  truncated: boolean
}

/**
 * One validation fold of a walk-forward run, from GET /api/walkforward/runs/:id.
 *
 * Both sides of every comparison are carried, not just the difference: "+5"
 * hides whether the model made 12 against a 7 or 2 against a -3, and those are
 * different claims about the same number.
 *
 * `driftTax`, `commissions` and `timing` split the gap to buy-and-hold into the
 * price of being out of the market at all, the mechanical cost of trading, and
 * the model's own contribution. **Only `timing` is the model.** A negative
 * `timing` means the days it sat out were better than average — it picked
 * wrong. A fold can lose heavily with `timing` near zero, which is the
 * difference between "the model lost 20 points" and "the model is absent and
 * the structure lost 20 points".
 */
export type WalkforwardFold = {
  runId: number
  fold: number
  /**
   * The engine this row scores (B9): a fold has one row per engine the run
   * trained, all on the same rows. Absent on data read before B9.
   */
  engine?: string
  valYear: number
  /** ISO dates. */
  trainFrom: string
  trainTo: string
  nTrain: number
  nVal: number
  stratReturn: number
  holdReturn: number
  stratSharpe: number
  holdSharpe: number
  stratMaxdd: number
  holdMaxdd: number
  trades: number
  /** Null for a money-trained model, which has no class to be right about. */
  accuracy: number | null
  baselineAcc: number | null
  daysOut: number
  driftTax: number
  commissions: number
  timing: number
  /** Tax on realized gains, points of starting capital; 0 when scored before tax. */
  gainsTax: number
  /** The epoch early stopping kept; null when the run trained fixed epochs. */
  bestEpoch: number | null
  /** Epochs actually trained before stopping; null likewise. */
  epochsRun: number | null
  /** Buy-and-hold's own commission and tax, scored with the fold under the same settings (B6). */
  holdCommissions: number
  holdGainsTax: number
  /** Faber and dual momentum over the fold (B7). */
  benchmarks?: FoldBenchmark[]
  /** Strategy 2: interest charged, negative points; this and the five below are null under Strategy 1. */
  marginInterest: number | null
  marginCalls: number | null
  /** Cuts back to the 50% ceiling. */
  trims: number | null
  daysLong: number | null
  daysHedged: number | null
  /** Bars below maintenance with nothing left on margin: the core itself at risk. */
  coreAtRisk: number | null
  /** G25: the same fold always long on margin (no model), percent. Null under Strategy 1. */
  leveredReturn: number | null
  /**
   * The engine's training loss over the fold, from its recorded epochs (G11);
   * null for a run from before epochs were recorded.
   */
  loss?: FoldLoss | null
}

/** One engine's training loss over a fold: decreased when the last is below the first. */
export type FoldLoss = {
  epochs: number
  first: number
  lowest: number
  last: number
  decreased: boolean
}

/** One engine's loss curve over a fold, epoch by epoch (a tree engine's rounds). */
export type EpochCurve = {
  fold: number
  engine: string
  train: number[]
  /** The early-stopping slice's loss; null without early stopping. */
  stop: (number | null)[] | null
}

/** Where a run that is still going has got to (G11). */
export type RunProgress = {
  fold: number | null
  engine: string | null
  epoch: number | null
  trainLoss: number | null
  recordedAt: string | null
  /** Fold rows finished, one per engine per fold, of those planned. */
  unitsDone: number
  unitsPlanned: number
  elapsedSeconds: number
  /** From the pace so far; null before the first fold row lands. */
  etaSeconds: number | null
}

/**
 * How a run acted on its signals (G5), with Strategy 2's dials and its margin
 * figures summed or averaged over the folds. Dials and figures are null under
 * Strategy 1.
 */
export type WalkforwardExecution = {
  kind: 'signals' | 'margin'
  leveragePct: number | null
  hedgePct: number | null
  marginSpreadPct: number | null
  maintenancePct: number | null
  longThreshold: number | null
  meanMarginInterest: number | null
  marginCalls: number | null
  trims: number | null
  coreAtRisk: number | null
  meanDaysLong: number | null
  meanDaysHedged: number | null
  /** G25: mean fold return always long on margin, no model; null before G25 or under Strategy 1. */
  meanLeveredReturn: number | null
  /** Folds where the model beat always-long-on-margin; null under Strategy 1. */
  foldsBeatLevered: number | null
}

/**
 * A walk-forward run: the configuration, plus its folds summarised.
 *
 * The summary half is aggregated by the API on read from `walkforward_folds`,
 * never stored — a stored copy would go stale the moment a fold was corrected.
 * Every average is **null when `folds` is 0**: a run still executing, or one
 * that died before its first fold. Zero would read as "the model broke even"
 * when nothing has been measured.
 */
export type WalkforwardRun = {
  id: number
  /**
   * The run's name, or (G49) its parsed label. The API writes both under this
   * one key and the label is written last, so a run read since G49 carries the
   * label object (null for a run from before) and its name is lost. Read it
   * through `runName` / `runLabel` in `lib/targets.ts`, which take either.
   */
  label: string
  /** The strategy's label with its horizon and thresholds (G49); null before G49. */
  labelSpec?: StrategyLabel | null
  instrumentId: number
  symbol: string
  sourceId: number
  sourceName: string
  timeframe: string
  featureSetId: number | null
  /** Network inputs: columns × lookback, not values per bar. */
  inputWidth: number
  objective: 'return' | 'sharpe' | 'drawdown' | string
  /** What the network trained on; differs from `objective` on purpose. */
  loss: string
  costBps: number
  park: string
  /** Null for a classifier — there is no position to cut. */
  threshold: number | null
  /** Percent on realized gains when scored after tax; null when scored before tax. */
  taxRate: number | null
  /**
   * The run whose stored signals this one was scored from, without retraining
   * (B10, `python -m nn.recalculate`); null on a run that trained.
   */
  recalculatedFrom: number | null
  /**
   * Bars dropped from the end of each training window because their targets
   * look into the scored window. Null on a run from before the purge, whose
   * folds trained on them and so read slightly optimistic.
   */
  purgeBars: number | null
  trainStart: number
  firstVal: number
  lastVal: number
  /** True when the run spent the holdout. A one-way door. */
  isTest: boolean
  /** The training window the run used (C6). Absent from an engine older than it. */
  window?: { kind: 'expanding' | 'sliding', trainYears: number | null, stepYears: number }
  /** The input columns in order; null on a run older than the record, which read the whole set. */
  columns: string[] | null
  /**
   * Each engine the run trained, in the run's order (B9). Null on a run from
   * before, which trained the network alone. The run's own figures below are
   * its FIRST engine's; `engineSummaries` on the detail has every engine's.
   */
  engines?: EngineRecord[] | null
  /** The saved strategy the run executed; null for a run launched from flags. */
  strategy: WalkforwardStrategyRef | null
  /** What produced the run's inputs (G3); null on a run from before it. */
  lineage: WalkforwardLineage | null
  /** Strategy 1 or 2, and Strategy 2's dials and margin figures (G5). */
  execution: WalkforwardExecution
  /**
   * G25: did a Strategy 2 run's mean return beat always long on margin? Null
   * under Strategy 1 or with no baseline. False beside a win over buy-and-hold
   * means the win is leverage, not the model.
   */
  beatsLeveredOnReturn?: boolean | null
  startedAt: string
  finishedAt: string | null
  inProgress: boolean
  /** Why the run stopped short: a NaN or infinite loss (G11); null otherwise. */
  failedReason?: string | null
  /** Set while the run is going (G11). */
  progress?: RunProgress
  folds: number
  foldsWon: number
  meanExcess: number | null
  meanStratReturn: number | null
  meanHoldReturn: number | null
  meanStratSharpe: number | null
  meanHoldSharpe: number | null
  worstStratDrawdown: number | null
  worstHoldDrawdown: number | null
  totalTrades: number
  meanDaysOut: number | null
  meanDriftTax: number | null
  meanCommissions: number | null
  /** Mean tax on realized gains per fold; 0 when scored before tax. */
  meanGainsTax: number | null
  meanTiming: number | null
  /**
   * What the run predicted (G49): 'class' for a run from before. For 'return'
   * every accuracy figure below and on its folds is a mean absolute error of
   * a return forecast, lower better; for 'direction' a hit rate.
   */
  target?: LabelTarget | string
  /** Mean fold accuracy on the decision the model makes (G43); null with no folds. */
  meanAccuracy?: number | null
  /** Mean fold accuracy of the naive predictor (R72, B8). */
  meanBaselineAcc?: number | null
  /** False when the model does not beat the naive predictor: the run is flagged. */
  beatsNaive?: boolean | null
  /** Null until a fold exists. Only decided for the `return` objective. */
  beatsHoldOnReturn: boolean | null
  /** Parsed by the API; the seed lives here, which §6.1 needs for reproducibility. */
  hyperparams: Record<string, number | boolean | string> | null
  /** Present only when the stored text would not parse. */
  hyperparamsRaw?: string
}

/**
 * Where a strategy run came from and what its training window really held
 * (§6.1, §6.2). Name and version are stored beside the id because a strategy
 * can be deleted and the run must still say what it ran.
 */
export type WalkforwardStrategyRef = {
  id: number
  name: string
  version: number | null
  /** Selected columns left out, and why. */
  dropped: { name: string, why: string }[] | null
  /** Kept columns with holes: `values` of `bars` train bars present. */
  partial: { name: string, values: number, bars: number }[] | null
  /** First train bar the matrix held; later than asked when a partial column starts late. */
  trainFirst: string | null
  trainBarsUsed: number | null
  trainBars: number | null
  /** The strategy's test.to; set only on a holdout run. */
  testEnd: string | null
}

/**
 * A run's input snapshot and code version (Architecture §1, §6.1; G3).
 * `setRebuiltSince` / `valuesChangedSince` come from the stored builds; null
 * when the run or the set has no build to compare.
 */
export type WalkforwardLineage = {
  featureRunId: number | null
  currentBuildId: number | null
  inputSnapshot: string
  parts: { matrix?: string, closes?: string, park?: string } | null
  codeHash: string | null
  gitSha: string | null
  libVersions: Record<string, string> | null
  setRebuiltSince: boolean | null
  valuesChangedSince: boolean | null
}

export type WalkforwardRuns = {
  items: WalkforwardRun[]
  /** Echoed back: an empty list means something different filtered than not. */
  timeframe: string | null
  includeTest: boolean
  limit: number
}

/**
 * What a run records about one engine it trained (B9): the library and its
 * version, the seed, and the settings used. The network's settings are the
 * run's hyperparameters.
 */
export type EngineRecord = {
  engine: string
  library: string
  version: string | null
  seed: number | null
  /** False for a tree engine: it took the raw rows, no scaler (R68). */
  scaled: boolean
  settings: Record<string, unknown>
}

/**
 * One engine's figures over a run's folds (B9), computed on read like the
 * run's own. Fields mirror the run's summary; averages are null with no folds.
 */
export type EngineSummary = {
  engine: string
  folds: number
  foldsWon: number
  meanExcess: number | null
  meanStratReturn: number | null
  meanHoldReturn: number | null
  meanStratSharpe: number | null
  worstStratDrawdown: number | null
  totalTrades: number
  meanTiming: number | null
  meanAccuracy: number | null
  meanBaselineAcc: number | null
  beatsNaive: boolean | null
  beatsHoldOnReturn: boolean | null
}

export type WalkforwardRunDetail = {
  run: WalkforwardRun
  /** One row per engine per fold, by fold, then in the run's engine order. */
  folds: WalkforwardFold[]
  /** One line per engine, in the run's order (B9); absent before B9. */
  engineSummaries?: EngineSummary[]
  /** Every engine's loss curve, fold by fold (G11); empty before epochs were recorded. */
  epochs?: EpochCurve[]
}

// ---------------------------------------------------------------------------
// Data feeds (G26): series refreshed straight from their providers
// ---------------------------------------------------------------------------

/** Stored days a provider now reports differently; the series waits for Apply. */
export type FeedHeld = {
  days: number
  from: string
  to: string
  /** Stored days the provider no longer has at all. */
  dropped: number
  /** Largest relative difference among them (1 for a dropped day). */
  largest: number
  /** Set when every change is one rescale of all prices: a dividend re-adjustment or a split. */
  factor: number | null
  /** New days the provider has that the held series is not taking. */
  waiting: number
  examples: { date: string, old: number[], new: number[] | null }[]
}

/** One series' outcome in one feeds run. */
export type FeedResult = {
  feed: string
  status: 'extended' | 'fixed' | 'extended+fixed' | 'up-to-date' | 'held' | 'loaded' | 'applied' | 'error'
  added?: number
  /** The last stored day, when its bar settled and was replaced. */
  fixed?: string | null
  /** Every settled day, when more than one did: gold, copper and the dollar
   *  index may also settle the volume of the day before the last. */
  fixedDays?: string[]
  held?: FeedHeld
  lastBefore?: string
  lastAfter?: string
  error?: string
  version?: { id: number, changeKind: string }
  runId?: number
  at?: string | null
}

export type FeedRow = {
  key: string
  symbol: string
  source: string
  type: string
  provider: 'yahoo' | 'yahoo-adj' | 'fred' | 'boc' | 'breadth' | 'sec'
  ref: string
  group: 'prices' | 'macro' | 'breadth' | 'earnings'
  /** Trades into the evening, so taken up to the previous day only. */
  overnight?: boolean
  lastDate: string | null
  /** The newest non-dry-run result for this series, if any run has touched it. */
  result: FeedResult | null
}

export type FeatureRebuild = {
  ok: boolean
  rows?: number
  first?: string
  last?: string
  setId?: number
  columns?: number
  firstComplete?: string
  buildId?: number
  checks?: string[]
  error?: string
}

export type FeedRunSummary = {
  id: number
  trigger: 'schedule' | 'button' | 'apply' | 'cli'
  status: 'running' | 'done' | 'done-with-errors' | 'failed' | 'interrupted'
  feeds: string[] | null
  applyFeed: string | null
  startedAt: string | null
  finishedAt: string | null
  error: string | null
  counts: Record<string, number>
  rebuild: FeatureRebuild | null
}

export type FeedRunDetail = FeedRunSummary & {
  result: {
    feeds: FeedResult[]
    members: { symbol: string, status: string, note: string }[]
    /** The SEC earnings refresh: the session every company's filings are complete
     *  for, and what each company's refresh did. Absent on runs before it was fed. */
    sec?: { through: string | null, companies: number, unmapped: string[],
      results: { cik: number, tickers: string[], status: string, added: number, note: string }[] } | null
  } | null
}

export type FeedSchedule = {
  enabled: boolean
  times?: string[]
  timezone?: string
  days?: string
  nextRun?: string
  error?: string
}

export type FeedsReport = {
  feeds: FeedRow[]
  manual: { symbol: string, source: string, how: string }[]
  running: boolean
  runs: FeedRunSummary[]
  schedule: FeedSchedule
  warning: string | null
}

// ---------------------------------------------------------------------------
// Pipeline stage states (G12): what is stale, and why
// ---------------------------------------------------------------------------

export type StageReason = {
  kind: string
  text: string
  /** `error` on a check error nobody overrode: it fails the stage (R12, R65). */
  severity?: 'error' | 'warning'
  series?: string
  part?: string
  days?: number
  version?: number
}

export type StageStatus = {
  state: 'passed' | 'failed' | 'stale' | 'empty' | 'unknown' | 'running'
  reasons: StageReason[]
  /** Overrides in use on this stage: said, never a reason to fail. */
  notes?: string[]
}

/** Whether a stored walk-forward run would read the same inputs if rerun now. */
export type RunStaleness = {
  runId: number
  strategy: string | null
  version: number | null
  isTest: boolean
  state: 'fresh' | 'stale' | 'unknown'
  reasons: StageReason[]
  /** Not reasons to rerun: e.g. the code has changed since. */
  notes: string[]
}

export type PipelineStatus = {
  timeframe: string
  checkedAt: string
  data: StageStatus & { cutoff?: string, required?: number }
  features: StageStatus & { setId?: number, buildId?: number | null }
  normalization: StageStatus
  split: StageStatus
  training: StageStatus & { runs: RunStaleness[], inProgress: number }
  backtest: StageStatus & {
    results: { id: number, state: 'fresh' | 'stale', reasons: StageReason[] }[]
    stale: number
  }
}

export const api = {
  health: (signal?: AbortSignal) => getJson<HealthReport>('/api/health', signal),
  sources: (signal?: AbortSignal) =>
    getJson<{ items: Source[] }>('/api/sources', signal),
  instruments: (timeframe?: string, signal?: AbortSignal) =>
    getJson<{ items: Instrument[], timeframe: string | null }>(
      `/api/instruments${timeframe ? `?${qs({ timeframe })}` : ''}`, signal,
    ),
  feeds: (signal?: AbortSignal) => getJson<FeedsReport>('/api/feeds', signal),
  pipelineStatus: (timeframe: string, signal?: AbortSignal) =>
    getJson<PipelineStatus>(`/api/nn/pipeline/status?${qs({ timeframe })}`, signal),
  feedRun: (id: number, signal?: AbortSignal) =>
    getJson<FeedRunDetail>(`/api/feeds/runs/${id}`, signal),
  refreshFeeds: (body: { feeds?: string[], rebuild?: boolean } = {}) =>
    sendJson<{ runId: number }>('/api/feeds/refresh', 'POST', body),
  applyFeed: (feed: string) =>
    sendJson<{ runId: number }>('/api/feeds/apply', 'POST', { feed }),
  imports: (
    params: {
      limit?: number; offset?: number; source?: string; instrument?: string; timeframe?: string;
      sort?: 'imported' | 'source' | 'instrument' | 'timeframe' | 'archive' | 'file' | 'rows';
      dir?: 'asc' | 'desc';
    } = {},
    signal?: AbortSignal,
  ) => getJson<Paginated<ImportRecord>>(`/api/imports?${qs(params)}`, signal),
  /** Version history of input files; `available` is false before the schema exists. */
  importFiles: (
    params: { limit?: number, offset?: number, source?: string, instrument?: string,
      timeframe?: string, kind?: string },
    signal?: AbortSignal,
  ) => getJson<Paginated<FileVersion> & { available: boolean }>(
    `/api/import-files?${qs(params)}`, signal,
  ),
  results: (
    params: {
      limit?: number; offset?: number; strategy?: string; instrument?: string;
      source?: string; timeframe?: string;
      sort?: ResultSortKey; dir?: 'asc' | 'desc';
    } = {},
    signal?: AbortSignal,
  ) => getJson<Paginated<ResultSummary>>(`/api/results?${qs(params)}`, signal),
  result: (id: string, signal?: AbortSignal) =>
    getJson<ResultDetail>(`/api/results/${encodeURIComponent(id)}`, signal),
  candles: (
    params: { instrument: string; source?: string; timeframe: string; from?: string; to?: string },
    signal?: AbortSignal,
  ) => getJson<CandleSeries>(`/api/candles?${qs(params)}`, signal),
  models: (timeframe?: string, signal?: AbortSignal) =>
    getJson<{ items: TrainedModel[], modelsDir: string }>(
      `/api/models${timeframe ? `?${qs({ timeframe })}` : ''}`, signal,
    ),
  strategies: (signal?: AbortSignal) =>
    getJson<{ items: Strategy[] }>('/api/strategies', signal),
  /**
   * Walk-forward runs, newest first. Served by Java and proxied through this
   * origin.
   *
   * `includeTest` defaults to false server-side and is left off here on
   * purpose: the design allows the holdout to be spent once, to confirm a
   * variant already chosen, and listing those runs beside the validation ones
   * is how it quietly becomes training data. Asking for them is deliberate.
   */
  walkforwardRuns: (
    params: { timeframe?: string, includeTest?: boolean, limit?: number } = {},
    signal?: AbortSignal,
  ) => getJson<WalkforwardRuns>(`/api/walkforward/runs?${qs(params)}`, signal),
  /**
   * A saved strategy's split as a walk-forward run makes it (G37). A strategy
   * that cannot run answers with every reason rather than an exception.
   */
  walkforwardSplit: async (strategyId: string, signal?: AbortSignal): Promise<StrategySplitAnswer> => {
    const res = await fetch(apiUrl(`/api/nn/walkforward/split?${qs({ strategyId })}`), { signal })
    const body = await res.json().catch(() => ({}))
    if (res.ok) return { ok: true, split: body as StrategySplit }
    return {
      ok: false,
      error: body?.error ?? `${res.status} ${res.statusText}`,
      reasons: Array.isArray(body?.reasons) ? body.reasons : [],
    }
  },
  /** One run with every fold in sequence. */
  walkforwardRun: (id: number, signal?: AbortSignal) =>
    getJson<WalkforwardRunDetail>(
      `/api/walkforward/runs/${encodeURIComponent(String(id))}`, signal,
    ),
  /**
   * Starts `python -m nn.walkforward --strategy-id N` on the loader. Resolves
   * to the job, or to `refused` with every reason the strategy cannot run as
   * saved: a refusal is an answer to show, not an error to throw.
   */
  walkforwardStart: async (
    body: { strategyId: number, label?: string },
  ): Promise<{ job: WalkforwardJob } | { refused: string, reasons: string[] }> => {
    const res = await fetch(apiUrl('/api/nn/walkforward'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    let parsed: { error?: string, reasons?: string[] } & Partial<WalkforwardJob> = {}
    try { parsed = await res.json() } catch { /* not JSON */ }
    if (res.ok) return { job: parsed as WalkforwardJob }
    if (res.status === 400 && Array.isArray(parsed.reasons)) {
      return { refused: parsed.error ?? 'refused', reasons: parsed.reasons }
    }
    throw new Error(parsed.error ?? `${res.status} ${res.statusText}`)
  },
  /** Jobs the loader has started since it came up, newest first. */
  walkforwardJobs: (signal?: AbortSignal) =>
    getJson<WalkforwardJobs>('/api/nn/walkforward/jobs', signal),
  features: (timeframe?: string, signal?: AbortSignal) =>
    getJson<FeaturesReport>(`/api/features${timeframe ? `?${qs({ timeframe })}` : ''}`, signal),
  /** One column of a set before and after its method, over the last `days`. */
  featurePreview: (setId: number | string, column: string, days?: number, signal?: AbortSignal) =>
    getJson<FeaturePreview>(`/api/nn/features/preview?${qs({ set: setId, column, days })}`, signal),
  /** The feature catalogue and the sets coverage can be measured against. */
  featureCatalogue: (signal?: AbortSignal) =>
    getJson<FeatureCatalogue>('/api/features/catalogue', signal),
  /** Per-feature coverage of one set over a date window. */
  featureCoverage: (
    params: { set: string, from?: string, to?: string }, signal?: AbortSignal,
  ) => getJson<CoverageReport>(`/api/features/coverage?${qs(params)}`, signal),
  /** Where one feature has no value, as date ranges. */
  featureGaps: (
    params: { set: string, feature: string, from?: string, to?: string },
    signal?: AbortSignal,
  ) => getJson<FeatureGaps>(`/api/features/gaps?${qs(params)}`, signal),
  /** The 12 per-bar features nn-feedforward computes. Proxied to the loader. */
  featuresSchema: (signal?: AbortSignal) =>
    getJson<FeaturesSchema>('/api/nn/features/schema', signal),
  /**
   * Saved strategies. `latest` collapses to the newest version per name, which
   * is what the builder's list shows; without it every version comes back,
   * newest first, for the history of one name.
   */
  savedStrategies: (
    opts: { name?: string, latest?: boolean } = {},
    signal?: AbortSignal,
  ) => {
    const q = qs({
      ...(opts.name ? { name: opts.name } : {}),
      ...(opts.latest ? { latest: '1' } : {}),
    })
    return getJson<SavedStrategiesResponse>(
      `/api/strategies/saved${q ? `?${q}` : ''}`, signal,
    )
  },
  savedStrategy: (id: string, signal?: AbortSignal) =>
    getJson<SavedStrategy>(`/api/strategies/saved/${id}`, signal),
  /**
   * Saves a new version. Never replaces one: §8.2a requires that editing a
   * saved strategy create a new version so every run still points at exactly
   * what it was trained from. There is deliberately no update or delete here.
   */
  saveStrategy: (s: SaveStrategyRequest) =>
    sendJson<SavedStrategy>('/api/strategies/saved', 'POST', s),
  /**
   * What a save of `s` would answer, storing nothing (G54): the API's own
   * checks, then the runner's (`nn/strategy_spec.py`), every reason at once.
   * Asked while the form is filled, so a refusal is read before Save is pressed.
   */
  checkStrategy: (s: SaveStrategyRequest) =>
    sendJson<{ ok: boolean, errors: string[] }>('/api/strategies/saved?check=1', 'POST', s),
  /**
   * Removes a saved strategy. `all` removes every version of its name.
   *
   * Not in tension with "editing creates a new version, never overwrites": that
   * rule protects a run's provenance, and a version nothing was trained from
   * has none to protect.
   */
  deleteStrategy: (id: string, all = false) =>
    sendJson<{ status: string, name?: string, versionsDeleted: number }>(
      `/api/strategies/saved/${id}${all ? '?all=1' : ''}`, 'DELETE',
    ),
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
  /**
   * Trains a model for a strategy that needs one. Synchronous and slow — tens
   * of seconds for a long daily series — so callers should show progress.
   * Numeric hyperparameters are sent as numbers, since the loader's schema
   * types them and would reject strings.
   */
  train: (req: TrainRequest) => {
    const { since, until } = engineWindow(req.firstCandle, req.lastCandle)
    const body: Record<string, unknown> = {
      symbol: req.symbol, source: req.source, timeframe: req.timeframe,
      since, until, mode: req.mode,
    }
    for (const [k, v] of Object.entries(req.parameters)) {
      if (v === '' || v === undefined) continue
      const n = Number(v)
      body[toSnake(k)] = Number.isFinite(n) ? n : v
    }
    return sendJson<TrainResponse>('/api/nn/train', 'POST', body)
  },
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
  /** Series with flagged rows: bars an import found an error on (G75). */
  checks: (signal?: AbortSignal) => getJson<{ series: FlaggedSeries[], open: number, accepted: number }>('/api/checks', signal),
  /** Accept a series' open flagged rows. The reason is required and recorded with them. */
  acceptChecks: (req: { symbol: string, source: string, timeframe?: string, reason: string }) =>
    sendJson<{ accepted: number, symbol: string, source: string, reason: string }>('/api/checks/accept', 'POST', req),
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
