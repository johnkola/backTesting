import { useMemo, useState } from 'react'
import { api, type AggregateResponse, type AuditResponse, type Instrument } from '../lib/api'
import { useApiData } from '../lib/useApiData'
import { tipClass } from '../components/FieldLabel'
import { categoryHint } from '../lib/cohesionHints'

// Rollups we offer to build from a D1 series (matches the old
// candles_weekly / candles_monthly continuous aggregates).
const ROLLUP_TARGETS = ['W1', 'MN1'] as const

export default function InstrumentsPage() {
  const [reloadKey, setReloadKey] = useState(0)

  const { data: instruments, error } = useApiData<Instrument[]>(
    (signal) => api.instruments(signal).then((r) => r.items),
    [reloadKey],
  )

  const refresh = () => setReloadKey((k) => k + 1)

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-1">Instruments</h1>
      <p className="text-base-content/70 mb-4 max-w-3xl">
        Everything you have candles for, and the ground truth for what you can back a test
        with. One card per symbol, one row per <strong>source</strong> and{' '}
        <strong>timeframe</strong> — the same symbol can hold parallel histories from
        different providers without them overwriting each other. If the Run page is not
        offering a combination you expect, this page is where you find out why. From here
        you can also build the weekly and monthly rollups a daily series can derive, and
        re-check the stored candles for data problems.
      </p>
      {error && <div className="alert alert-error">{error}</div>}
      {!instruments && !error && <span className="loading loading-spinner" />}
      {instruments && instruments.length === 0 && (
        <div className="text-base-content/60">No instruments imported yet.</div>
      )}
      {instruments && instruments.length > 0 && (
        <div className="space-y-4">
          {instruments.map((i) => (
            <InstrumentCard key={i.id} instrument={i} onAggregated={refresh} />
          ))}
        </div>
      )}
    </section>
  )
}

/**
 * Runs the cohesion checks over what is already stored for this instrument and
 * shows the per-series verdict. Advisory only — it never changes data, which is
 * why it can be a one-click action with no confirmation.
 */
function AuditButton({ symbol }: { symbol: string }) {
  const [running, setRunning] = useState(false)
  const [report, setReport] = useState<AuditResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setRunning(true)
    setError(null)
    try {
      setReport(await api.audit({ symbol, examples: true }))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="mt-3">
      <div className="flex items-center gap-3">
        <span
          className={tipClass}
          data-tip="Re-runs the cohesion checks over the candles already stored: gaps, duplicate or out-of-order timestamps, structural breaks, outliers. Read-only, and findings are advice rather than a blocker."
        >
          <button className="btn btn-sm btn-outline" onClick={run} disabled={running}>
            {running && <span className="loading loading-spinner loading-xs" />}
            Check data quality
          </button>
        </span>
        {report && (
          <span
            className={tipClass}
            data-tip={report.ok
              ? `Nothing suspect across ${report.seriesAudited} series. That means no obvious data defect — not that nothing unusual happened in the market.`
              : `${report.totalIssues} finding(s) across ${report.seriesAudited} series. Findings are advice, never a blocker: the candles are stored and backtests will run. Open a series below to see what was flagged.`}
          >
            <span className={`badge ${report.ok ? 'badge-success' : 'badge-warning'}`}>
              {report.ok
                ? `${report.seriesAudited} series clean`
                : `${report.totalIssues} issue${report.totalIssues === 1 ? '' : 's'}`}
            </span>
          </span>
        )}
        {error && <span className="text-error text-sm">{error}</span>}
      </div>

      {report && !report.ok && (
        <p className="mt-2 text-xs text-base-content/70 max-w-3xl">
          These are advisory. Nothing was changed, and every candle is still there — the
          checks look for broken <em>data</em>, not unusual <em>markets</em>, and two
          categories have a known class of false positive. Hover any{' '}
          <span className="badge badge-xs badge-outline align-middle">category</span> badge
          below for what it flags and when it is wrong. A run of <code>gap</code> findings
          on a long US history is usually days the exchange was closed for a reason no
          calendar can compute.
        </p>
      )}

      {report && report.items.length > 0 && (
        <div className="mt-2 space-y-1">
          {report.items.map((s) => (
            <details key={`${s.source}-${s.timeframe}`} className="text-sm">
              <summary className={`cursor-pointer ${s.ok ? 'text-base-content/60' : 'text-warning'}`}>
                <span className="font-mono">{s.source}/{s.timeframe}</span> — {s.summary}
              </summary>
              {s.examples && s.examples.length > 0 && (
                <ul className="ml-6 mt-1 text-xs text-base-content/70 space-y-0.5">
                  {s.examples.slice(0, 10).map((f, n) => (
                    <li key={n}>
                      <span className={tipClass} data-tip={categoryHint(f.category) ?? f.category}>
                        <span className="badge badge-xs badge-outline mr-2 cursor-help">{f.category}</span>
                      </span>
                      {f.timestamp?.slice(0, 19) ?? ''} — {f.detail}
                    </li>
                  ))}
                </ul>
              )}
            </details>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * A column header that explains itself. The tip sits on the header text rather
 * than on the whole cell, so a wide right-aligned column does not open a bubble
 * from the far edge of the table.
 */
function Th({ children, hint, align }: { children: React.ReactNode; hint: string; align?: 'right' }) {
  return (
    <th className={align === 'right' ? 'text-right' : undefined}>
      <span className={tipClass} data-tip={hint}>
        <span
          tabIndex={0}
          role="note"
          aria-label={hint}
          className="cursor-help underline decoration-dotted decoration-base-content/30
                     underline-offset-4 transition-colors hover:decoration-base-content/70
                     focus-visible:outline-none focus-visible:decoration-base-content/70"
        >
          {children}
        </span>
      </span>
    </th>
  )
}

function InstrumentCard({ instrument: i, onAggregated }: { instrument: Instrument; onAggregated: () => void }) {
  // Timeframes present per source name, so a D1 row knows which rollups exist.
  const tfsBySource = useMemo(() => {
    const m = new Map<string, Set<string>>()
    for (const s of i.sources) {
      if (!m.has(s.sourceName)) m.set(s.sourceName, new Set())
      m.get(s.sourceName)!.add(s.timeframe)
    }
    return m
  }, [i.sources])

  return (
    <div className="bg-base-200 rounded-box p-4">
      <div className="flex flex-wrap items-baseline gap-3 mb-3">
        <h2 className="text-xl font-mono font-semibold">{i.symbol}</h2>
        <span className="badge badge-outline">{i.type}</span>
        <span className="text-base-content/60">{i.name ?? ''}</span>
        <span className="ml-auto text-sm text-base-content/50">
          precision {i.pricePrecision} · pip {i.pipSize}
        </span>
      </div>
      {i.sources.length === 0 ? (
        <div className="text-base-content/60 text-sm">No candles loaded.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="table table-sm">
            <thead>
              <tr>
                <Th hint="Which import these candles came from — the label given at upload time, such as yahoo or a broker export. Pick the same name again when you run a backtest.">
                  Source
                </Th>
                <Th hint="Candle size for this row. D1 is one bar per trading day; W1 and MN1 are usually derived from it rather than imported.">
                  Timeframe
                </Th>
                <Th align="right" hint="How many bars are stored for this source and timeframe. Fewer than a few hundred makes most backtest metrics unreliable.">
                  Candles
                </Th>
                <Th hint="Timestamp of the oldest stored bar. A backtest cannot start before this date.">
                  From
                </Th>
                <Th hint="Timestamp of the newest stored bar. Import again to extend it — nothing here updates on its own.">
                  To
                </Th>
                <Th align="right" hint="Builds the weekly and monthly series from this daily one. Only offered on a D1 row, because a rollup has to come from a finer timeframe.">
                  Rollups
                </Th>
              </tr>
            </thead>
            <tbody>
              {i.sources.map((s) => (
                <tr key={`${s.sourceId}-${s.timeframe}`}>
                  <td className="font-mono">{s.sourceName}</td>
                  <td>{s.timeframe}</td>
                  <td className="text-right tabular-nums">{s.candleCount.toLocaleString()}</td>
                  <td className="text-base-content/70">{new Date(s.fromDate).toLocaleDateString()}</td>
                  <td className="text-base-content/70">{new Date(s.toDate).toLocaleDateString()}</td>
                  <td className="text-right">
                    {s.timeframe === 'D1' && (
                      <AggregateButton
                        symbol={i.symbol}
                        source={s.sourceName}
                        present={tfsBySource.get(s.sourceName) ?? new Set()}
                        onDone={onAggregated}
                      />
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {i.sources.length > 0 && <AuditButton symbol={i.symbol} />}
    </div>
  )
}

function summarize(res: AggregateResponse): string {
  return res.results
    .map((r) =>
      r.status === 'aggregated'
        ? `${r.timeframe}: ${r.rowsWritten.toLocaleString()} rows`
        : `${r.timeframe}: skipped (${r.existingRows.toLocaleString()} exist)`,
    )
    .join(' · ')
}

/** Build W1/MN1 from a D1 series for one (instrument, source). Missing-only
 *  by default; "force" rebuilds rollups that already exist. */
function AggregateButton({
  symbol,
  source,
  present,
  onDone,
}: {
  symbol: string
  source: string
  present: Set<string>
  onDone: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [force, setForce] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const allPresent = ROLLUP_TARGETS.every((t) => present.has(t))

  async function run() {
    setBusy(true)
    setErr(null)
    setMsg(null)
    try {
      const res = await api.aggregate({
        symbol,
        source,
        sourceTf: 'D1',
        targetTfs: [...ROLLUP_TARGETS],
        skipExisting: !force,
      })
      setMsg(summarize(res))
      onDone()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        <span
          className={tipClass}
          data-tip="Builds weekly and monthly candles from this daily series. A timeframe that already has rows is skipped, not refreshed — tick force to rebuild one after importing more daily candles."
        >
          <button className="btn btn-xs btn-outline" onClick={run} disabled={busy}>
            {busy && <span className="loading loading-spinner loading-xs" />}
            Roll up → W1 · MN1
          </button>
        </span>
        <label
          className={`label cursor-pointer gap-1 p-0 ${tipClass}`}
          data-tip="Rebuild a rollup that already has rows, instead of skipping it. Use this after importing more daily candles."
        >
          <input
            type="checkbox"
            className="checkbox checkbox-xs"
            checked={force}
            onChange={(e) => setForce(e.target.checked)}
          />
          <span className="label-text text-xs">force</span>
        </label>
      </div>
      {msg && <span className="text-xs text-success">{msg}</span>}
      {err && <span className="text-xs text-error">{err}</span>}
      {allPresent && !force && !msg && !err && (
        <span className="text-xs text-base-content/50">W1 &amp; MN1 present — check force to rebuild</span>
      )}
    </div>
  )
}
