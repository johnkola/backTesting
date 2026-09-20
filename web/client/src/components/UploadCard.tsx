import { useRef, useState } from 'react'
import {
  api,
  type CohesionCategory,
  type CohesionReport,
  type SliceOutcome,
  type SlicePreview,
  type UploadImportResponse,
} from '../lib/api'
import FieldLabel, { tipClass } from './FieldLabel'

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN1'] as const
const TYPES = ['STOCK', 'FOREX', 'CRYPTO', 'INDEX', 'COMMODITY'] as const

type UploadState =
  | { kind: 'idle' }
  | { kind: 'submitting' }
  | { kind: 'success'; result: UploadImportResponse }
  | { kind: 'error'; message: string }

/**
 * The CSV upload form, with its per-slice outcome and cohesion feedback.
 *
 * Lives here rather than on the Imports page because two flows begin with
 * getting candles in: browsing the import log, and training a model. One form
 * in one place means the archive rules, the force semantics and the cohesion
 * panel cannot drift between them.
 *
 * `onSuccess` fires only on an accepted upload, so callers can refresh whatever
 * they show alongside it.
 */
export default function UploadCard({ onSuccess }: { onSuccess: () => void }) {
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
            <FieldLabel
              text="CSV file"
              className="text-xs"
              hint="The candle file to import. Its header must read Date,Open,High,Low,Close,Volume."
            />
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
            <FieldLabel
              text="Symbol"
              className="text-xs"
              hint="Ticker these candles belong to. Uppercased as you type."
            />
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
            <FieldLabel
              text="Type"
              className="text-xs"
              hint="Instrument class the symbol is filed under: STOCK, FOREX, CRYPTO, INDEX or COMMODITY."
            />
            <select
              className="select select-sm select-bordered"
              value={type}
              onChange={(e) => setType(e.target.value as typeof TYPES[number])}
            >
              {TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>

          <label className="form-control">
            <FieldLabel
              text="Timeframe"
              className="text-xs"
              hint="Candle size the rows actually are. It is not detected from the file — say what you are importing."
            />
            <select
              className="select select-sm select-bordered"
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value as typeof TIMEFRAMES[number])}
            >
              {TIMEFRAMES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>

          <label className="form-control">
            <FieldLabel
              text="Source"
              className="text-xs"
              hint="Label for where the data came from, such as yahoo or a broker export. Empty files it under default."
            />
            <input
              type="text"
              className="input input-sm input-bordered"
              placeholder="e.g. yahoo"
              value={source}
              onChange={(e) => setSource(e.target.value.trim())}
            />
          </label>

          <div className="md:col-span-6 flex flex-wrap gap-3 items-center">
            <label
              className={`label cursor-pointer gap-2 ${tipClass}`}
              data-tip="Overwrite year slices that are already archived with different content. Without it a clash returns 409 and nothing is written."
            >
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
