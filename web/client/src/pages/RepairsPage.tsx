import { useState } from 'react'
import { api, type MissingMethod, type Repair, type RepairChoice, type RepairLog } from '../lib/api'
import { useApiData } from '../lib/useApiData'
import { tipClass } from '../components/FieldLabel'
import { CHOICE_LABEL, METHOD_LABEL, choicesOf, fmt, missingBars, seriesLine, whyHeld } from '../lib/repairs'

/**
 * The repair log on one screen (G76; R89): every repair with its original
 * value, its candidates, the one applied, and who, when and why.
 *
 * A repair never edits the raw candle. It is a row here, laid over the raw bar
 * for everything that reads the adjusted candles (checks, features, training,
 * backtests), and taking it back shows the raw value again. A row is applied
 * by the rule only when a second source and the calculated value agree;
 * otherwise it waits here for a choice, and every choice needs a reason.
 */
export default function RepairsPage() {
  const [status, setStatus] = useState<'held' | 'applied' | ''>('held')
  const [symbol, setSymbol] = useState('')
  const [reload, setReload] = useState(0)
  const [reason, setReason] = useState('')
  const [hand, setHand] = useState<Record<number, string>>({})
  const [message, setMessage] = useState<{ kind: 'ok' | 'error', text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  // The missing-bar panel: which series, and how to calculate its bars.
  const [mSymbol, setMSymbol] = useState('')
  const [mSource, setMSource] = useState('')
  const [method, setMethod] = useState<MissingMethod>('carry')
  const [mDay, setMDay] = useState('')
  const [relSymbol, setRelSymbol] = useState('')
  const [relSource, setRelSource] = useState('')

  const { data, error } = useApiData<RepairLog>(
    (signal) => api.repairs({ status, symbol, limit: 500 }, signal),
    [status, symbol, reload],
  )

  async function act(run: () => Promise<string>) {
    setBusy(true)
    setMessage(null)
    try {
      setMessage({ kind: 'ok', text: await run() })
      setReload((n) => n + 1)
    } catch (e) {
      setMessage({ kind: 'error', text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
    }
  }

  const needReason = reason.trim().length === 0
  const choose = (which: RepairChoice, r: Repair) => act(async () => {
    const value = which === 'hand' ? Number(hand[r.id]) : undefined
    const done = await api.chooseRepair({ which, reason, ids: [r.id], value })
    return done.applied
      ? `Applied the ${CHOICE_LABEL[which]} value to ${r.symbol} ${r.barTime.slice(0, 10)} ${r.field}.`
      : 'Nothing was applied: the row has no such candidate.'
  })
  const chooseSeries = (which: RepairChoice, s: RepairLog['series'][number]) => act(async () => {
    const done = await api.chooseRepair({ which, reason, symbol: s.symbol, source: s.source,
      timeframe: s.timeframe })
    return `Applied the ${CHOICE_LABEL[which]} value to ${done.applied} held row(s) of ${s.symbol}/${s.source}.`
  })
  const revert = (r: Repair) => act(async () => {
    await api.revertRepair({ id: r.id, reason })
    return `Took back the repair of ${r.symbol} ${r.barTime.slice(0, 10)} ${r.field}: the raw value shows again.`
  })
  const propose = (f: RepairLog['flagged'][number]) => act(async () => {
    const out = await api.proposeRepairs({ symbol: f.symbol, source: f.source, timeframe: f.timeframe })
    return `${f.symbol}/${f.source}: ${out.proposed} repair row(s) for ${out.bars} flagged bar(s); `
      + `${out.applied} applied by the rule, ${out.held} held for a choice.`
  })

  const findMissing = () => act(async () => {
    const out = await api.findMissingBars({ symbol: mSymbol, source: mSource })
    setSymbol(mSymbol)
    return `${mSymbol}/${mSource}: ${out.missing} missing bar(s) on trading days; `
      + `${out.applied} filled by the rule, ${out.held} held for a choice.`
  })
  const recalculate = () => act(async () => {
    const out = await api.recalculateMissing({ symbol: mSymbol, source: mSource, method,
      day: mDay || undefined,
      ...(method === 'related' ? { relatedSymbol: relSymbol, relatedSource: relSource } : {}) })
    const skipped = out.skipped.map((x) => `${x.day}: ${x.why}`).join('; ')
    return `Recalculated ${out.recalculated} bar(s) as ${METHOD_LABEL[method]}.`
      + (skipped ? ` Not recalculated: ${skipped}.` : '')
  })
  const fillBar = (which: RepairChoice, b: ReturnType<typeof missingBars>[number]) => act(async () => {
    const done = await api.chooseRepair({ which, reason, ids: b.ids })
    return `Filled ${b.symbol}/${b.source} ${b.day} from the ${CHOICE_LABEL[which]} bar `
      + `(${done.applied} field(s)).`
  })
  const bars = data ? missingBars(data.items) : []
  const canFind = mSymbol.trim() !== '' && mSource.trim() !== ''

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-1">Repairs</h1>
      <p className="text-sm text-base-content/70 mb-4 max-w-4xl">
        A bar an import found an error on can be repaired here. The stored candle is never
        changed: a repair is laid over it for the feature build, training and backtests, and can
        be taken back. A value is applied by itself only when a second source and the calculated
        value agree; otherwise it waits for a choice below.
      </p>

      <div className="flex flex-wrap items-end gap-3 mb-4">
        <label className="form-control">
          <span className="label-text text-xs">Show</span>
          <select
            className="select select-sm select-bordered"
            value={status}
            onChange={(e) => setStatus(e.target.value as typeof status)}
          >
            <option value="held">held for a choice</option>
            <option value="applied">applied</option>
            <option value="">all</option>
          </select>
        </label>
        <label className="form-control">
          <span className="label-text text-xs">Symbol</span>
          <input
            className="input input-sm input-bordered w-32"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.trim())}
            placeholder="all"
          />
        </label>
        <label className="form-control grow max-w-xl">
          <span className={`label-text text-xs ${tipClass}`} data-tip="Recorded on every row a choice touches, with who made it and when. Nothing is applied or taken back without one.">
            Reason for the next choice (required)
          </span>
          <input
            className="input input-sm input-bordered"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. the vendor's range leaves out the settlement price"
          />
        </label>
      </div>

      {message && (
        <div className={`alert ${message.kind === 'ok' ? 'alert-success' : 'alert-error'} text-sm mb-3`}>
          {message.text}
        </div>
      )}
      {error && <div className="alert alert-error">{error}</div>}
      {!data && !error && <span className="loading loading-spinner" />}

      {data && (
        <>
          <div className="grid gap-3 md:grid-cols-2 mb-4">
            <div className="bg-base-200 rounded-box p-3" data-testid="repair-series">
              <h2 className="font-semibold text-sm mb-1">Series with repairs</h2>
              {data.series.length === 0 && (
                <p className="text-sm text-base-content/60">None yet.</p>
              )}
              {data.series.map((s) => (
                <div key={`${s.symbol}/${s.source}/${s.timeframe}`} className="text-sm py-1">
                  <span className="font-mono">{s.symbol}/{s.source} {s.timeframe}</span>
                  <span className="text-base-content/70"> · {seriesLine(s)}</span>
                  {s.heldWithCalculated > 0 && (
                    <button
                      className="btn btn-xs btn-warning ml-2"
                      disabled={busy || needReason}
                      onClick={() => chooseSeries('calculated', s)}
                      title={needReason ? 'Type a reason first' : undefined}
                    >
                      apply the calculated value to all {s.heldWithCalculated}
                    </button>
                  )}
                </div>
              ))}
            </div>
            <div className="bg-base-200 rounded-box p-3" data-testid="repair-flagged">
              <h2 className="font-semibold text-sm mb-1">Series with flagged rows</h2>
              {data.flagged.length === 0 && (
                <p className="text-sm text-base-content/60">No series has a flagged row.</p>
              )}
              {data.flagged.map((f) => (
                <div key={`${f.symbol}/${f.source}/${f.timeframe}`} className="text-sm py-1">
                  <span className="font-mono">{f.symbol}/{f.source} {f.timeframe}</span>
                  <span className="text-base-content/70">
                    {' '}· {f.open} open, {f.accepted} accepted
                    {f.missing ? `, ${f.missing} missing bar(s)` : ''} · {f.from} to {f.to}
                  </span>
                  <button
                    className="btn btn-xs btn-outline ml-2"
                    disabled={busy}
                    onClick={() => propose(f)}
                  >
                    propose repairs
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-base-200 rounded-box p-3 mb-4" data-testid="missing-bars">
            <h2 className="font-semibold text-sm mb-1">Missing bars</h2>
            <p className="text-xs text-base-content/70 mb-2 max-w-4xl">
              A trading day with no bar, on the NYSE calendar: closed days are not gaps. A missing
              bar is a warning and holds nothing. It is filled from a second source, a calculated
              bar or by hand, whole: it appears in the series once its four prices are applied.
              A series on another exchange's calendar shows that exchange's closed days here;
              accept those as closed.
            </p>
            <div className="flex flex-wrap items-end gap-2">
              <label className="form-control">
                <span className="label-text text-xs">Symbol</span>
                <input className="input input-sm input-bordered w-28" value={mSymbol}
                  onChange={(e) => setMSymbol(e.target.value.trim().toUpperCase())} placeholder="QQQ" />
              </label>
              <label className="form-control">
                <span className="label-text text-xs">Source</span>
                <input className="input input-sm input-bordered w-28" value={mSource}
                  onChange={(e) => setMSource(e.target.value.trim())} placeholder="yahoo" />
              </label>
              <button className="btn btn-sm btn-outline" disabled={busy || !canFind} onClick={findMissing}>
                find missing bars
              </button>
              <span className="divider divider-horizontal mx-0" />
              <label className="form-control">
                <span className="label-text text-xs">Calculate by</span>
                <select className="select select-sm select-bordered" value={method}
                  onChange={(e) => setMethod(e.target.value as MissingMethod)}>
                  {(Object.keys(METHOD_LABEL) as MissingMethod[]).map((m) => (
                    <option key={m} value={m}>{METHOD_LABEL[m]}</option>
                  ))}
                </select>
              </label>
              {method === 'related' && (
                <>
                  <label className="form-control">
                    <span className="label-text text-xs">Related symbol</span>
                    <input className="input input-sm input-bordered w-28" value={relSymbol}
                      onChange={(e) => setRelSymbol(e.target.value.trim().toUpperCase())} placeholder="SPY" />
                  </label>
                  <label className="form-control">
                    <span className="label-text text-xs">Related source</span>
                    <input className="input input-sm input-bordered w-28" value={relSource}
                      onChange={(e) => setRelSource(e.target.value.trim())} placeholder="yahoo" />
                  </label>
                </>
              )}
              <label className="form-control">
                <span className="label-text text-xs">Day (empty: every held bar)</span>
                <input type="date" className="input input-sm input-bordered" value={mDay}
                  onChange={(e) => setMDay(e.target.value)} />
              </label>
              <button
                className="btn btn-sm btn-outline"
                disabled={busy || !canFind || (method === 'related' && (!relSymbol || !relSource))}
                onClick={recalculate}
              >
                recalculate
              </button>
            </div>
            {bars.length > 0 && (
              <div className="mt-3 flex flex-col gap-1">
                {bars.map((b) => (
                  <div key={b.key} className="text-sm flex flex-wrap items-center gap-2">
                    <span className="font-mono">{b.symbol}/{b.source} {b.timeframe} {b.day}</span>
                    {b.choices.map((c) => (
                      <button key={c} className="btn btn-xs btn-warning"
                        disabled={busy || needReason} onClick={() => fillBar(c, b)}
                        title={needReason ? 'Type a reason first' : undefined}>
                        fill from the {CHOICE_LABEL[c]} bar
                      </button>
                    ))}
                    {b.choices.length === 0 && (
                      <span className="text-xs text-base-content/60">
                        no whole candidate bar: recalculate, or enter each field by hand below
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="overflow-x-auto bg-base-200 rounded-box">
            <table className="table table-sm" data-testid="repair-log">
              <thead>
                <tr>
                  <th>Series</th>
                  <th>Bar</th>
                  <th>Field</th>
                  <th className="text-right">Original</th>
                  <th className="text-right">Second source</th>
                  <th className="text-right">Calculated</th>
                  <th className="text-right">Hand-entered</th>
                  <th className="text-right">Applied</th>
                  <th>Who, when, why</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((r) => (
                  <tr key={r.id} className="hover:bg-base-300 align-top">
                    <td className="font-mono whitespace-nowrap">{r.symbol}/{r.source} {r.timeframe}</td>
                    <td className="whitespace-nowrap">{r.barTime.slice(0, r.timeframe === 'D1' ? 10 : 16)}</td>
                    <td>
                      <span className={tipClass} data-tip={r.problem}>
                        <span className="badge badge-sm badge-ghost cursor-help">{r.field}</span>
                      </span>
                    </td>
                    <td className="text-right tabular-nums">{fmt(r.original)}</td>
                    <td className="text-right tabular-nums">
                      {fmt(r.secondSource)}
                      {r.secondSourceName && (
                        <div className="text-xs text-base-content/60">{r.secondSourceName}</div>
                      )}
                    </td>
                    <td className="text-right tabular-nums">
                      {r.calculated === null ? '—' : (
                        <span className={tipClass} data-tip={r.calculatedHow ?? ''}>
                          <span className="cursor-help">{fmt(r.calculated)}</span>
                        </span>
                      )}
                    </td>
                    <td className="text-right tabular-nums">{fmt(r.handEntered)}</td>
                    <td className="text-right tabular-nums">
                      {r.status === 'applied' ? (
                        <>
                          <span className="font-semibold">{fmt(r.applied)}</span>
                          <div className="text-xs text-base-content/60">
                            {r.appliedFrom ? CHOICE_LABEL[r.appliedFrom] : ''}
                          </div>
                        </>
                      ) : <span className="badge badge-sm badge-warning">held</span>}
                    </td>
                    <td className="text-xs max-w-xs">
                      {r.decidedBy ? (
                        <>
                          <div>{r.decidedBy}, {r.decidedAt ? new Date(r.decidedAt).toLocaleString() : ''}</div>
                          <div className="text-base-content/70">{r.reason}</div>
                        </>
                      ) : <span className="text-base-content/60">{whyHeld(r, data.tolerances)}</span>}
                    </td>
                    <td className="whitespace-nowrap">
                      {r.status === 'held' ? (
                        <div className="flex flex-wrap items-center gap-1">
                          {choicesOf(r).filter((c) => c !== 'hand').map((c) => (
                            <button
                              key={c} className="btn btn-xs btn-outline"
                              disabled={busy || needReason} onClick={() => choose(c, r)}
                              title={needReason ? 'Type a reason first' : undefined}
                            >
                              use {CHOICE_LABEL[c]}
                            </button>
                          ))}
                          <input
                            className="input input-xs input-bordered w-24" placeholder="value"
                            inputMode="decimal" value={hand[r.id] ?? ''}
                            onChange={(e) => setHand({ ...hand, [r.id]: e.target.value })}
                          />
                          <button
                            className="btn btn-xs btn-outline"
                            disabled={busy || needReason || !Number.isFinite(Number(hand[r.id]))
                              || (hand[r.id] ?? '').trim() === ''}
                            onClick={() => choose('hand', r)}
                          >
                            use this
                          </button>
                        </div>
                      ) : (
                        <button
                          className="btn btn-xs btn-ghost" disabled={busy || needReason}
                          onClick={() => revert(r)}
                          title={needReason ? 'Type a reason first' : undefined}
                        >
                          take back
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {data.items.length === 0 && (
                  <tr>
                    <td colSpan={10} className="text-center text-base-content/60">
                      No repairs match.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {data.items.length >= 500 && (
            <p className="text-xs text-base-content/60 mt-2">
              Showing the first 500 rows, newest bar first. Narrow by symbol to see the rest.
            </p>
          )}
        </>
      )}
    </section>
  )
}
