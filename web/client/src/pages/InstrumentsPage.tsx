import { useEffect, useState } from 'react'
import { api, isAbortError, type Instrument } from '../lib/api'

export default function InstrumentsPage() {
  const [instruments, setInstruments] = useState<Instrument[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    api.instruments(ctrl.signal)
      .then((r) => setInstruments(r.items))
      .catch((e: Error) => { if (!isAbortError(e)) setError(e.message) })
    return () => ctrl.abort()
  }, [])

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
            <div key={i.id} className="bg-base-200 rounded-box p-4">
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
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
