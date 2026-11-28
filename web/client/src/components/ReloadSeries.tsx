import { useState } from 'react'
import { api, type ReloadResult } from '../lib/api'
import { hintFor } from '../lib/fieldHints'
import { reloadWrites, reloadYearLines } from '../lib/reload'
import { tipClass } from './FieldLabel'

/**
 * Reload a series from its archive, in one action: every year of it at once,
 * from the CSVs its imports archived (`POST /api/imports/reload`).
 *
 * Never writes on the first click. "Check" is the dry run: it compares every
 * archived year with the stored bars and says what a reload would put back or
 * rewrite. "Reload" appears only when there is something to write, and does
 * exactly that. A file that changed after its import is refused by the check,
 * with the reason, before anything is written.
 */
export default function ReloadSeries({
  symbol: initialSymbol,
  source: initialSource,
  timeframe: initialTimeframe,
  onReloaded,
}: {
  symbol: string
  source: string
  timeframe: string
  onReloaded: () => void
}) {
  const [symbol, setSymbol] = useState(initialSymbol)
  const [source, setSource] = useState(initialSource)
  const [timeframe, setTimeframe] = useState(initialTimeframe || 'D1')
  const [checked, setChecked] = useState<ReloadResult | null>(null)
  const [done, setDone] = useState<ReloadResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const series = { symbol: symbol.trim(), source: source.trim(), timeframe: timeframe.trim() }
  const ready = Boolean(series.symbol && series.source && series.timeframe)

  async function call(dryRun: boolean) {
    setBusy(true)
    setError(null)
    try {
      const r = await api.reloadSeries({ ...series, dryRun })
      if (dryRun) {
        setChecked(r)
        setDone(null)
      } else {
        setDone(r)
        setChecked(null)
        onReloaded()
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  function edit(set: (v: string) => void) {
    return (e: React.ChangeEvent<HTMLInputElement>) => {
      set(e.target.value)
      setChecked(null)
      setDone(null)
      setError(null)
    }
  }

  const shown = done ?? checked
  return (
    <div className="bg-base-200 rounded-box p-4 mb-4">
      <h2 className="font-medium mb-2">
        <span className={tipClass} data-tip={hintFor('imports.reload')}>
          Reload a series from its archive
        </span>
      </h2>
      <div className="flex flex-wrap items-center gap-2">
        <input className="input input-sm input-bordered w-32" placeholder="symbol" value={symbol}
          onChange={edit(setSymbol)} aria-label="Symbol to reload" />
        <input className="input input-sm input-bordered w-32" placeholder="source" value={source}
          onChange={edit(setSource)} aria-label="Source to reload" />
        <input className="input input-sm input-bordered w-20" placeholder="D1" value={timeframe}
          onChange={edit(setTimeframe)} aria-label="Timeframe to reload" />
        <span className={tipClass} data-tip={hintFor('imports.reloadCheck')}>
          <button type="button" className="btn btn-sm" disabled={!ready || busy}
            onClick={() => call(true)}>
            {busy && !checked && <span className="loading loading-spinner loading-xs" />}
            Check
          </button>
        </span>
        {checked && reloadWrites(checked) && (
          <span className={tipClass} data-tip={hintFor('imports.reloadRun')}>
            <button type="button" className="btn btn-sm btn-warning" disabled={busy}
              onClick={() => call(false)}>
              {busy && <span className="loading loading-spinner loading-xs" />}
              Reload
            </button>
          </span>
        )}
      </div>
      {error && <div className="alert alert-error text-sm mt-3 whitespace-normal">{error}</div>}
      {shown && (
        <div className="text-sm mt-3 space-y-1">
          <p>{shown.lines[0]}</p>
          {reloadYearLines(shown).map((line) => (
            <p key={line} className="font-mono text-xs">{line}</p>
          ))}
          {checked && !reloadWrites(checked) && (
            <p className="text-base-content/60">Nothing to write: the stored bars are the archive's.</p>
          )}
          {(done?.rollups ?? []).map((r) => (
            <p key={r.timeframe} className="text-xs">
              Rolled up {r.timeframe} from {r.since}: {r.rows} bar(s).
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
