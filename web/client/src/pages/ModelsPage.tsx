import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type TrainedModel } from '../lib/api'
import { useApiData } from '../lib/useApiData'
import { tipClass } from '../components/FieldLabel'

function pct(n: number | null): string {
  if (n == null || !Number.isFinite(n)) return '—'
  return `${n.toFixed(1)}%`
}

function ms(n: number | null): string {
  if (n == null || !Number.isFinite(n)) return '—'
  if (n < 1000) return `${n} ms`
  return `${(n / 1000).toFixed(1)} s`
}

function epochRange(from: number | null, to: number | null): string {
  if (from == null || to == null) return '—'
  const f = new Date(from * 1000).toLocaleDateString()
  const t = new Date(to * 1000).toLocaleDateString()
  return f === t ? f : `${f} → ${t}`
}

/** Format the compact version id ("20260511T134522.123Z") as e.g. "05-11 13:45". */
function formatVersion(v: string | null): string {
  if (!v) return 'legacy'
  const m = v.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})\.\d{3}Z$/)
  if (!m) return v
  const [, , mo, d, hh, mm] = m
  return `${mo}-${d} ${hh}:${mm}`
}

/** Stable React key per row: a (cacheKey, versionId) pair uniquely identifies a row. */
function rowKey(m: TrainedModel): string {
  return `${m.cacheKey ?? 'unkeyed'}:${m.versionId ?? 'legacy'}`
}

/**
 * First 12 characters of the cache key, or a placeholder.
 *
 * This used to be `m.cacheKey.slice(0, 12)` on a field the type says is a
 * string. It reached the page as undefined anyway — the API was reading the
 * loader's metadata with the wrong key names — and the throw blanked the entire
 * app. The API is fixed; this stays because a model directory is a file on
 * disk, and the page should degrade to a dash rather than a white screen.
 */
function shortKey(cacheKey: string | null): string {
  if (!cacheKey) return '—'
  return `${cacheKey.slice(0, 12)}…`
}

export default function ModelsPage() {
  const [expanded, setExpanded] = useState<string | null>(null)

  const { data, error } = useApiData<{ items: TrainedModel[]; modelsDir: string }>(
    (signal) => api.models(signal),
    [],
  )

  return (
    <section>
      <div className="flex flex-wrap items-baseline gap-3 mb-4">
        <h1 className="text-2xl font-semibold">Trained models</h1>
        {data && (
          <span className="text-sm text-base-content/60 font-mono">
            {data.modelsDir}
          </span>
        )}
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {!data && !error && <span className="loading loading-spinner" />}

      {data && data.items.length === 0 && (
        <div className="alert">
          <div>
            <p>No trained models on disk.</p>
            <p className="text-sm text-base-content/60 mt-1">
              Run a backtest with a persistable strategy (e.g.{' '}
              <code className="font-mono">nn-feedforward</code>) to populate the cache, then refresh.
            </p>
          </div>
        </div>
      )}

      {data && data.items.length > 0 && (
        <div className="overflow-x-auto bg-base-200 rounded-box">
          <table className="table table-sm">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>Instrument</th>
                <th>Source</th>
                <th>Timeframe</th>
                <th>Training range</th>
                <th className="text-right">Bars</th>
                <th className="text-right">Val. acc.</th>
                <th className="text-right">Train time</th>
                <th>Created</th>
                <th>Version</th>
                <th className="text-right">Used in</th>
                <th>Cache key</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((m) => {
                const key = rowKey(m)
                const isOpen = expanded === key
                return (
                  <Fragment key={key}>
                    <tr className="hover:bg-base-300">
                      <td className="font-mono">{m.strategyName ?? '—'}</td>
                      <td className="font-mono">{m.instrumentSymbol ?? `#${m.instrumentId ?? '?'}`}</td>
                      <td className="font-mono">{m.sourceName ?? `#${m.sourceId ?? '?'}`}</td>
                      <td>{m.timeframe ?? '—'}</td>
                      <td className="whitespace-nowrap text-base-content/80">
                        {epochRange(m.trainingFromEpochSec, m.trainingToEpochSec)}
                      </td>
                      <td className="text-right tabular-nums">
                        {m.trainingBarCount?.toLocaleString() ?? '—'}
                      </td>
                      <td className="text-right tabular-nums">{pct(m.validationAccuracyPct)}</td>
                      <td className="text-right tabular-nums">{ms(m.trainingDurationMs)}</td>
                      <td className="whitespace-nowrap">
                        {m.createdAt ? new Date(m.createdAt).toLocaleString() : '—'}
                      </td>
                      <td className="whitespace-nowrap font-mono text-xs" title={m.versionId ?? 'pre-versioning entry'}>
                        {m.versionId ? formatVersion(m.versionId) : <span className="text-base-content/40">legacy</span>}
                      </td>
                      <td className="text-right tabular-nums">
                        {m.backtestCount > 0 ? (
                          <Link
                            to={`/results?strategy=${encodeURIComponent(m.strategyName ?? '')}`}
                            className={`link link-hover ${tipClass}`}
                            data-tip="How many saved backtests reused this cached model. Opens the Results page filtered to this strategy."
                          >
                            {m.backtestCount}
                          </Link>
                        ) : (
                          <span className="text-base-content/40">0</span>
                        )}
                      </td>
                      <td>
                        <span
                          className="font-mono text-xs text-base-content/60"
                          title={m.cacheKey ?? 'no cache key in metadata.json'}
                        >
                          {shortKey(m.cacheKey)}
                        </span>
                      </td>
                      <td>
                        <span
                          className={tipClass}
                          data-tip="Show the hyperparameters this model was trained with. They are part of the cache key, so a different set trains a separate model."
                        >
                          <button
                            className="btn btn-xs btn-ghost"
                            onClick={() => setExpanded(isOpen ? null : key)}
                          >
                            {isOpen ? 'hide' : 'params'}
                          </button>
                        </span>
                      </td>
                    </tr>
                    {isOpen && (
                      <tr className="bg-base-300/40">
                        <td colSpan={13}>
                          <div className="p-3 space-y-3">
                            <div className="flex flex-wrap gap-2">
                              <span className="badge badge-outline font-mono">
                                DL4J {m.dl4jVersion ?? '?'}
                              </span>
                              <span className="badge badge-outline font-mono">
                                key {m.cacheKey ?? '—'}
                              </span>
                            </div>
                            <div>
                              <div className="text-xs uppercase tracking-wide text-base-content/60 mb-1">
                                Hyperparameters
                              </div>
                              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-x-6 gap-y-1 text-sm">
                                {Object.entries(m.hyperparams).map(([k, v]) => (
                                  <div key={k} className="flex justify-between gap-2">
                                    <span className="font-mono text-base-content/70">{k}</span>
                                    <span className="font-mono tabular-nums">{v}</span>
                                  </div>
                                ))}
                              </div>
                            </div>
                            <div className="text-xs text-base-content/50 font-mono">
                              {m.diskPath}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
