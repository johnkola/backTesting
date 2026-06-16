import { useEffect, useMemo, useRef, useState } from 'react'
import {
  api,
  isAbortError,
  type CohesionCategory,
  type CohesionReport,
  type ImportRecord,
  type Paginated,
  type SliceOutcome,
  type SlicePreview,
  type UploadImportResponse,
} from '../lib/api'
import Pagination from '../components/Pagination'

const LIMIT = 25

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN1'] as const
const TYPES = ['STOCK', 'FOREX', 'CRYPTO', 'INDEX', 'COMMODITY'] as const

type UploadState =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'success'; result: UploadImportResponse }
  | { kind: 'error'; message: string }

export default function ImportsPage() {
  const [data, setData] = useState<Paginated<ImportRecord> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [source, setSource] = useState('')
  const [instrument, setInstrument] = useState('')
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    setError(null)
    const ctrl = new AbortController()
    api.imports({ limit: LIMIT, offset, source, instrument }, ctrl.signal)
      .then(setData)
      .catch((e: Error) => { if (!isAbortError(e)) setError(e.message) })
    return () => ctrl.abort()
  }, [offset, source, instrument, reloadKey])

  function applyFilters(s: string, i: string) {
    setOffset(0)
    setSource(s)
    setInstrument(i)
  }

  // Mark older audit rows that share an archive_path with a newer row as
  // "superseded" — visual cue so the user can see at a glance which file
  // currently occupies each archive slot. List is sorted DESC by importedAt,
  // so the first occurrence per archivePath is the active one.
  const supersededIds = useMemo(() => {
    if (!data) return new Set<string>()
    const seen = new Set<string>()
    const superseded = new Set<string>()
    for (const r of data.items) {
      if (r.archivePath) {
        if (seen.has(r.archivePath)) superseded.add(r.id)
        seen.add(r.archivePath)
      }
    }
    return superseded
  }, [data])

  function refreshHistory() {
    setReloadKey((k) => k + 1)
  }

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-4">Imports</h1>

      <UploadCard onSuccess={refreshHistory} />

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
                  <th>Archive path</th>
                  <th>File (uploaded)</th>
                  <th>Hash</th>
                  <th className="text-right">Rows</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => {
                  const superseded = supersededIds.has(r.id)
                  return (
                    <tr key={r.id} className={superseded ? 'opacity-50' : undefined}>
                      <td className="whitespace-nowrap">
                        {new Date(r.importedAt).toLocaleString()}
                      </td>
                      <td className="font-mono">{r.sourceName}</td>
                      <td className="font-mono">{r.instrumentSymbol}</td>
                      <td>{r.timeframe}</td>
                      <td className="font-mono text-xs">
                        {r.archivePath ?? <span className="text-base-content/40">—</span>}
                        {superseded && (
                          <span className="badge badge-warning badge-xs ml-2">superseded</span>
                        )}
                      </td>
                      <td title={r.filePath}>{r.fileName}</td>
                      <td className="font-mono text-xs">
                        {r.fileHash ? r.fileHash.slice(0, 8) : (
                          <span className="text-base-content/40">—</span>
                        )}
                      </td>
                      <td className="text-right tabular-nums">{r.rowCount.toLocaleString()}</td>
                    </tr>
                  )
                })}
                {data.items.length === 0 && (
                  <tr><td colSpan={8} className="text-center text-base-content/60">No imports match.</td></tr>
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

function UploadCard({ onSuccess }: { onSuccess: () => void }) {
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [symbol, setSymbol] = useState('')
  const [type, setType] = useState<typeof TYPES[number]>('STOCK')
  const [timeframe, setTimeframe] = useState<typeof TIMEFRAMES[number]>('D1')
  const [source, setSource] = useState('default')
  const [force, setForce] = useState(false)
  const [state, setState] = useState<UploadState>({ kind: 'idle' })

  function resetAfterSuccess() {
    setFile(null)
    setForce(false)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!file || !symbol) return
    setState({ kind: 'submitting' })
    try {
      const result = await api.uploadImport({ file, symbol, type, timeframe, source, force })
      setState({ kind: 'success', result })
      if (result.status === 'completed') {
        const anyWrite = result.imports.some((s) => s.status === 'created' || s.status === 'overwritten')
        resetAfterSuccess()
        if (anyWrite) onSuccess()
      } else if (result.status === 'conflict') {
        setForce(true)  // user can immediately resubmit to force-overwrite
      }
    } catch (err) {
      setState({ kind: 'error', message: err instanceof Error ? err.message : String(err) })
    }
  }

  return (
    <div className="card bg-base-200 mb-6">
      <div className="card-body p-4">
        <h2 className="card-title text-lg">Upload CSV</h2>
        <form onSubmit={submit} className="grid grid-cols-1 md:grid-cols-6 gap-3 items-end">
          <label className="form-control md:col-span-2">
            <span className="label-text text-xs">CSV file</span>
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,text/csv"
              className="file-input file-input-sm file-input-bordered w-full"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              required
            />
          </label>

          <label className="form-control">
            <span className="label-text text-xs">Symbol</span>
            <input
              type="text"
              className="input input-sm input-bordered"
              placeholder="e.g. QQQ"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.trim().toUpperCase())}
              required
            />
          </label>

          <label className="form-control">
            <span className="label-text text-xs">Type</span>
            <select
              className="select select-sm select-bordered"
              value={type}
              onChange={(e) => setType(e.target.value as typeof TYPES[number])}
            >
              {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>

          <label className="form-control">
            <span className="label-text text-xs">Timeframe</span>
            <select
              className="select select-sm select-bordered"
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value as typeof TIMEFRAMES[number])}
            >
              {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>

          <label className="form-control">
            <span className="label-text text-xs">Source</span>
            <input
              type="text"
              className="input input-sm input-bordered"
              placeholder="e.g. yahoo"
              value={source}
              onChange={(e) => setSource(e.target.value.trim())}
            />
          </label>

          <div className="md:col-span-6 flex flex-wrap gap-3 items-center">
            <label className="label cursor-pointer gap-2">
              <input
                type="checkbox"
                className="checkbox checkbox-sm"
                checked={force}
                onChange={(e) => setForce(e.target.checked)}
              />
              <span className="label-text">Force overwrite</span>
            </label>
            <button
              type="submit"
              className="btn btn-sm btn-primary"
              disabled={!file || !symbol || state.kind === 'submitting'}
            >
              {state.kind === 'submitting' && <span className="loading loading-spinner loading-xs" />}
              Upload
            </button>
            {state.kind === 'idle' && (
              <span className="text-xs text-base-content/60">
                Archive layout: <span className="font-mono">&lt;source&gt;/&lt;symbol&gt;/&lt;year&gt;/&lt;timeframe&gt;.csv</span>
              </span>
            )}
          </div>
        </form>

        <UploadFeedback state={state} />
      </div>
    </div>
  )
}

function UploadFeedback({ state }: { state: UploadState }) {
  if (state.kind === 'idle' || state.kind === 'submitting') return null
  if (state.kind === 'error') {
    return <div className="alert alert-error mt-3 text-sm">{state.message}</div>
  }
  const { result } = state
  if (result.status === 'compressed_chunk') {
    return (
      <div className="alert alert-warning mt-3 text-sm">
        <div>
          <strong>Postgres rejected the upsert.</strong> {result.error}
          <div className="mt-1 opacity-80">{result.hint}</div>
        </div>
      </div>
    )
  }
  if (result.status === 'completed') {
    const counts = countByStatus(result.imports)
    const allSkipped = counts.created === 0 && counts.overwritten === 0
    return (
      <div className={`alert ${allSkipped ? 'alert-info' : 'alert-success'} mt-3 text-sm`}>
        <div className="w-full">
          <div className="font-medium">
            {summarize(counts, result.imports.length)}
          </div>
          <SliceList rows={result.imports} />
          {result.cohesion && <CohesionPanel report={result.cohesion} />}
        </div>
      </div>
    )
  }
  // result.status === 'conflict'
  return (
    <div className="alert alert-warning mt-3 text-sm">
      <div className="w-full">
        <div className="font-medium">
          <strong>Conflict.</strong> {result.message}
        </div>
        <SliceList rows={result.imports} />
        <div className="mt-2 opacity-80">
          Force overwrite is now checked — click Upload again to apply the changes above.
        </div>
      </div>
    </div>
  )
}

function countByStatus(rows: SliceOutcome[]) {
  const c = { created: 0, overwritten: 0, skipped: 0 }
  for (const r of rows) c[r.status]++
  return c
}

function summarize(c: { created: number; overwritten: number; skipped: number }, total: number) {
  const parts: string[] = []
  if (c.created)     parts.push(`${c.created} created`)
  if (c.overwritten) parts.push(`${c.overwritten} overwritten`)
  if (c.skipped)     parts.push(`${c.skipped} skipped`)
  return `${total} year ${total === 1 ? 'slice' : 'slices'}: ${parts.join(' · ')}`
}

const STATUS_BADGE: Record<string, string> = {
  created:          'badge-success',
  overwritten:      'badge-success',
  skipped:          'badge-ghost',
  'would-create':   'badge-info',
  'would-overwrite':'badge-info',
  'would-skip':     'badge-ghost',
  conflict:         'badge-warning',
}

const COHESION_LABEL: Record<CohesionCategory, string> = {
  ohlc: 'OHLC',
  duplicate: 'duplicate',
  order: 'order',
  gap: 'gaps',
  outlier: 'outliers',
}
const COHESION_ORDER: CohesionCategory[] = ['ohlc', 'duplicate', 'order', 'gap', 'outlier']

/** Read-only cohesiveness advisory for a just-completed import. The data was
 *  imported regardless; this only surfaces what looks off. */
function CohesionPanel({ report }: { report: CohesionReport }) {
  if (report.ok) {
    return (
      <div className="mt-2 text-xs opacity-70">
        ✓ Data cohesion: no issues across {report.totalBars.toLocaleString()} bars.
      </div>
    )
  }
  return (
    <div className="mt-3 rounded-box border border-warning bg-base-100 p-2 text-base-content">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium text-warning">
          ⚠ Data cohesion: {report.totalIssues.toLocaleString()}{' '}
          {report.totalIssues === 1 ? 'issue' : 'issues'} over {report.totalBars.toLocaleString()} bars
        </span>
        {COHESION_ORDER.filter((c) => report.counts[c] > 0).map((c) => (
          <span key={c} className="badge badge-warning badge-sm">
            {COHESION_LABEL[c]}: {report.counts[c]}
          </span>
        ))}
      </div>
      {report.examples.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs opacity-80">
            Show example findings ({report.examples.length})
          </summary>
          <table className="table table-xs mt-1">
            <thead>
              <tr><th>Check</th><th>Timestamp</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {report.examples.map((f, i) => (
                <tr key={`${f.category}-${f.timestamp}-${i}`}>
                  <td><span className="badge badge-ghost badge-xs">{COHESION_LABEL[f.category]}</span></td>
                  <td className="font-mono text-xs whitespace-nowrap">{f.timestamp}</td>
                  <td className="text-xs">{f.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
      <div className="mt-1 text-xs opacity-60">
        Advisory only — the data was imported. Re-check stored data anytime with{' '}
        <span className="font-mono">backtest-audit</span>.
      </div>
    </div>
  )
}

function SliceList({ rows }: { rows: (SliceOutcome | SlicePreview)[] }) {
  return (
    <table className="table table-xs mt-2">
      <thead>
        <tr>
          <th>Year</th>
          <th>Status</th>
          <th>Archive path</th>
          <th className="text-right">Rows</th>
          <th>Existing</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => {
          const archivePath = 'import' in r ? r.import.archivePath
            : 'archivePath' in r ? r.archivePath : null
          const rowCount = 'import' in r ? r.import.rowCount
            : 'rowCount' in r ? r.rowCount : 0
          const existing = 'existing' in r ? r.existing : null
          return (
            <tr key={r.year}>
              <td>{r.year}</td>
              <td>
                <span className={`badge badge-sm ${STATUS_BADGE[r.status] ?? ''}`}>
                  {r.status}
                </span>
              </td>
              <td className="font-mono text-xs">{archivePath ?? '—'}</td>
              <td className="text-right tabular-nums">{rowCount.toLocaleString()}</td>
              <td className="text-xs opacity-70">
                {existing ? (
                  <>
                    {existing.fileName} · {new Date(existing.importedAt).toLocaleDateString()}
                    {existing.fileHash && (
                      <> · <span className="font-mono">{existing.fileHash.slice(0, 8)}</span></>
                    )}
                  </>
                ) : '—'}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}
