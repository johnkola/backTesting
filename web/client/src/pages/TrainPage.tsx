import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  api, engineWindow, isAbortError,
  type AuditResponse, type Instrument, type Strategy, type TrainResponse,
} from '../lib/api'
import FieldLabel, { tipClass } from '../components/FieldLabel'
import UploadCard from '../components/UploadCard'
import { categoryHint } from '../lib/cohesionHints'

/**
 * Train a model for a strategy that needs one before it can be backtested.
 *
 * Training was CLI-only until this page existed, and the reason it is not
 * simply a button is the cache key: a model is addressed by a fingerprint of
 * the exact candle window it trained on, and the engine addresses candles by
 * Ta4j bar-END times rather than the stored timestamps. Choose the window by
 * hand and you get a model no run will ever find — 409, with the model sitting
 * on disk. This page computes the window from the series the user picks (see
 * `engineWindow`), so what it trains is always what the Run page can use.
 *
 * Training is synchronous and slow — tens of seconds on a long daily series —
 * so the button stays busy rather than pretending to be instant.
 */
export default function TrainPage() {
  const [strategies, setStrategies] = useState<Strategy[] | null>(null)
  const [instruments, setInstruments] = useState<Instrument[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [strategyName, setStrategyName] = useState('')
  const [symbol, setSymbol] = useState('')
  const [source, setSource] = useState('')
  const [timeframe, setTimeframe] = useState('')
  const [params, setParams] = useState<Record<string, string>>({})
  const [mode, setMode] = useState<'auto' | 'force'>('force')

  const [reloadKey, setReloadKey] = useState(0)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<TrainResponse | null>(null)
  const [resultKey, setResultKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Verification gate. A model is only as good as the candles behind it, and a
  // gap or a broken bar is invisible once it has been folded into weights — so
  // the data is checked before training rather than after a confusing result.
  // Findings never block (they are advisory everywhere else in this app), but
  // an unclean series has to be acknowledged deliberately.
  // Each of these is stamped with the series it belongs to, so switching
  // instrument invalidates them by comparison rather than by resetting state
  // inside an effect — the pattern `lib/useApiData.ts` uses, and what
  // `react-hooks/set-state-in-effect` exists to prevent.
  const [checking, setChecking] = useState(false)
  const [audit, setAudit] = useState<{ key: string; data: AuditResponse } | null>(null)
  const [auditError, setAuditError] = useState<{ key: string; message: string } | null>(null)
  const [ackKey, setAckKey] = useState<string | null>(null)

  useEffect(() => {
    const ac = new AbortController()
    Promise.all([api.strategies(ac.signal), api.instruments(ac.signal)])
      .then(([s, i]) => {
        // Only strategies that actually cache a model are trainable; the Ta4j
        // ones have nothing to learn and `train` would be a no-op on them.
        const trainable = s.items.filter((x) => x.requiresTrainedModel)
        setStrategies(trainable)
        setInstruments(i.items)
        if (trainable.length > 0) {
          setStrategyName(trainable[0].name)
          setParams({ ...trainable[0].defaultParameters })
        }
        const withCandles = i.items.find((x) => x.sources.length > 0)
        if (withCandles) {
          setSymbol(withCandles.symbol)
          setSource(withCandles.sources[0].sourceName)
          setTimeframe(withCandles.sources[0].timeframe)
        }
      })
      .catch((e) => { if (!isAbortError(e)) setLoadError((e as Error).message) })
    return () => ac.abort()
  }, [reloadKey])

  const strategy = strategies?.find((s) => s.name === strategyName)
  const instrument = instruments?.find((i) => i.symbol === symbol)
  const sourcesForSymbol = useMemo(
    () => [...new Set(instrument?.sources.map((s) => s.sourceName) ?? [])],
    [instrument],
  )
  const timeframesForSource = useMemo(
    () => instrument?.sources.filter((s) => s.sourceName === source).map((s) => s.timeframe) ?? [],
    [instrument, source],
  )
  /** The exact stored series the model will be fingerprinted against. */
  const slice = instrument?.sources.find(
    (s) => s.sourceName === source && s.timeframe === timeframe,
  )
  const window = slice ? engineWindow(timeframe, slice.fromDate, slice.toDate) : null

  /** Identity of the series on screen. Anything stamped with a different one is stale. */
  const seriesKey = `${symbol}|${source}|${timeframe}`
  const report = audit?.key === seriesKey ? audit.data : null
  const reportError = auditError?.key === seriesKey ? auditError.message : null
  const acknowledged = ackKey === seriesKey
  const verified = report !== null
  const clean = report?.ok === true
  const ready = !!(strategy && slice) && !busy && verified && (clean || acknowledged)

  async function verify() {
    if (!slice) return
    const key = seriesKey
    setChecking(true); setAuditError(null); setAudit(null); setAckKey(null)
    try {
      setAudit({ key, data: await api.audit({ symbol, source, timeframe, examples: true }) })
    } catch (err) {
      setAuditError({ key, message: (err as Error).message })
    } finally {
      setChecking(false)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!slice || !strategy) return
    setBusy(true); setError(null); setResult(null)
    try {
      setResult(await api.train({
        symbol, source, timeframe,
        firstCandle: slice.fromDate, lastCandle: slice.toDate,
        mode, parameters: params,
      }))
      setResultKey(seriesKey)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-1">Train a model</h1>
      <p className="text-base-content/70 mb-4 max-w-3xl">
        One strategy learns from the data instead of following a fixed rule, and it needs a
        trained model before a backtest will run — without one, the Run page returns 409. The
        whole path is here, in order: get candles in, check them, train, read the result. The
        model is cached on disk and reused afterwards, so you only come back when the data or
        the settings change.
      </p>

      {loadError && <div className="alert alert-error mb-4">{loadError}</div>}
      {!strategies && !loadError && <span className="loading loading-spinner" />}
      {strategies && strategies.length === 0 && (
        <div className="alert alert-info">No strategy in this build needs training.</div>
      )}

      {strategies && strategies.length > 0 && instruments && (
        <div className="space-y-4">
          <Step n={1} title="Add data, if you need to"
            note="Skip this if the series you want is already imported. An upload here is the same one the Imports page does — same archive rules, same cohesion report.">
            <UploadCard onSuccess={() => setReloadKey((k) => k + 1)} />
          </Step>

          <form onSubmit={submit} className="space-y-4">
            <Step n={2} title="Pick what to learn from">
              <div className="bg-base-200 rounded-box p-4 grid gap-4 md:grid-cols-2">
                <label className="form-control">
                  <FieldLabel
                    text="Strategy"
                    hint="Only strategies that cache a trained model are listed. The indicator strategies have nothing to learn, so they never need this page."
                  />
                  <select
                    className="select select-bordered" value={strategyName}
                    onChange={(e) => {
                      const s = strategies.find((x) => x.name === e.target.value)
                      if (s) { setStrategyName(s.name); setParams({ ...s.defaultParameters }) }
                    }}
                  >
                    {strategies.map((s) => (
                      <option key={s.name} value={s.name}>{s.name} — {s.description}</option>
                    ))}
                  </select>
                </label>

                <label className="form-control">
                  <FieldLabel
                    text="Instrument"
                    hint="The symbol to learn from. Only imported symbols appear; the model is tied to this one and cannot be reused on another."
                  />
                  <select
                    className="select select-bordered" value={symbol}
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
                  <FieldLabel
                    text="Source"
                    hint="Which import to learn from. A model trained on one source will not be found by a run against another, because the source is part of its identity."
                  />
                  <select
                    className="select select-bordered" value={source}
                    onChange={(e) => setSource(e.target.value)}
                  >
                    {sourcesForSymbol.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </label>

                <label className="form-control">
                  <FieldLabel
                    text="Timeframe"
                    hint="Candle size to learn from. Only timeframes actually stored for this source are offered — training never derives a rollup the way a backtest can."
                  />
                  <select
                    className="select select-bordered" value={timeframe}
                    onChange={(e) => setTimeframe(e.target.value)}
                  >
                    {timeframesForSource.map((tf) => <option key={tf} value={tf}>{tf}</option>)}
                  </select>
                </label>
              </div>

              {slice && window && (
                <div className="bg-base-200 rounded-box p-4 text-sm mt-4">
                  <div className="font-medium mb-1">What it will learn from</div>
                  <p className="text-base-content/70">
                    All <span className="font-mono">{slice.candleCount.toLocaleString()}</span> stored
                    candles, {new Date(slice.fromDate).toLocaleDateString()} to{' '}
                    {new Date(slice.toDate).toLocaleDateString()}. The whole series is used — there is
                    deliberately no date range here, because the window a model trains on becomes part
                    of its identity, and a hand-picked range produces a model the Run page cannot find.
                  </p>
                  <p className="text-base-content/50 text-xs mt-2 font-mono break-all">
                    engine window: {window.since} → {window.until}
                  </p>
                </div>
              )}

            </Step>

            <Step n={3} title="Check the data"
              note="Training folds whatever is in the candles into the model's weights, where a gap or a broken bar becomes invisible. Check first, so a surprising result later is about the strategy rather than the data.">
              <VerifyPanel
                slice={!!slice} checking={checking} audit={report} error={reportError}
                acknowledged={acknowledged} onVerify={verify}
                onAcknowledge={(v) => setAckKey(v ? seriesKey : null)}
              />

            </Step>

            <Step n={4} title="Train">
              <details className="bg-base-200 rounded-box p-4">
                <summary className="cursor-pointer font-medium">
                  Settings — {strategy ? Object.keys(strategy.defaultParameters).length : 0} of them,
                  all optional
                </summary>
                <p className="text-sm text-base-content/70 mt-2">
                  Every one of these is part of the model's identity: change any value and you get a
                  separate model rather than a replacement, and a run must use the same values to find
                  it. Leave them alone unless you are deliberately comparing.
                </p>
                <div className="grid gap-4 md:grid-cols-3 mt-4">
                  {strategy && Object.entries(strategy.defaultParameters).map(([key, def]) => (
                    <label key={key} className="form-control">
                      <FieldLabel
                        text={key} className="font-mono text-xs"
                        hint={`Training setting. Empty uses the default, ${def}. Changing it trains a separate model rather than replacing this one.`}
                      />
                      <input
                        className="input input-bordered" placeholder={def}
                        value={params[key] ?? ''}
                        onChange={(e) => setParams((p) => ({ ...p, [key]: e.target.value }))}
                      />
                    </label>
                  ))}
                </div>
              </details>

              <div className="flex flex-wrap items-center gap-3">
                <span
                  className={tipClass}
                  data-tip="Retrain writes a new version even when one is already cached. Reuse returns the existing model untouched and finishes instantly."
                >
                  <div className="join">
                    <button
                      type="button"
                      className={`join-item btn btn-sm ${mode === 'force' ? 'btn-active' : ''}`}
                      onClick={() => setMode('force')}
                    >
                      Retrain
                    </button>
                    <button
                      type="button"
                      className={`join-item btn btn-sm ${mode === 'auto' ? 'btn-active' : ''}`}
                      onClick={() => setMode('auto')}
                    >
                      Reuse if cached
                    </button>
                  </div>
                </span>

                <span
                  className={tipClass}
                  data-tip={!verified
                    ? 'Check the data first. A gap or a broken bar disappears into the weights, and you would not see it again in anything the model produces.'
                    : (!clean && !acknowledged)
                      ? 'The check found something. Read it, then tick the box to train anyway.'
                      : 'Runs in the loader and is synchronous — tens of seconds on a long daily series. The page waits rather than polling.'}
                >
                  <button className="btn btn-primary" disabled={!ready}>
                    {busy && <span className="loading loading-spinner loading-sm" />}
                    {busy ? 'Training…' : 'Train model'}
                  </button>
                </span>

                {!verified && (
                  <span className="text-sm text-base-content/60">Check the data first.</span>
                )}

                {busy && (
                  <span className="text-sm text-base-content/60">
                    This can take a while. Leaving the page cancels nothing — the loader finishes.
                  </span>
                )}
              </div>
            </Step>
          </form>
        </div>
      )}

      {error && (
        <div className="alert alert-error mt-4">
          <span>{error}</span>
        </div>
      )}

      {result && resultKey === seriesKey && <TrainSummary result={result} symbol={symbol} source={source} timeframe={timeframe} />}
    </section>
  )
}

/**
 * One numbered stage of the flow. The numbering is the point: training is four
 * things in a fixed order, and the failure people hit is doing them out of it —
 * training before the data is in, or before anyone has looked at it.
 */
function Step(
  { n, title, note, children }:
  { n: number; title: string; note?: string; children: React.ReactNode },
) {
  return (
    <section>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="badge badge-neutral badge-sm font-mono">{n}</span>
        <h2 className="font-medium">{title}</h2>
      </div>
      {note && <p className="text-sm text-base-content/70 mb-2 max-w-3xl">{note}</p>}
      {children}
    </section>
  )
}

function pct(v: number | undefined): string {
  return v === undefined || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(1)}%`
}

function TrainSummary(
  { result, symbol, source, timeframe }:
  { result: TrainResponse; symbol: string; source: string; timeframe: string },
) {
  const reused = result.status === 'cached'
  return (
    <div className="bg-base-200 rounded-box p-4 mt-6">
      <div className="flex flex-wrap items-baseline gap-3 mb-3">
        <h2 className="text-xl font-semibold">{result.strategy}</h2>
        <span className="badge badge-outline">{symbol} · {source} · {timeframe}</span>
        <span className={`badge ${reused ? 'badge-info' : 'badge-success'}`}>
          {reused ? 'reused an existing model' : 'trained a new model'}
        </span>
      </div>

      <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
        <Tile
          label="Validation accuracy" value={pct(result.finalValAcc)}
          hint="How often the model predicted correctly on the held-out tail of the range — data it did not learn from. Three outcomes are possible, so roughly 33% is what guessing would score."
        />
        <Tile
          label="Training accuracy" value={pct(result.finalTrainAcc)}
          hint="Accuracy on the data it did learn from. Far above the validation figure means it memorised rather than generalised."
        />
        <Tile
          label="Learned from" value={result.trainSamples?.toLocaleString() ?? '—'}
          hint="Bars used for learning, after the lookback window and the forward-looking label are trimmed off each end."
        />
        <Tile
          label="Held back" value={result.valSamples?.toLocaleString() ?? '—'}
          hint="Bars kept aside to score the model. They are the most recent ones in the range, not a random sample."
        />
      </div>

      <div className="mt-4 text-sm space-y-1">
        <div>
          <span className="text-base-content/60">version</span>{' '}
          <span className="font-mono">{result.versionId}</span>
        </div>
        <div className="break-all">
          <span className="text-base-content/60">cache key</span>{' '}
          <span className="font-mono text-xs">{result.cacheKey}</span>
        </div>
      </div>

      <div className="alert alert-info mt-4 text-sm">
        <span>
          Ready to use. Open <Link className="link" to="/run">Run</Link>, pick{' '}
          <span className="font-mono">{result.strategy}</span> with the same instrument, source and
          timeframe, and leave the date range empty — the run finds this model automatically.
          It is also listed on <Link className="link" to="/models">Models</Link>.
        </span>
      </div>

      <p className="mt-3 text-xs text-base-content/60 max-w-3xl">
        Read the accuracy with care. The held-back bars are the tail of the same range, and the
        labels look ahead a few bars to decide what the right answer was, so this measures whether
        the model learned a pattern in this history — not whether it would have predicted the
        future. See{' '}
        <Link className="link" to="/docs/architecture">Known limitations</Link>.
      </p>
    </div>
  )
}

function Tile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="bg-base-100 rounded-box px-3 py-2">
      <FieldLabel
        bare text={label} hint={hint}
        className="text-xs uppercase tracking-wide text-base-content/50"
      />
      <div className="text-lg font-semibold tabular-nums">{value}</div>
    </div>
  )
}

/**
 * The check that runs before training. It is the same cohesion pass the
 * Instruments page offers, narrowed to the one series about to be learned
 * from, and it is a gate rather than a note: a gap or a malformed bar vanishes
 * into the weights, and nothing the model later produces will point back at it.
 *
 * Findings still never block — they are advisory everywhere else in this app,
 * and most `gap` findings on a long US history are market closures rather than
 * missing data. So an unclean series is trainable, but only deliberately.
 */
function VerifyPanel(
  { slice, checking, audit, error, acknowledged, onVerify, onAcknowledge }: {
    slice: boolean
    checking: boolean
    audit: AuditResponse | null
    error: string | null
    acknowledged: boolean
    onVerify: () => void
    onAcknowledge: (v: boolean) => void
  },
) {
  const clean = audit?.ok === true
  return (
    <div className="bg-base-200 rounded-box p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium">1. Check the data</span>
        <span
          className={tipClass}
          data-tip="Runs the cohesion checks over the candles this model would learn from: gaps, duplicate or out-of-order timestamps, structurally broken bars, outliers. Read-only — it changes nothing."
        >
          <button
            type="button" className="btn btn-sm btn-outline"
            onClick={onVerify} disabled={!slice || checking}
          >
            {checking && <span className="loading loading-spinner loading-xs" />}
            {audit ? 'Check again' : 'Check data'}
          </button>
        </span>
        {audit && (
          <span
            className={tipClass}
            data-tip={clean
              ? 'Nothing suspect in this series. That means no obvious defect — not that the data is necessarily complete.'
              : `${audit.totalIssues} finding(s). Read them below; most gap findings on a long history are market closures rather than missing bars.`}
          >
            <span className={`badge ${clean ? 'badge-success' : 'badge-warning'}`}>
              {clean ? 'no problems found' : `${audit.totalIssues} finding${audit.totalIssues === 1 ? '' : 's'}`}
            </span>
          </span>
        )}
        {error && <span className="text-error text-sm">{error}</span>}
      </div>

      {!audit && !checking && (
        <p className="text-sm text-base-content/70 mt-2">
          Training folds whatever is in the candles into the model's weights, where a gap or a
          broken bar becomes invisible. Check first, so a surprising result later is about the
          strategy rather than the data.
        </p>
      )}

      {audit && !clean && (
        <>
          <div className="mt-3 space-y-1">
            {audit.items.filter((i) => !i.ok).map((i) => (
              <details key={`${i.source}-${i.timeframe}`} className="text-sm">
                <summary className="cursor-pointer text-warning">
                  <span className="font-mono">{i.source}/{i.timeframe}</span> — {i.summary}
                </summary>
                {i.examples && i.examples.length > 0 && (
                  <ul className="ml-6 mt-1 text-xs text-base-content/70 space-y-0.5">
                    {i.examples.slice(0, 8).map((f, n) => (
                      <li key={n}>
                        <span className={tipClass} data-tip={categoryHint(f.category) ?? f.category}>
                          <span className="badge badge-xs badge-outline mr-2 cursor-help">
                            {f.category}
                          </span>
                        </span>
                        {f.timestamp?.slice(0, 10) ?? ''} — {f.detail}
                      </li>
                    ))}
                  </ul>
                )}
              </details>
            ))}
          </div>
          <label className={`label cursor-pointer justify-start gap-2 mt-3 ${tipClass}`}
            data-tip="Findings are advice, not a verdict. Most gap findings on a long US history are days the exchange was shut, which no calendar can compute.">
            <input
              type="checkbox" className="checkbox checkbox-sm"
              checked={acknowledged} onChange={(e) => onAcknowledge(e.target.checked)}
            />
            <span className="label-text">I have read these and want to train anyway</span>
          </label>
        </>
      )}
    </div>
  )
}
