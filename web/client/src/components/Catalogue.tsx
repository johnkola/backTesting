import { api, type BenchmarkEntry, type EngineEntry } from '../lib/api'
import { libraryLine } from '../lib/catalogue'
import { useApiData } from '../lib/useApiData'

/**
 * The Docs tab's catalogue (R74): every engine a strategy can train and every
 * benchmark a run is scored beside, each with its library and version, what it
 * does and what checks it. The entries are the runner's own registries, so a
 * new engine or benchmark appears here when it is registered.
 */
export default function Catalogue() {
  const engines = useApiData((signal) => api.engines(signal).then((r) => r.items), [])
  const benchmarks = useApiData((signal) => api.benchmarks(signal).then((r) => r.items), [])
  return (
    <div className="space-y-6">
      <p className="text-sm text-base-content/70 max-w-3xl">
        What a strategy trains with and what every result is scored beside. The same entries
        are on the Strategy tab and in the tooltips of the result page&apos;s and the Training
        tab&apos;s benchmark rows.
      </p>
      <Entries title="Engines" data={engines.data} error={engines.error} />
      <Entries title="Benchmarks" data={benchmarks.data} error={benchmarks.error} />
    </div>
  )
}

function Entries({ title, data, error }: {
  title: string
  data: (EngineEntry | BenchmarkEntry)[] | null
  error: string | null
}) {
  return (
    <section>
      <h2 className="text-lg font-semibold mb-2">{title}</h2>
      {error && <div className="alert alert-error text-sm">{error}</div>}
      {!data && !error && <span className="loading loading-spinner" />}
      {data && (
        <div className="overflow-x-auto bg-base-200 rounded-box">
          <table className="table table-sm">
            <thead>
              <tr><th>Name</th><th>Computed with</th><th>What it does</th><th>Checked by</th></tr>
            </thead>
            <tbody>
              {data.map((e) => (
                <tr key={e.name} className="align-top">
                  <td>
                    <div className="font-medium">{e.title}</div>
                    <div className="font-mono text-xs text-base-content/60">{e.name}</div>
                  </td>
                  <td className="text-xs whitespace-nowrap">{libraryLine(e)}</td>
                  <td className="text-xs">{e.description}</td>
                  <td className="text-xs">{e.checkedBy}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
