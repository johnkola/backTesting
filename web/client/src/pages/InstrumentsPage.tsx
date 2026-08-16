import { useEffect, useMemo, useState } from 'react'
import { api, isAbortError, type AggregateResponse, type Instrument } from '../lib/api'

// Rollups we offer to build from a D1 series (matches the old
// candles_weekly / candles_monthly continuous aggregates).
const ROLLUP_TARGETS = ['W1', 'MN1'] as const

export default function InstrumentsPage() {
  const [instruments, setInstruments] = useState<Instrument[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    const ctrl = new AbortController()
    api.instruments(ctrl.signal)
      .then((r) => setInstruments(r.items))
      .catch((e: Error) => { if (!isAbortError(e)) setError(e.message) })
    return () => ctrl.abort()
  }, [reloadKey])

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
        <button className="btn btn-xs btn-outline" onClick={run} disabled={busy}>
          {busy && <span className="loading loading-spinner loading-xs" />}
          Roll up → W1 · MN1
        </button>
        <label
          className="label cursor-pointer gap-1 p-0"
          title="Rebuild even if the target rollup already exists"
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
