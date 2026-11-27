import { useEffect, useState } from 'react'
import FeaturePreview from '../../components/FeaturePreview'
import { api, type FeatureCheck, type FeatureColumn, type FeaturesReport, type JobRow } from '../../lib/api'
import { checkColumn } from '../../lib/normalizationCheck'
import { useApiData } from '../../lib/useApiData'
import { useTimeframe } from '../../lib/useTimeframe'
import { usePipelineStatus } from '../../lib/pipelineStatusContext'
import { mergeState } from '../../lib/stageStatus'
import { stageInstrument } from '../../lib/instrument'
import StageShell, { type StageState } from './StageShell'

/** A disabled column is a recorded decision, not an absence — the badge says
 *  which decision, straight from features.toml.
 *
 *  "selectable" is `status = "selectable"`: built and stored, trained on only
 *  when a strategy ticks it (RSI, MACD, realized volatility and two ratios; the
 *  author's decision 2026-11-19). "data only" is `status = "data-only"`: stored
 *  for charts and rules, never an input; no column has it today. Both are
 *  written out, never read from a MISSING status, which was wrong twice (G9).
 *  A disabled column with no status says just that, "off". */
function stateBadge(col: FeatureColumn) {
  if (col.enabled) return { label: 'in the model', cls: 'badge-success' }
  if (!col.status) return { label: 'off', cls: 'badge-ghost' }
  switch (col.status) {
    case 'selectable': return { label: 'selectable', cls: 'badge-info' }
    case 'data-only': return { label: 'data only', cls: 'badge-info' }
    case 'blocked': return { label: 'blocked', cls: 'badge-error' }
    case 'not-imported': return { label: 'not imported', cls: 'badge-warning' }
    case 'later-paid': return { label: 'later — paid', cls: 'badge-info' }
    case 'short-history': return { label: 'short history', cls: 'badge-warning' }
    case 'undecided': return { label: 'undecided', cls: 'badge-ghost' }
    default: return { label: col.status, cls: 'badge-warning' }
  }
}

/** `symbol/source` for a check's published series, as the table prints it. */
function publishedRef(c: FeatureCheck) {
  return `${c.published.symbol}/${c.published.source}`
}

function fmtParams(params: Record<string, number | string>) {
  const entries = Object.entries(params)
  return entries.length ? entries.map(([k, v]) => `${k}=${v}`).join(', ') : '—'
}

/** Stage 2 — the feature table itself: which columns exist, how each is
 *  normalized, and which are out of the model with the reason recorded. */
export default function FeaturesStage() {
  const [tf] = useTimeframe()
  const [reload, setReload] = useState(0)
  const { status, error: statusError, reload: reloadStatus } = usePipelineStatus()
  // The sets of the instrument the pipeline is on: the status names
  // it, the one picked or the config's own, so another instrument's newer set
  // is never shown as this one's.
  const on = stageInstrument(status, statusError)
  const { data, error } = useApiData<FeaturesReport>(
    (signal) => (on ? api.features(tf, signal, on) : new Promise<FeaturesReport>(() => {})),
    [tf, reload, on?.symbol ?? '', on?.source ?? '', on === null])
  const [openNote, setOpenNote] = useState<string | null>(null)
  const [previewing, setPreviewing] = useState<string | null>(null)
  const build = useBuild(tf, on, () => { setReload((n) => n + 1); reloadStatus() })

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
  const dataOnly = off.filter((c) => c.status === 'data-only')
  const selectable = off.filter((c) => c.status === 'selectable')
  const newest = data.sets[0] ?? null
  const stale = newest ? newest.schemaVersion !== config.schemaVersion : false

  // Only LIVE checks mark a column. A check on a disabled column is skipped,
  // and a badge next to it would claim verification that never runs.
  const checks = config.checks ?? []
  const checkByFeature = new Map(checks.filter((c) => c.live).map((c) => [c.feature, c]))

  // R68: every stored column is normalized, which the check below reads off
  // its statistics. The method each got is the set's own spec, not today's file.
  const methodOf = new Map((newest?.columns ?? []).map((c) => [c.name, c.method]))
  const columnRows = [...data.columnStats].sort((a, b) => a.name.localeCompare(b.name))
    .map((s) => ({ stats: s, method: methodOf.get(s.name) ?? '—', problem: checkColumn(s) }))
  const problems = columnRows.filter((r) => r.problem)
  const own: StageState = newest === null ? 'empty' : stale ? 'stale'
    : problems.length ? 'failed' : 'passed'
  const scale = config.normalization
  // The pipeline's check adds what a schema comparison cannot see: any edit to
  // features.toml, and input series stored since the build read them.
  const merged = mergeState(own, status?.features)

  return (
    <StageShell
      state={merged.state}
      alsoStale={merged.alsoStale}
      reasons={status?.features.reasons}
      notes={status?.features.notes}
      summary={
        newest === null
          ? `No set built for ${tf}`
          : stale
            ? `Newest set is schema v${newest.schemaVersion}, config declares v${config.schemaVersion}`
            : problems.length
              ? `${problems.length} of ${columnRows.length} stored columns are not normalized`
              : `${enabled.length} input columns built and normalized, ${config.inputWidth} values per bar`
              + (selectable.length ? `, plus ${selectable.length} selectable` : '')
              + (dataOnly.length ? `, plus ${dataOnly.length} data only` : '')
      }
      detail={
        newest === null
          ? 'Build the set with the button below.'
          : stale
            ? 'Rebuild before training, or a model binds to a vector the config no longer describes.'
            : (
              <>
                Each column is stored normalized: after its method, scaled into -1..1 by the
                bars before it ({scale ? `${scale.scale}, ${scale.warmupBars} bars of warm-up` : 'expanding min-max'}),
                over the whole history, so any window cut from it later is clean and nothing is
                fitted on a training window. {' '}
                Aligned to <span className="font-mono">{config.alignment.calendarSymbol}</span>{' '}
                ({config.alignment.calendarSource}, {config.alignment.calendarTimeframe}), fill{' '}
                <span className="font-mono">{config.alignment.fill}</span> to a maximum of{' '}
                {config.alignment.maxFillDays} days. {off.length} further columns are defined
                but not default inputs{selectable.length
                  ? `; ${selectable.length} of them are built and a strategy may select them`
                  : ''}{dataOnly.length
                  ? `; ${dataOnly.length} are stored as data only`
                  : ''}.
              </>
            )
      }
    >
      <BuildPanel tf={tf} build={build} />

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
              <th>Method</th>
              <th>Params</th>
              <th className="text-right">Width</th>
              <th>State</th>
              <th>Checked</th>
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
                    {checkByFeature.has(c.name)
                      ? (
                        <span
                          className="badge badge-sm badge-outline tooltip tooltip-right"
                          data-tip={`vs ${publishedRef(checkByFeature.get(c.name)!)}, `
                            + `tolerance ${checkByFeature.get(c.name)!.tolerance}`}
                        >
                          cross-checked
                        </span>
                      )
                      : <span className="text-base-content/40">—</span>}
                  </td>
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

      {checks.length > 0 && (
        <>
          <h2 className="text-lg font-semibold mb-2">Data checks</h2>
          <p className="text-sm text-base-content/70 mb-2">
            A column computed here, against the vendor&apos;s own version of the same
            number. The design asks for these so a figure is verifiable rather than
            merely present: the yield curve is computed from its two legs, and
            FRED&apos;s published spread is kept only to disagree with. A check flags,
            it never blocks a build &mdash; the result is printed by{' '}
            <span className="font-mono">nn.feature_build</span>.
          </p>
          <div className="overflow-x-auto bg-base-200 rounded-box mb-6">
            <table className="table table-sm">
              <thead>
                <tr>
                  <th>Check</th>
                  <th>Computed column</th>
                  <th>Compared with</th>
                  <th className="text-right">Tolerance</th>
                  <th className="text-right">Warm-up</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {checks.map((c) => (
                  <tr key={c.name} className={c.live ? '' : 'opacity-60'}>
                    <td className="font-mono text-xs">{c.name}</td>
                    <td className="font-mono text-xs">{c.feature}</td>
                    <td className="font-mono text-xs">{publishedRef(c)}</td>
                    <td className="text-right font-mono text-xs">{c.tolerance}</td>
                    <td className="text-right">{c.warmupDays || '—'}</td>
                    <td>
                      <span className={`badge badge-sm ${c.live ? 'badge-success' : 'badge-ghost'}`}>
                        {c.live ? 'live' : 'skipped — column off'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {newest && columnRows.length > 0 && (
        <>
          <h2 className="text-lg font-semibold mb-2">
            Stored values of set #{newest.id}: is every column normalized?
          </h2>
          {previewing && (
            <FeaturePreview setId={newest.id} column={previewing} onClose={() => setPreviewing(null)} />
          )}
          <div className="overflow-x-auto bg-base-200 rounded-box mb-2">
            <table className="table table-sm [&_td]:px-2 [&_th]:px-2">
              <thead>
                <tr>
                  <th>Column</th>
                  <th>Method</th>
                  <th className="text-right">N</th>
                  <th className="text-right">Nulls</th>
                  <th className="text-right">Min</th>
                  <th className="text-right">Max</th>
                  <th className="text-right">Mean</th>
                  <th className="text-right">Stdev</th>
                  <th>Check</th>
                  <th><span className="sr-only">Preview</span></th>
                </tr>
              </thead>
              <tbody>
                {columnRows.map(({ stats: c, method, problem }) => (
                  <tr key={c.name} className={problem ? 'bg-error/10' : ''}>
                    <td className="font-mono">{c.name}</td>
                    <td className="font-mono text-xs">{method}</td>
                    <td className="text-right">{c.n.toLocaleString()}</td>
                    <td className="text-right">{c.nulls > 0 ? c.nulls.toLocaleString() : '—'}</td>
                    <td className="text-right font-mono text-xs">{c.min?.toFixed(2) ?? '—'}</td>
                    <td className="text-right font-mono text-xs">{c.max?.toFixed(2) ?? '—'}</td>
                    <td className="text-right font-mono text-xs">{c.mean?.toFixed(3) ?? '—'}</td>
                    <td className="text-right font-mono text-xs">{c.stdev?.toFixed(3) ?? '—'}</td>
                    <td className="text-xs">
                      {problem ? <span className="text-error">{problem}</span> : <span className="text-success">ok</span>}
                    </td>
                    <td>
                      <button
                        type="button"
                        className={`btn btn-xs ${previewing === c.name ? 'btn-primary' : 'btn-ghost'}`}
                        onClick={() => setPreviewing(previewing === c.name ? null : c.name)}
                      >
                        Preview
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-sm text-base-content/60 mb-6">
            Nulls at the start are the warm-up: a column has no value until its method's window
            and the scale's {scale?.warmupBars ?? 252} earlier bars are there, which is why it is
            stored as NULL rather than zero. A value past ±1 is a new extreme against the bars
            before it. Preview a column to see the series its method read beside the stored value.
          </p>
        </>
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

type Build = {
  job: JobRow | null
  error: string | null
  start: () => void
}

/**
 * Starts a feature build (`POST /api/features/build`, a job) and follows it on
 * the jobs table every two seconds until it ends, then calls `onDone` so the
 * page and the stage states read the new set.
 */
function useBuild(tf: string, on: { symbol?: string, source?: string } | null,
  onDone: () => void): Build {
  const [jobId, setJobId] = useState<number | null>(null)
  const [job, setJob] = useState<JobRow | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (jobId === null) return
    let cancelled = false
    const tick = async () => {
      try {
        const row = await api.job(jobId)
        if (cancelled) return
        setJob(row)
        if (row.status === 'queued' || row.status === 'running') {
          setTimeout(tick, 2000)
        } else {
          setJobId(null)
          if (row.status === 'done') onDone()
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      }
    }
    void tick()
    return () => { cancelled = true }
    // onDone is a fresh closure each render; the job id is what decides.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  return {
    job,
    error,
    start: () => {
      setError(null)
      setJob(null)
      api.buildFeatures(tf, on ?? {})
        .then((r) => setJobId(r.jobId))
        .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
    },
  }
}

function BuildPanel({ tf, build }: { tf: string, build: Build }) {
  const running = build.job !== null && (build.job.status === 'queued' || build.job.status === 'running')
  const output = build.job?.result?.output ?? []
  return (
    <div className="bg-base-200 rounded-box p-3 mb-6">
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className="btn btn-sm btn-primary" disabled={running} onClick={build.start}>
          {running && <span className="loading loading-spinner loading-xs" />}
          Build features for {tf}
        </button>
        <span className="text-sm text-base-content/70">
          Builds every column over the whole history and replaces the set built from the same
          config. Takes about half a minute for daily bars.
        </span>
      </div>
      {build.error && <div className="alert alert-error text-sm mt-2">{build.error}</div>}
      {build.job && (
        <div className="mt-2 text-sm">
          <span className={`badge badge-sm ${build.job.status === 'done' ? 'badge-success'
            : build.job.status === 'failed' ? 'badge-error' : 'badge-info'}`}>
            job {build.job.id}: {build.job.status}
          </span>
          {build.job.error && <span className="text-error ml-2">{build.job.error}</span>}
          {output.length > 0 && (
            <pre className="text-xs bg-base-300 rounded p-2 mt-2 max-h-48 overflow-auto">
              {output.slice(-12).join('\n')}
            </pre>
          )}
        </div>
      )}
    </div>
  )
}
