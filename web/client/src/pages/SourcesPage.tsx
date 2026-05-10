import { useEffect, useState } from 'react'
import { api, isAbortError, type Source } from '../lib/api'

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    api.sources(ctrl.signal)
      .then((r) => setSources(r.items))
      .catch((e: Error) => { if (!isAbortError(e)) setError(e.message) })
    return () => ctrl.abort()
  }, [])

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-4">Data sources</h1>
      {error && <div className="alert alert-error">{error}</div>}
      {!sources && !error && <span className="loading loading-spinner" />}
      {sources && (
        <div className="overflow-x-auto bg-base-200 rounded-box">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th>Created</th>
                <th className="text-right">ID</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id}>
                  <td className="font-mono">{s.name}</td>
                  <td className="text-base-content/70">{s.description ?? '—'}</td>
                  <td>{new Date(s.created_at).toLocaleString()}</td>
                  <td className="text-right text-base-content/50">{s.id}</td>
                </tr>
              ))}
              {sources.length === 0 && (
                <tr><td colSpan={4} className="text-center text-base-content/60">No sources yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
