import { useMemo, useState } from 'react'
import {
  api,
  type ImportDeletePreview,
  type ImportRecord,
  type Paginated,
} from '../lib/api'
import { useApiData } from '../lib/useApiData'
import Pagination from '../components/Pagination'
import { tipClass } from '../components/FieldLabel'
import UploadCard from '../components/UploadCard'

const LIMIT = 25

type SortKey = 'imported' | 'source' | 'instrument' | 'timeframe' | 'archive' | 'file' | 'rows'
type SortDir = 'asc' | 'desc'

// Table columns. `key: null` = not sortable (hash order is meaningless). The
// default direction when first clicking a column: newest/largest-first for the
// date and row-count columns, A→Z for the text ones.
const COLUMNS: { key: SortKey | null; label: string; align?: 'right' }[] = [
  { key: 'imported', label: 'Imported' },
  { key: 'source', label: 'Source' },
  { key: 'instrument', label: 'Instrument' },
  { key: 'timeframe', label: 'Timeframe' },
  { key: 'archive', label: 'Archive path' },
  { key: 'file', label: 'File (uploaded)' },
  { key: null, label: 'Hash' },
  { key: 'rows', label: 'Rows', align: 'right' },
  { key: null, label: '' },
]
/**
 * Hint for a sortable column header. The superseded-highlight caveat only makes
 * sense on the other columns — on Imported itself it would read as a warning
 * about the ordering you are already in, so it is left off there.
 */
function sortHint(label: string, key: SortKey): string {
  const base = `Sort by ${label.toLowerCase()}. Click again to reverse.`
  return key === 'imported'
    ? `${base} This is the default ordering, and the only one under which the superseded highlight holds.`
    : `${base} Sorting by this turns off the superseded highlight, which only holds under newest-first.`
}

const DEFAULT_DIR: Record<SortKey, SortDir> = {
  imported: 'desc', rows: 'desc',
  source: 'asc', instrument: 'asc', timeframe: 'asc', archive: 'asc', file: 'asc',
}

export default function ImportsPage() {
  const [offset, setOffset] = useState(0)
  const [source, setSource] = useState('')
  const [instrument, setInstrument] = useState('')
  const [sort, setSort] = useState<SortKey>('imported')
  const [dir, setDir] = useState<SortDir>('desc')
  const [reloadKey, setReloadKey] = useState(0)

  const { data, error } = useApiData<Paginated<ImportRecord>>(
    (signal) => api.imports({ limit: LIMIT, offset, source, instrument, sort, dir }, signal),
    [offset, source, instrument, sort, dir, reloadKey],
  )

  function applyFilters(s: string, i: string) {
    setOffset(0)
    setSource(s)
    setInstrument(i)
  }

  // Clicking a header sorts by it (server-side, across all pages); clicking the
  // active column again flips direction. Any sort change resets to page 1.
  function toggleSort(key: SortKey) {
    setOffset(0)
    if (key === sort) {
      setDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSort(key)
      setDir(DEFAULT_DIR[key])
    }
  }

  // Mark older audit rows that share an archive_path with a newer row as
  // "superseded" — visual cue so the user can see at a glance which file
  // currently occupies each archive slot. This assumes the default newest-first
  // ordering (first occurrence per archivePath = the active one), so we skip it
  // once the user re-sorts, where that assumption no longer holds.
  const supersededIds = useMemo(() => {
    const empty = new Set<string>()
    if (!data || sort !== 'imported' || dir !== 'desc') return empty
    const seen = new Set<string>()
    const superseded = new Set<string>()
    for (const r of data.items) {
      if (r.archivePath) {
        if (seen.has(r.archivePath)) superseded.add(r.id)
        seen.add(r.archivePath)
      }
    }
    return superseded
  }, [data, sort, dir])

  function refreshHistory() {
    setReloadKey((k) => k + 1)
  }

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-4">Imports</h1>

      <UploadCard onSuccess={refreshHistory} />

      <div className="flex flex-wrap gap-2 mb-4">
        <span className={tipClass} data-tip="Show only imports filed under this source. Matched exactly.">
          <input
            className="input input-sm input-bordered"
            placeholder="filter by source (e.g. yahoo)"
            value={source}
            onChange={(e) => applyFilters(e.target.value, instrument)}
          />
        </span>
        <span className={tipClass} data-tip="Show only imports for this symbol. Matched exactly.">
          <input
            className="input input-sm input-bordered"
            placeholder="filter by instrument (e.g. AAPL)"
            value={instrument}
            onChange={(e) => applyFilters(source, e.target.value)}
          />
        </span>
        {(source || instrument) && (
          <span className={tipClass} data-tip="Clears both filters and returns to the full import log.">
            <button className="btn btn-sm btn-ghost" onClick={() => applyFilters('', '')}>
              clear
            </button>
          </span>
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
                  {COLUMNS.map((c) => (
                    <th key={c.label} className={c.align === 'right' ? 'text-right' : undefined}>
                      {c.key ? (
                        <span
                          className={tipClass}
                          data-tip={sortHint(c.label, c.key!)}
                        >
                          <button
                            type="button"
                            className="inline-flex items-center gap-1 hover:text-primary"
                            onClick={() => toggleSort(c.key!)}
                            aria-sort={sort === c.key ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}
                          >
                            {c.label}
                            <span className="opacity-60 w-2 text-xs">
                              {sort === c.key ? (dir === 'asc' ? '▲' : '▼') : ''}
                            </span>
                          </button>
                        </span>
                      ) : c.label}
                    </th>
                  ))}
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
                      <td className="text-right">
                        <DeleteImportButton record={r} onDeleted={refreshHistory} />
                      </td>
                    </tr>
                  )
                })}
                {data.items.length === 0 && (
                  <tr><td colSpan={9} className="text-center text-base-content/60">No imports match.</td></tr>
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

/**
 * Undo one import: delete the candles it wrote, keep the archived CSV.
 *
 * Never deletes on the first click. The loader's `dry_run` mode answers the
 * only question worth asking first — how many candles are actually in that
 * window — because the row count an import *recorded* and what is in the table
 * *now* can differ once a later import has overwritten part of the year. So the
 * confirmation states the real number, and says the archive survives, which is
 * what makes this reversible: re-import the same file and the rows come back.
 */
function DeleteImportButton({
  record,
  onDeleted,
}: {
  record: ImportRecord
  onDeleted: () => void
}) {
  const [preview, setPreview] = useState<ImportDeletePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function openPreview() {
    setBusy(true)
    setError(null)
    try {
      setPreview(await api.previewDeleteImport(record.id))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      await api.deleteImport(record.id)
      setPreview(null)
      onDeleted()
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  function cancel() {
    setPreview(null)
    setError(null)
  }

  return (
    <>
      <button
        type="button"
        className={`btn btn-ghost btn-xs text-error ${tipClass}`}
        onClick={openPreview}
        disabled={busy}
        data-tip="Deletes the candles this import wrote, within its slice year, and its audit row. The archived CSV is kept, so you can re-import it. You get a count to confirm first."
        aria-label={`Undo import ${record.id}`}
      >
        {busy && !preview ? <span className="loading loading-spinner loading-xs" /> : 'Undo'}
      </button>

      {error && !preview && (
        <div className="text-xs text-error mt-1 whitespace-normal">{error}</div>
      )}

      {preview && (
        <dialog className="modal modal-open">
          <div className="modal-box">
            <h3 className="font-semibold text-lg">Undo this import?</h3>
            <p className="py-2 text-sm">
              Deletes{' '}
              <span className="font-semibold tabular-nums">
                {preview.candlesWouldDelete.toLocaleString()}
              </span>{' '}
              candle{preview.candlesWouldDelete === 1 ? '' : 's'} for{' '}
              <span className="font-mono">{preview.symbol}</span> ·{' '}
              <span className="font-mono">{preview.source}</span> ·{' '}
              <span className="font-mono">{preview.timeframe}</span> in{' '}
              <span className="font-mono">{preview.year}</span>, and removes the audit row.
            </p>

            {preview.candlesWouldDelete !== preview.rowsRecorded && (
              <p className="text-xs text-base-content/60 pb-2">
                The import recorded {preview.rowsRecorded.toLocaleString()} rows; the window holds{' '}
                {preview.candlesInWindow.toLocaleString()} now — a later import for the same year
                has overwritten part of it.
              </p>
            )}

            <div className="alert alert-info text-sm">
              <span>
                The archived CSV{' '}
                <span className="font-mono text-xs">{preview.archivePath}</span> is kept, so this
                can be undone by re-importing it.
              </span>
            </div>

            {error && <div className="alert alert-error text-sm mt-3">{error}</div>}

            <div className="modal-action">
              <button type="button" className="btn btn-sm" onClick={cancel} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="btn btn-sm btn-error" onClick={confirm} disabled={busy}>
                {busy && <span className="loading loading-spinner loading-xs" />}
                Delete candles
              </button>
            </div>
          </div>
          <form method="dialog" className="modal-backdrop">
            <button type="button" onClick={cancel}>close</button>
          </form>
        </dialog>
      )}
    </>
  )
}
