import { useEffect, useState } from 'react'
import { api, isAbortError, type ImportRecord, type Paginated } from '../lib/api'
import Pagination from '../components/Pagination'

const LIMIT = 25

export default function ImportsPage() {
  const [data, setData] = useState<Paginated<ImportRecord> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [source, setSource] = useState('')
  const [instrument, setInstrument] = useState('')

  useEffect(() => {
    setError(null)
    const ctrl = new AbortController()
    api.imports({ limit: LIMIT, offset, source, instrument }, ctrl.signal)
      .then(setData)
      .catch((e: Error) => { if (!isAbortError(e)) setError(e.message) })
    return () => ctrl.abort()
  }, [offset, source, instrument])

  function applyFilters(s: string, i: string) {
    setOffset(0)
    setSource(s)
    setInstrument(i)
  }

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-4">Imports</h1>

      <div className="flex flex-wrap gap-2 mb-4">
        <input
          className="input input-sm input-bordered"
          placeholder="filter by source (e.g. yahoo)"
          value={source}
          onChange={(e) => applyFilters(e.target.value, instrument)}
        />
        <input
          className="input input-sm input-bordered"
          placeholder="filter by instrument (e.g. AAPL)"
          value={instrument}
          onChange={(e) => applyFilters(source, e.target.value)}
        />
        {(source || instrument) && (
          <button className="btn btn-sm btn-ghost" onClick={() => applyFilters('', '')}>
            clear
          </button>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {!data && !error && <span className="loading loading-spinner" />}
      {data && (
        <>
          <div className="overflow-x-auto bg-base-200 rounded-box">
            <table className="table table-sm">
              <thead>
                <tr>
                  <th>Imported</th>
                  <th>Source</th>
                  <th>Instrument</th>
                  <th>Timeframe</th>
                  <th>File</th>
                  <th className="text-right">Rows</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id}>
                    <td className="whitespace-nowrap">{new Date(r.importedAt).toLocaleString()}</td>
                    <td className="font-mono">{r.sourceName}</td>
                    <td className="font-mono">{r.instrumentSymbol}</td>
                    <td>{r.timeframe}</td>
                    <td title={r.filePath}>{r.fileName}</td>
                    <td className="text-right tabular-nums">{r.rowCount.toLocaleString()}</td>
                  </tr>
                ))}
                {data.items.length === 0 && (
                  <tr><td colSpan={6} className="text-center text-base-content/60">No imports match.</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <Pagination total={data.total} limit={data.limit} offset={data.offset} onChange={setOffset} />
        </>
      )}
    </section>
  )
}
