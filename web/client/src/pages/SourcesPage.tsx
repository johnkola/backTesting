import { useState } from 'react'
import RemoveSeriesButton from '../components/RemoveSeriesButton'
import { api, type Source } from '../lib/api'
import { useApiData } from '../lib/useApiData'

export default function SourcesPage() {
  const [reload, setReload] = useState(0)
  const { data: sources, error } = useApiData<Source[]>(
    (signal) => api.sources(signal).then((r) => r.items),
    [reload],
  )

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
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id}>
                  <td className="font-mono">{s.name}</td>
                  <td className="text-base-content/70">{s.description ?? '—'}</td>
                  <td>{new Date(s.created_at).toLocaleString()}</td>
                  <td className="text-right text-base-content/50">{s.id}</td>
                  <td>
                    <RemoveSeriesButton
                      label="delete"
                      name={s.name}
                      preview={() => api.deleteSource(String(s.id), true)}
                      remove={() => api.deleteSource(String(s.id))}
                      onDeleted={() => setReload((n) => n + 1)}
                    />
                  </td>
                </tr>
              ))}
              {sources.length === 0 && (
                <tr><td colSpan={5} className="text-center text-base-content/60">No sources yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
