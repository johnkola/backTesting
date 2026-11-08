import { useState } from 'react'
import { api, type FileVersion } from '../lib/api'
import { useApiData } from '../lib/useApiData'
import { tipClass } from './FieldLabel'
import Pagination from './Pagination'
import {
  CHANGE_KIND_BADGE, CHANGE_KIND_HINT, CHANGE_KIND_LABEL, diffSummary, exampleDates, isFix,
} from '../lib/fileVersions'

const LIMIT = 10

/**
 * Every file loaded for a series, newest first, with what kind of change each was.
 *
 * The import log below this lists year slices; this lists files. It answers
 * "which file is loaded, and did a later one fix history or only extend it?",
 * which the slices cannot: a provider's revision and a refreshed download both
 * rewrite the current year, but only the revision changes bars already stored.
 *
 * Filters are the import log's, so both views show the same series.
 */
export default function FileVersions(
  { source, instrument, reloadKey }: { source: string, instrument: string, reloadKey: number },
) {
  const [offset, setOffset] = useState(0)
  const [open, setOpen] = useState<string | null>(null)
  const [prevFilter, setPrevFilter] = useState(`${source}|${instrument}`)
  // Back to page 1 when the filters change, without an effect.
  if (prevFilter !== `${source}|${instrument}`) {
    setPrevFilter(`${source}|${instrument}`)
    setOffset(0)
  }

  const { data, error } = useApiData(
    (signal) => api.importFiles({ limit: LIMIT, offset, source, instrument }, signal),
    [offset, source, instrument, reloadKey],
  )

  if (error) return <div className="alert alert-error mb-6 text-sm">{error}</div>
  if (!data) return null

  return (
    <div className="card bg-base-200 mb-6">
      <div className="card-body p-4">
        <h2 className="card-title text-lg">File versions</h2>
        {!data.available ? (
          <p className="text-sm opacity-70">
            No version history yet: the engine has not applied the schema that adds it. Restart
            the engine, then load a file.
          </p>
        ) : data.items.length === 0 ? (
          <p className="text-sm opacity-70">
            No file versions recorded{source || instrument ? ' for this filter' : ''} yet. Every
            file loaded from now on is recorded here.
          </p>
        ) : (
          <>
            <table className="table table-xs">
              <thead>
                <tr>
                  <th>Loaded</th>
                  <th>Series</th>
                  <th>Change</th>
                  <th>File</th>
                  <th>Covers</th>
                  <th className="text-right">Rows</th>
                  <th>What changed</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((v) => (
                  <VersionRow
                    key={v.id} v={v} open={open === v.id}
                    onToggle={() => setOpen((o) => (o === v.id ? null : v.id))}
                  />
                ))}
              </tbody>
            </table>
            <Pagination total={data.total} limit={LIMIT} offset={offset} onChange={setOffset} />
          </>
        )}
      </div>
    </div>
  )
}

function VersionRow({ v, open, onToggle }: { v: FileVersion, open: boolean, onToggle: () => void }) {
  const changedYears = Object.entries(v.diff)
    .filter(([, d]) => diffSummary(d))
    .sort(([a], [b]) => Number(a) - Number(b))
  const fixYears = changedYears.filter(([, d]) => isFix(d)).map(([y]) => y)
  return (
    <>
      <tr>
        <td className="whitespace-nowrap">
          {new Date(v.importedAt).toLocaleString()}
          {v.lastSeenAt !== v.importedAt && (
            <div className={`text-xs opacity-60 ${tipClass}`} data-tip="The same bytes were uploaded again.">
              seen again {new Date(v.lastSeenAt).toLocaleDateString()}
            </div>
          )}
        </td>
        <td className="font-mono text-xs">{v.sourceName}/{v.instrumentSymbol} {v.timeframe}</td>
        <td>
          <span
            className={`badge badge-sm ${CHANGE_KIND_BADGE[v.changeKind]} ${tipClass}`}
            data-tip={CHANGE_KIND_HINT[v.changeKind]}
          >
            {CHANGE_KIND_LABEL[v.changeKind]}
          </span>
        </td>
        <td className="text-xs">
          {v.fileName}
          <div className={`font-mono opacity-60 ${tipClass}`} data-tip={`SHA-256 ${v.fileHash}`}>
            {v.fileHash.slice(0, 12)}
          </div>
        </td>
        <td className="text-xs whitespace-nowrap">
          {v.firstDate?.slice(0, 10) ?? '—'} → {v.lastDate?.slice(0, 10) ?? '—'}
        </td>
        <td className="text-right tabular-nums">{v.rowCount.toLocaleString()}</td>
        <td className="text-xs">
          {changedYears.length === 0 ? '—' : (
            <button type="button" className="link link-hover text-left" onClick={onToggle}>
              {changedYears.length} year{changedYears.length === 1 ? '' : 's'} changed
              {fixYears.length > 0 && (
                <span className="text-warning"> · history revised in {fixYears.join(', ')}</span>
              )}
            </button>
          )}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={7} className="bg-base-100">
            <table className="table table-xs">
              <thead><tr><th>Year</th><th>Difference</th><th>First changed or removed</th></tr></thead>
              <tbody>
                {changedYears.map(([year, d]) => (
                  <tr key={year}>
                    <td>{year}</td>
                    <td className={isFix(d) ? 'text-warning' : ''}>{diffSummary(d)}</td>
                    <td className="font-mono">{exampleDates(d).join(', ') || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  )
}
