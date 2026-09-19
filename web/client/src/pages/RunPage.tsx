import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api, isAbortError,
  type Instrument, type RunResult, type Strategy,
} from '../lib/api'
import FieldLabel from '../components/FieldLabel'

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
        setInstruments(i.items)
        if (s.items.length > 0) selectStrategy(s.items[0])
        const first = i.items.find((x) => x.sources.length > 0)
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

          <details className="bg-base-200 rounded-box p-4">
            <summary className="cursor-pointer font-medium">
              Optional: date range, capital{strategy && Object.keys(strategy.defaultParameters).length > 0 ? ', parameters' : ''}
            </summary>
            <div className="grid gap-4 md:grid-cols-3 mt-4">
              <label className="form-control">
                <FieldLabel text="From" hint="First candle to include. Empty runs from the start of the stored range." />
                <input type="date" className="input input-bordered"
                  value={from} onChange={(e) => setFrom(e.target.value)} />
              </label>
              <label className="form-control">
                <FieldLabel text="To" hint="Last candle to include. Empty runs to the end of the stored range." />
                <input type="date" className="input input-bordered"
                  value={to} onChange={(e) => setTo(e.target.value)} />
              </label>
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

function Stat({ label, value, tone }: { label: string; value: string; tone?: 'success' | 'error' }) {
  return (
    <div className="bg-base-100 rounded-box px-3 py-2">
      <div className="text-xs uppercase tracking-wide text-base-content/50">{label}</div>
      <div className={`text-lg font-semibold tabular-nums ${tone ? `text-${tone}` : ''}`}>{value}</div>
    </div>
  )
}
