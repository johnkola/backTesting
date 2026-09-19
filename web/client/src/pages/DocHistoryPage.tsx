import { Link, useParams } from 'react-router-dom'
import { api, type DocRevision } from '../lib/api'
import { useApiData } from '../lib/useApiData'

/** Captured revisions of one doc. A revision is recorded whenever the file's
 *  content hash changes, which the API checks each time the doc is fetched. */
export default function DocHistoryPage() {
  const { slug = 'readme' } = useParams<{ slug: string }>()
  const { data, error } = useApiData<{ label: string; items: DocRevision[] }>(
    (signal) => api.docHistory(slug, signal),
    [slug],
  )

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-2">
        {data?.label ?? slug} — revision history
      </h1>
      <p className="mb-4 text-sm text-base-content/70">
        A new revision is recorded whenever the file's content hash changes.{' '}
        <Link to={`/docs/${slug}`} className="link">Back to {data?.label ?? slug}</Link>
      </p>

      {error && <div className="alert alert-error">{error}</div>}
      {!data && !error && <span className="loading loading-spinner" />}

      {data && data.items.length === 0 && (
        <div className="alert">
          No revisions captured yet — open <Link className="link" to={`/docs/${slug}`}>the doc</Link>{' '}
          once to record the first one.
        </div>
      )}

      {data && data.items.length > 0 && (
        <div className="overflow-x-auto bg-base-200 rounded-box">
          <table className="table table-sm">
            <thead>
              <tr>
                <th>ID</th>
                <th>Captured</th>
                <th>Hash</th>
                <th className="text-right">Size</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.items.map((h) => (
                <tr key={h.id}>
                  <td className="font-mono">{h.id}</td>
                  <td className="whitespace-nowrap">{new Date(h.capturedAt).toLocaleString()}</td>
                  <td className="font-mono text-base-content/60">{h.contentHash.slice(0, 12)}…</td>
                  <td className="text-right tabular-nums">{h.sizeBytes.toLocaleString()} B</td>
                  <td>
                    <Link className="btn btn-xs btn-outline" to={`/docs/${slug}?rev=${h.id}`}>
                      view
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
