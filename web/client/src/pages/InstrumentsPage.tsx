import { useMemo, useState } from 'react'
import { api, type AggregateResponse, type AuditResponse, type Instrument } from '../lib/api'
import { useApiData } from '../lib/useApiData'
import { tipClass } from '../components/FieldLabel'

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
      <h1 className="text-2xl font-semibold mb-4">Instruments</h1>
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
          <span className={`badge ${report.ok ? 'badge-success' : 'badge-warning'}`}>
            {report.ok
              ? `${report.seriesAudited} series clean`
              : `${report.totalIssues} issue${report.totalIssues === 1 ? '' : 's'}`}
          </span>
        )}
        {error && <span className="text-error text-sm">{error}</span>}
      </div>

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
                      <span className="badge badge-xs badge-outline mr-2">{f.category}</span>
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
                <th>Source</th>
                <th>Timeframe</th>
                <th className="text-right">Candles</th>
                <th>From</th>
                <th>To</th>
                <th className="text-right">Rollups</th>
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
