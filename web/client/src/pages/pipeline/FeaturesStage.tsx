import { useState } from 'react'
import { api, type FeatureColumn, type FeaturesReport } from '../../lib/api'
import { useApiData } from '../../lib/useApiData'
import { useTimeframe } from '../../lib/useTimeframe'
import StageShell, { type StageState } from './StageShell'

/** A disabled column is a recorded decision, not an absence — the badge says
 *  which decision, straight from features.toml. */
function stateBadge(col: FeatureColumn) {
  if (col.enabled) return { label: 'in the model', cls: 'badge-success' }
  switch (col.status) {
    case 'blocked': return { label: 'blocked', cls: 'badge-error' }
    case 'needs-import': return { label: 'needs import', cls: 'badge-warning' }
    case 'later-paid': return { label: 'later — paid', cls: 'badge-info' }
    case 'undecided': return { label: 'undecided', cls: 'badge-ghost' }
    default: return { label: 'data only', cls: 'badge-ghost' }
  }
}

function fmtParams(params: Record<string, number | string>) {
  const entries = Object.entries(params)
  return entries.length ? entries.map(([k, v]) => `${k}=${v}`).join(', ') : '—'
}

/** Stage 2 — the feature table itself: which columns exist, how each is
 *  normalized, and which are out of the model with the reason recorded. */
export default function FeaturesStage() {
  const [tf] = useTimeframe()
  const { data, error } = useApiData<FeaturesReport>((signal) => api.features(tf, signal), [tf])
  const [openNote, setOpenNote] = useState<string | null>(null)

  if (error) return <div className="alert alert-error">{error}</div>
  if (!data) return <span className="loading loading-spinner" />

  if (data.configError) {
    return (
      <StageShell
        state="failed"
        summary="features.toml could not be read"
        detail={data.configError}
      />
    )
  }

  const config = data.config
  if (!config) return <div className="alert">No feature config available.</div>

  const enabled = config.columns.filter((c) => c.enabled)
  const off = config.columns.filter((c) => !c.enabled)
  const newest = data.sets[0] ?? null
  const stale = newest ? newest.schemaVersion !== config.schemaVersion : false

  const state: StageState = newest === null ? 'empty' : stale ? 'stale' : 'passed'

  return (
    <StageShell
      state={state}
      summary={
        newest === null
          ? `No set built for ${tf}`
          : stale
            ? `Newest set is schema v${newest.schemaVersion}, config declares v${config.schemaVersion}`
            : `${enabled.length} columns built, ${config.inputWidth} values per bar`
      }
      detail={
        newest === null
          ? <>Run <span className="font-mono">python -m nn.feature_build</span> to build one.</>
          : stale
            ? 'Rebuild before training, or a model binds to a vector the config no longer describes.'
            : (
              <>
                Aligned to <span className="font-mono">{config.alignment.calendarSymbol}</span>{' '}
                ({config.alignment.calendarSource}, {config.alignment.calendarTimeframe}), fill{' '}
                <span className="font-mono">{config.alignment.fill}</span> to a maximum of{' '}
                {config.alignment.maxFillDays} days. {off.length} further columns are defined
                but out of the model.
              </>
            )
      }
    >
      <div className="stats stats-horizontal bg-base-200 shadow-sm mb-6 flex-wrap">
        <div className="stat">
          <div className="stat-title">Schema version</div>
          <div className="stat-value text-2xl">{config.schemaVersion}</div>
          <div className="stat-desc">keys the model cache</div>
        </div>
        <div className="stat">
          <div className="stat-title">Values per bar</div>
          <div className="stat-value text-2xl">{config.inputWidth}</div>
          <div className="stat-desc">cyclical columns count twice</div>
        </div>
        <div className="stat">
          <div className="stat-title">Network inputs</div>
          <div className="stat-value text-2xl">
            {config.lookbackWindow ? config.inputWidth * config.lookbackWindow : '—'}
          </div>
          <div className="stat-desc">× lookback {config.lookbackWindow ?? '?'}</div>
        </div>
      </div>

      <div className="overflow-x-auto bg-base-200 rounded-box mb-4">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Column</th>
              <th>Normalization</th>
              <th>Params</th>
              <th className="text-right">Width</th>
              <th>State</th>
              <th>Why</th>
            </tr>
          </thead>
          <tbody>
            {config.columns.map((c) => {
              const badge = stateBadge(c)
              return (
                <tr key={c.name} className={c.enabled ? '' : 'opacity-60'}>
                  <td className="font-mono">{c.name}</td>
                  <td className="font-mono text-xs">{c.method}</td>
                  <td className="font-mono text-xs">{fmtParams(c.params)}</td>
                  <td className="text-right">{c.width}</td>
                  <td><span className={`badge badge-sm ${badge.cls}`}>{badge.label}</span></td>
                  <td>
                    {c.note
                      ? (
                        <button
                          type="button"
                          className="btn btn-ghost btn-xs"
                          onClick={() => setOpenNote(openNote === c.name ? null : c.name)}
                        >
                          {openNote === c.name ? 'hide' : 'note'}
                        </button>
                      )
                      : <span className="text-base-content/40">—</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {openNote && (
        <div className="card bg-base-200 mb-6">
          <div className="card-body">
            <h3 className="card-title text-base font-mono">{openNote}</h3>
            <pre className="whitespace-pre-wrap text-sm text-base-content/80">
              {config.columns.find((c) => c.name === openNote)?.note}
            </pre>
          </div>
        </div>
      )}

      <h2 className="text-lg font-semibold mb-2">Built sets</h2>
      <div className="overflow-x-auto bg-base-200 rounded-box">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>ID</th>
              <th>Instrument</th>
              <th className="text-right">Schema</th>
              <th>Config</th>
              <th className="text-right">Columns</th>
              <th className="text-right">Rows</th>
              <th className="text-right">Values</th>
              <th className="text-right">Nulls</th>
              <th>First complete</th>
              <th>Built</th>
            </tr>
          </thead>
          <tbody>
            {data.sets.map((s) => (
              <tr key={s.id} className={newest && s.id === newest.id ? 'font-medium' : 'opacity-70'}>
                <td>{s.id}</td>
                <td className="font-mono text-xs">
                  {s.symbol} <span className="opacity-60">{s.sourceName} {s.timeframe}</span>
                </td>
                <td className="text-right">{s.schemaVersion}</td>
                <td className="font-mono text-xs">{s.configHash.slice(0, 10)}</td>
                <td className="text-right">{s.columnCount}</td>
                <td className="text-right">{s.rowCount.toLocaleString()}</td>
                <td className="text-right">{s.valueCount.toLocaleString()}</td>
                <td className="text-right">{s.nullCount.toLocaleString()}</td>
                <td className="text-xs">{s.firstCompleteDate?.slice(0, 10) ?? '—'}</td>
                <td className="text-xs">{new Date(s.builtAt).toLocaleString()}</td>
              </tr>
            ))}
            {data.sets.length === 0 && (
              <tr>
                <td colSpan={10} className="text-center text-base-content/60">
                  No sets built at {tf}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </StageShell>
  )
}
