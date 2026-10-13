import { api, type FeaturesReport, type Instrument } from '../../lib/api'
import { useApiData } from '../../lib/useApiData'
import { useTimeframe } from '../../lib/useTimeframe'
import StageShell from './StageShell'

type Loaded = { instruments: Instrument[], features: FeaturesReport }

/**
 * Stage 1 — data gathering and storage.
 *
 * The validation gate the design asks for is "every expected series is
 * present". That is answerable here without a new table: the feature config
 * already declares exactly which (symbol, source) pairs the enabled columns
 * need, so the gate is that list minus what the database holds.
 */
export default function DataStage() {
  const [tf] = useTimeframe()
  const { data, error } = useApiData<Loaded>(
    (signal) => Promise.all([
      api.instruments(tf, signal).then((r) => r.items),
      api.features(tf, signal),
    ]).then(([instruments, features]) => ({ instruments, features })),
    [tf],
  )

  if (error) return <div className="alert alert-error">{error}</div>
  if (!data) return <span className="loading loading-spinner" />

  const { instruments, features } = data
  const required = features.config?.requiredSeries ?? []

  // A series counts as held when some source slice for it exists at this
  // timeframe. The source name has to match too: QQQ from yahoo-adj is not
  // the same column input as QQQ from yahoo.
  const held = new Set<string>()
  for (const i of instruments) {
    for (const s of i.sources) held.add(`${i.symbol}/${s.sourceName}`)
  }
  const missing = required.filter((r) => !held.has(`${r.symbol}/${r.source}`))

  const state = required.length === 0
    ? 'unknown'
    : missing.length === 0 ? 'passed' : 'failed'

  return (
    <StageShell
      state={state}
      summary={
        required.length === 0
          ? 'No required series declared'
          : missing.length === 0
            ? `All ${required.length} required series present at ${tf}`
            : `${missing.length} of ${required.length} required series missing at ${tf}`
      }
      detail={
        missing.length > 0
          ? (
            <>
              Missing:{' '}
              {missing.map((m) => (
                <span key={`${m.symbol}/${m.source}`} className="badge badge-error badge-sm mr-1 font-mono">
                  {m.symbol} ({m.source})
                </span>
              ))}
              <br />
              An enabled column cannot build without its series, so the feature
              stage will fail until these are imported.
            </>
          )
          : 'Every (symbol, source) pair the enabled columns name is loadable.'
      }
    >
      <div className="overflow-x-auto bg-base-200 rounded-box">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Type</th>
              <th>Source</th>
              <th className="text-right">Bars</th>
              <th>From</th>
              <th>To</th>
              <th>Needed</th>
            </tr>
          </thead>
          <tbody>
            {instruments.flatMap((i) =>
              i.sources.map((s) => {
                const needed = required.some(
                  (r) => r.symbol === i.symbol && r.source === s.sourceName,
                )
                return (
                  <tr key={`${i.symbol}-${s.sourceId}-${s.timeframe}`}>
                    <td className="font-mono">{i.symbol}</td>
                    <td className="text-xs opacity-70">{i.type}</td>
                    <td className="font-mono text-xs">{s.sourceName}</td>
                    <td className="text-right">{s.candleCount.toLocaleString()}</td>
                    <td className="text-xs">{s.fromDate?.slice(0, 10) ?? '—'}</td>
                    <td className="text-xs">{s.toDate?.slice(0, 10) ?? '—'}</td>
                    <td>
                      {needed
                        ? <span className="badge badge-success badge-sm">in the model</span>
                        : <span className="text-base-content/40">—</span>}
                    </td>
                  </tr>
                )
              }),
            )}
            {instruments.length === 0 && (
              <tr>
                <td colSpan={7} className="text-center text-base-content/60">
                  Nothing stored at {tf}. Import some bars, or switch timeframe.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </StageShell>
  )
}
