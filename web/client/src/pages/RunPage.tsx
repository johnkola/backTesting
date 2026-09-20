import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api, beatBuyAndHold, isAbortError, isTradeable, vsBuyAndHold,
  type Instrument, type RunResult, type Strategy,
} from '../lib/api'
import FieldLabel, { tipClass } from '../components/FieldLabel'
import { metricHint } from '../lib/metricHints'

/**
 * Run a backtest. The instrument, source and timeframe options come from what
 * is actually imported (GET /api/instruments), and the strategy list and its
 * parameter defaults from the engine (GET /api/strategies) — so the form can
 * never offer a combination the backend has no data for.
 */
export default function RunPage() {
  const [strategies, setStrategies] = useState<Strategy[] | null>(null)
  const [instruments, setInstruments] = useState<Instrument[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [strategyName, setStrategyName] = useState('')
  const [symbol, setSymbol] = useState('')
  const [source, setSource] = useState('')
  const [timeframe, setTimeframe] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [capital, setCapital] = useState('')
  const [params, setParams] = useState<Record<string, string>>({})
  const [modelVersion, setModelVersion] = useState('')

  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [result, setResult] = useState<RunResult | null>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    Promise.all([api.strategies(ctrl.signal), api.instruments(ctrl.signal)])
      .then(([s, i]) => {
        setStrategies(s.items)
        // Indices are filtered out rather than shown and rejected: they are real
        // data but not holdings, and the engine refuses them. Offering one in a
        // picker labelled "what to trade" is the wrong promise to make.
        setInstruments(i.items.filter(isTradeable))
        if (s.items.length > 0) selectStrategy(s.items[0])
        const first = i.items.filter(isTradeable).find((x) => x.sources.length > 0)
        if (first) {
          setSymbol(first.symbol)
          setSource(first.sources[0].sourceName)
          setTimeframe(first.sources[0].timeframe)
        }
      })
      .catch((e: Error) => { if (!isAbortError(e)) setLoadError(e.message) })
    return () => ctrl.abort()
  }, [])

  const strategy = strategies?.find((s) => s.name === strategyName) ?? null

  /** Switching strategy resets the parameter form to that strategy's defaults. */
  function selectStrategy(s: Strategy) {
    setStrategyName(s.name)
    setParams({ ...s.defaultParameters })
  }

  const instrument = instruments?.find((i) => i.symbol === symbol) ?? null

  const sourcesForSymbol = useMemo(() => {
    if (!instrument) return []
    return [...new Set(instrument.sources.map((s) => s.sourceName))]
  }, [instrument])

  /**
   * Timeframes the chosen (instrument, source) actually holds, plus the rollups
   * `run` can build on the fly — it aggregates a missing higher timeframe from
   * a finer one before starting, so offering those is honest.
   */
  const timeframesForSource = useMemo(() => {
    if (!instrument) return []
    const present = instrument.sources
      .filter((s) => s.sourceName === source)
      .map((s) => s.timeframe)
    const derivable = present.includes('D1') ? ['W1', 'MN1'] : []
    return [...new Set([...present, ...derivable])]
  }, [instrument, source])

  const seriesInfo = instrument?.sources.find(
    (s) => s.sourceName === source && s.timeframe === timeframe,
  )

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setRunning(true)
    setRunError(null)
    setResult(null)
    try {
      const r = await api.run({
        strategy: strategyName,
        instrument: symbol,
        timeframe,
        source,
        from: from || undefined,
        to: to || undefined,
        capital: capital ? Number(capital) : undefined,
        parameters: params,
        modelVersion: modelVersion || undefined,
      })
      setResult(r)
    } catch (err) {
      setRunError((err as Error).message)
    } finally {
      setRunning(false)
    }
  }

  const ready = strategyName && symbol && source && timeframe

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-1">Run a backtest</h1>
      <p className="text-base-content/60 mb-4">
        Runs on the engine and saves the result. Expect a second or so.
      </p>

      {loadError && <div className="alert alert-error mb-4">{loadError}</div>}
      {!strategies && !loadError && <span className="loading loading-spinner" />}

      {strategies && instruments && (
        <form onSubmit={submit} className="space-y-4">
          <div className="bg-base-200 rounded-box p-4 grid gap-4 md:grid-cols-2">
            <label className="form-control">
              <FieldLabel text="Strategy" hint="What decides entries and exits. Switching resets the parameters below to that strategy's defaults." />
              <select
                className="select select-bordered"
                value={strategyName}
                onChange={(e) => {
                  const s = strategies.find((x) => x.name === e.target.value)
                  if (s) selectStrategy(s)
                }}
              >
                {strategies.map((s) => (
                  <option key={s.name} value={s.name}>{s.name} — {s.description}</option>
                ))}
              </select>
              {strategy?.requiresTrainedModel && (
                <div className="label">
                  <span className="label-text-alt text-warning">
                    Needs a trained model. Train it first, or the run returns 409.
                  </span>
                </div>
              )}
            </label>

            <label className="form-control">
              <FieldLabel text="Instrument" hint="The symbol to trade. Only imported symbols are listed; (no candles) means nothing is stored for it yet." />
              <select
                className="select select-bordered"
                value={symbol}
                onChange={(e) => {
                  const next = instruments.find((i) => i.symbol === e.target.value)
                  setSymbol(e.target.value)
                  if (next && next.sources.length > 0) {
                    setSource(next.sources[0].sourceName)
                    setTimeframe(next.sources[0].timeframe)
                  }
                }}
              >
                {instruments.map((i) => (
                  <option key={i.id} value={i.symbol}>
                    {i.symbol}{i.sources.length === 0 ? ' (no candles)' : ''}
                  </option>
                ))}
              </select>
            </label>

            <label className="form-control">
              <FieldLabel text="Source" hint="Which import the candles came from. One symbol can hold parallel histories from several sources without them overwriting each other." />
              <select
                className="select select-bordered"
                value={source}
                onChange={(e) => setSource(e.target.value)}
              >
                {sourcesForSymbol.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </label>

            <label className="form-control">
              <FieldLabel text="Timeframe" hint="Candle size. A D1 series also offers W1 and MN1 — the engine aggregates those from the daily bars before the run starts." />
              <select
                className="select select-bordered"
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
              >
                {timeframesForSource.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
              </select>
              <div className="label">
                <span className="label-text-alt text-base-content/50">
                  {seriesInfo
                    ? `${seriesInfo.candleCount.toLocaleString()} candles, ${seriesInfo.fromDate?.slice(0, 10)} → ${seriesInfo.toDate?.slice(0, 10)}`
                    : 'Not stored yet — the engine will build it from a finer timeframe.'}
                </span>
              </div>
            </label>
          </div>

          <DateRange
            from={from} to={to} setFrom={setFrom} setTo={setTo}
            available={seriesInfo ? { from: seriesInfo.fromDate, to: seriesInfo.toDate } : null}
          />

          <details className="bg-base-200 rounded-box p-4">
            <summary className="cursor-pointer font-medium">
              Optional: capital{strategy && Object.keys(strategy.defaultParameters).length > 0 ? ', parameters' : ''}
            </summary>
            <div className="grid gap-4 md:grid-cols-3 mt-4">
              <label className="form-control">
                <FieldLabel text="Initial capital" hint="Starting equity. Empty uses the engine's configured default." />
                <input type="number" step="any" className="input input-bordered"
                  placeholder="from config" value={capital}
                  onChange={(e) => setCapital(e.target.value)} />
              </label>
            </div>

            {strategy && Object.keys(strategy.defaultParameters).length > 0 && (
              <div className="grid gap-4 md:grid-cols-3 mt-4">
                {Object.entries(strategy.defaultParameters).map(([key, def]) => (
                  <label key={key} className="form-control">
                    <FieldLabel
                      text={key}
                      className="font-mono text-xs"
                      hint={`Strategy parameter. Empty uses the default, ${def}.`}
                    />
                    <input
                      className="input input-bordered"
                      value={params[key] ?? ''}
                      placeholder={def}
                      onChange={(e) => setParams((p) => ({ ...p, [key]: e.target.value }))}
                    />
                  </label>
                ))}
              </div>
            )}

            {strategy?.requiresTrainedModel && (
              <label className="form-control mt-4">
                <FieldLabel text="Model version (optional pin)" hint="Pins the run to one trained model. Empty uses the newest model under this configuration's cache key." />
                <input
                  className="input input-bordered font-mono"
                  placeholder="latest under the cache key"
                  value={modelVersion}
                  onChange={(e) => setModelVersion(e.target.value)}
                />
                <div className="label">
                  <span className="label-text-alt text-base-content/50">
                    Version ids are on the <Link className="link" to="/models">Models</Link> page.
                  </span>
                </div>
              </label>
            )}
          </details>

          <button className="btn btn-primary" disabled={!ready || running}>
            {running && <span className="loading loading-spinner loading-sm" />}
            {running ? 'Running…' : 'Run backtest'}
          </button>
        </form>
      )}

      {runError && (
        <div className="alert alert-error mt-4">
          <span>{runError}</span>
        </div>
      )}

      {result && <RunSummary result={result} />}
    </section>
  )
}

/**
 * PerformanceMetrics fields are optional — older saved results predate some of
 * them, and the type carries an index signature for the rest. Format defensively
 * rather than asserting they're present.
 */
function num(value: number | undefined, digits = 2, suffix = ''): string {
  return value !== undefined && Number.isFinite(value)
    ? `${value.toFixed(digits)}${suffix}`
    : '—'
}

function RunSummary({ result }: { result: RunResult }) {
  const m = result.metrics
  const positive = (m.totalReturnPct ?? 0) >= 0

  return (
    <div className="bg-base-200 rounded-box p-4 mt-6">
      <div className="flex flex-wrap items-baseline gap-3 mb-4">
        <h2 className="text-xl font-semibold">
          {result.strategyName} · {result.instrumentSymbol} {result.timeframe}
        </h2>
        <span className="badge badge-outline">{result.dataSource}</span>
        {result.modelCacheHit !== null && (
          <span className={`badge ${result.modelCacheHit ? 'badge-success' : 'badge-warning'}`}>
            {result.modelCacheHit ? 'cache hit' : 'fresh'}
          </span>
        )}
        <Verdict metrics={m} />
        <span className="ml-auto text-sm text-base-content/50">
          {result.startDate.slice(0, 10)} → {result.endDate.slice(0, 10)}
        </span>
      </div>

      <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
        <Stat label="Return" value={num(m.totalReturnPct, 2, '%')} tone={positive ? 'success' : 'error'} />
        <Stat label="Final equity" value={`$${result.finalEquity.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} />
        <Stat label="Sharpe" value={num(m.sharpeRatio)} />
        <Stat label="Max drawdown" value={num(m.maxDrawdownPct, 2, '%')} />
        <Stat label="Trades" value={m.totalTrades !== undefined ? String(m.totalTrades) : '—'} />
        <Stat label="Win rate" value={num(m.winRate, 1, '%')} />
        <Stat label="Profit factor" value={num(m.profitFactor)} />
        <Stat label="Buy & hold" value={num(m.buyAndHoldReturnPct, 2, '%')} />
      </div>

      {m.totalTrades === 0 && (
        <div className="alert alert-info mt-4">
          <span>
            No trades: the strategy never produced an entry signal over this range. The run itself
            succeeded — try a different period, or loosen the strategy parameters.
          </span>
        </div>
      )}

      <p className="mt-4 text-sm text-base-content/60">
        Saved. It is now in <Link className="link" to="/results">Results</Link>, with the full trade
        table and equity curve.
      </p>
    </div>
  )
}

/**
 * One metric tile. The label carries its own explanation, the same bargain
 * FieldLabel strikes on the form above — the hint hangs off the label text
 * rather than a separate marker beside it. `bare` drops FieldLabel's wrapping
 * label row, which belongs to a form control and not to a tile, and the
 * className overrides `label-text` with the tile's own type scale. A metric
 * with no hint renders as a plain label rather than an empty bubble.
 */
const STAT_LABEL = 'text-xs uppercase tracking-wide text-base-content/50'

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'success' | 'error' }) {
  const hint = metricHint(label)

  return (
    <div className="bg-base-100 rounded-box px-3 py-2">
      {hint
        ? <FieldLabel bare text={label} hint={hint} className={STAT_LABEL} />
        : <div className={STAT_LABEL}>{label}</div>}
      <div className={`text-lg font-semibold tabular-nums ${tone ? `text-${tone}` : ''}`}>{value}</div>
    </div>
  )
}

/** A preset span, in years. `null` means the whole stored range. */
const PRESETS: { label: string; years: number | null }[] = [
  { label: '1Y', years: 1 },
  { label: '3Y', years: 3 },
  { label: '5Y', years: 5 },
  { label: '10Y', years: 10 },
  { label: 'All', years: null },
]

const iso = (d: Date) => d.toISOString().slice(0, 10)

/**
 * The date range for a run, promoted out of the "Optional" disclosure it used to
 * hide in. It is the control people reach for most — a backtest over the wrong
 * decade is the commonest way to get a meaningless number — and burying it made
 * it look advanced.
 *
 * The inputs are bounded by what is actually stored, so a range with no data
 * cannot be picked. Presets are anchored to the end of the stored data rather
 * than to today, because the last bar is often months behind now and "5Y" ought
 * to mean five years of data, not five years ending in a gap.
 */
function DateRange(
  { from, to, setFrom, setTo, available }: {
    from: string
    to: string
    setFrom: (v: string) => void
    setTo: (v: string) => void
    available: { from: string; to: string } | null
  },
) {
  const lo = available ? available.from.slice(0, 10) : undefined
  const hi = available ? available.to.slice(0, 10) : undefined

  function applyPreset(years: number | null) {
    if (!available) return
    if (years === null) { setFrom(''); setTo(''); return }
    const end = new Date(available.to)
    const start = new Date(end)
    start.setFullYear(start.getFullYear() - years)
    const floor = new Date(available.from)
    setFrom(iso(start < floor ? floor : start))
    setTo(iso(end))
  }

  // Calendar span of what is selected, falling back to the stored range when a
  // side is left empty — which is what the engine does with an empty bound.
  const effFrom = from || lo
  const effTo = to || hi
  let span: string | null = null
  if (effFrom && effTo) {
    const days = (new Date(effTo).getTime() - new Date(effFrom).getTime()) / 86_400_000
    span = days < 0 ? 'invalid' : days < 400
      ? `${Math.round(days)} days`
      : `${(days / 365.25).toFixed(1)} years`
  }

  return (
    <div className="bg-base-200 rounded-box p-4">
      <div className="flex flex-wrap items-baseline gap-3 mb-3">
        <span className="font-medium">Date range</span>
        <span className="text-sm text-base-content/60">
          {available
            ? <>stored: {lo} → {hi}</>
            : 'pick an instrument and timeframe first'}
        </span>
        {span && (
          <span className={`text-sm ${span === 'invalid' ? 'text-error' : 'text-base-content/60'}`}>
            {span === 'invalid' ? 'From is after To' : `· covering ${span}`}
          </span>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <label className="form-control">
          <FieldLabel
            text="From"
            hint="First candle to include. Empty starts at the beginning of the stored range. The strategy's warmup is taken from here, so a short range with a long indicator period can leave it with almost nothing to trade."
          />
          <input
            type="date" className="input input-bordered" min={lo} max={hi}
            value={from} onChange={(e) => setFrom(e.target.value)}
          />
        </label>
        <label className="form-control">
          <FieldLabel
            text="To"
            hint="Last candle to include. Empty runs to the end of the stored range. Import again to extend it — this does not advance on its own."
          />
          <input
            type="date" className="input input-bordered" min={lo} max={hi}
            value={to} onChange={(e) => setTo(e.target.value)}
          />
        </label>
        <div className="form-control">
          <FieldLabel
            text="Quick spans"
            hint="Counted back from the last stored bar, not from today — the data often ends months ago, and a span ending in a gap is not the span you asked for."
          />
          <div className="join">
            {PRESETS.map((p) => (
              <button
                key={p.label} type="button" className="join-item btn btn-sm"
                disabled={!available} onClick={() => applyPreset(p.years)}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * Did this run beat holding? Placed beside the headline rather than left to be
 * inferred from two tiles, because it is the question the backtest exists to
 * answer and the easiest one to skip past. A run can show a large positive
 * return and still be the worse choice — that is the normal case, not the
 * exception, for an indicator strategy in a rising market.
 */
function Verdict({ metrics }: { metrics: RunResult['metrics'] }) {
  const r = { totalReturnPct: metrics.totalReturnPct, buyAndHoldReturnPct: metrics.buyAndHoldReturnPct }
  const beat = beatBuyAndHold(r)
  const diff = vsBuyAndHold(r)
  if (beat === null || diff === null) return null
  return (
    <span
      className={tipClass}
      data-tip={beat
        ? `Ahead of buy-and-hold by ${diff.toFixed(1)} points over the same bars.`
        : `Holding the instrument over the same bars would have returned ${Math.abs(diff).toFixed(1)} points more, with no trading and no costs. The strategy has to beat that to be worth running.`}
    >
      <span className={`badge cursor-help ${beat ? 'badge-success' : 'badge-warning'}`}>
        {beat ? 'beat' : 'lost to'} buy &amp; hold by {Math.abs(diff).toFixed(1)} pts
      </span>
    </span>
  )
}
