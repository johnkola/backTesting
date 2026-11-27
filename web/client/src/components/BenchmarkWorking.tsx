import { api, type Candle, type ResultDetail, type RuleMonth, type RunCost, type Trade } from '../lib/api'
import { useApiData } from '../lib/useApiData'
import { barStamp } from '../lib/marketTime'
import { grouped, percent } from '../lib/numbers'
import { costText } from '../lib/runSettings'
import { isMonthly } from '../lib/benchmarks'
import {
  MAX_AT, agreesWith, barIndex, boughtAtTheFirstOpen, commissionPerSettings, equityCsv,
  fillPerSettings, instantsByAsset, reconcile, tradeLines, tradesCsv, type Fill,
} from '../lib/benchmarkCheck'

/**
 * One benchmark of a result, opened under its row of the benchmark table so
 * each of its figures can be checked by hand against the prices: how each
 * figure is made, every trade beside the bar it filled
 * at, the month ends behind Faber and dual momentum, and the trades and daily
 * equity as CSV for the two figures a spreadsheet is needed for (max drawdown
 * and Sharpe).
 *
 * The bars come from `GET /api/candles?at=`, exactly the ones the fills name;
 * the month ends from `GET /api/nn/rules`, the same code that made the calls
 * the engine traded. The page recomputes no figure: it shows the engine's and
 * the prices beside them (`lib/benchmarkCheck`).
 */
type Benchmark = {
  finalEquity: number | null
  trades: Trade[] | null
  equity: number[] | null
  metrics: { totalTrades?: number | null, tradingDays?: number | null } | null
}

function benchmarkOf(result: NonNullable<ResultDetail['result']>, name: string): Benchmark | null {
  if (name === 'strategy') {
    return { finalEquity: result.finalEquity, trades: result.trades,
      equity: result.equityHistory.map((p) => p.equity), metrics: result.metrics }
  }
  if (name === 'buy_and_hold') return result.buyAndHold ?? null
  return result.benchmarks?.find((b) => b.name === name && b.available) ?? null
}

const price = (n: number | null) => (n === null ? '—' : grouped(n, 4))
const money = (n: number | null) => (n === null ? '—' : grouped(n, 2))
const signed = (n: number | null, digits = 3) =>
  n === null ? '—' : `${n >= 0 ? '+' : '−'}${grouped(Math.abs(n), digits)}%`
const ratio = (n: number | null) => (n === null ? '—' : signed(n * 100, 2))

function download(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

/** Each asset's bars at the instants its fills name. */
function useFillBars(detail: ResultDetail, trades: readonly Trade[]) {
  const symbol = detail.instrumentSymbol
  const wanted = instantsByAsset(trades, symbol)
  const asked = [...wanted].map(([asset, at]) => [asset, at.join(',')] as const)
  const tooMany = [...wanted.values()].some((at) => at.length > MAX_AT)
  return {
    tooMany,
    ...useApiData<Map<string, Map<number, Candle>>>(async (signal) => {
      if (tooMany) return new Map()
      const out = new Map<string, Map<number, Candle>>()
      for (const [asset, at] of asked) {
        const r = await api.candles({ instrument: asset, source: detail.dataSource ?? undefined,
          timeframe: detail.timeframe, at }, signal)
        out.set(asset, barIndex(r.items))
      }
      return out
    }, [symbol, detail.dataSource, detail.timeframe, JSON.stringify(asked)]),
  }
}

/** ✓ when a figure agrees with the run's settings, ✗ with the figure they make when not. */
function Agrees({ ok, expected }: { ok: boolean | null, expected: number | null }) {
  if (ok === null) return null
  return ok
    ? <span className="text-success" title="agrees with the run's settings"> ✓</span>
    : <span className="text-error" title={`the run's settings make it ${expected}`}> ✗</span>
}

function FillCells({ f, side, timeframe, slippage }: {
  f: Fill, side: 'buy' | 'sell', timeframe: string, slippage: RunCost | null | undefined
}) {
  const expected = fillPerSettings(f, side, slippage)
  return (
    <>
      <td className="whitespace-nowrap">{barStamp(f.time, timeframe)}</td>
      <td className="text-right tabular-nums" title={`the bar's ${f.at}`}>
        {price(f.barPrice)} <span className="text-base-content/50">{f.at}</span>
      </td>
      <td className="text-right tabular-nums whitespace-nowrap">
        {price(f.fill)}<Agrees ok={agreesWith(f.fill, expected)} expected={expected} />
      </td>
      <td className="text-right tabular-nums text-base-content/70">{signed(f.slipPct)}</td>
    </>
  )
}

function MonthTable({ detail, rule }: { detail: ResultDetail, rule: 'faber' | 'dual_momentum' }) {
  const from = (detail.startDate ?? '').slice(0, 10)
  const to = (detail.endDate ?? '').slice(0, 10)
  const { data, error } = useApiData((signal) => api.ruleMonths(
    { symbol: detail.instrumentSymbol, source: detail.dataSource ?? '', from, to }, signal),
  [detail.instrumentSymbol, detail.dataSource, from, to])
  if (error) return <div className="alert alert-error text-sm">Month ends: {error}</div>
  if (!data) return <span className="loading loading-spinner loading-sm" />
  const months: RuleMonth[] = data.months ?? []
  if (!months.length) return <p className="text-base-content/60">No month ends in this window.</p>
  const faber = rule === 'faber'
  return (
    <div className="overflow-x-auto max-h-96">
      <table className="table table-xs table-pin-rows">
        <thead>
          <tr>
            <th>Month end</th>
            <th className="text-right">Close</th>
            {faber ? (
              <>
                <th className="text-right" title="The mean of this and the nine month-end closes before it">10-month average</th>
                <th>Holds next month</th>
              </>
            ) : (
              <>
                <th className="text-right" title="This close over the close 12 month ends before, less 1">12-month return</th>
                <th className="text-right">SPY close</th>
                <th className="text-right">SPY 12-month</th>
                <th className="text-right" title="What cash earned at the T-bill rate over the same 12 months">T-bills 12-month</th>
                <th>Holds next month</th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {months.map((m) => (
            <tr key={m.date} className={m.used ? '' : 'text-base-content/50'}>
              <td className="whitespace-nowrap">{m.date}</td>
              <td className="text-right tabular-nums">{price(m.close)}</td>
              {faber ? (
                <>
                  <td className="text-right tabular-nums">{price(m.average10)}</td>
                  <td>{m.faber ?? '—'}</td>
                </>
              ) : (
                <>
                  <td className="text-right tabular-nums">{ratio(m.return12)}</td>
                  <td className="text-right tabular-nums">{price(m.spyClose)}</td>
                  <td className="text-right tabular-nums">{ratio(m.spyReturn12)}</td>
                  <td className="text-right tabular-nums">{ratio(m.bills12)}</td>
                  <td>{m.dualMomentum ?? '—'}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function BenchmarkWorking({ detail, name, label }: {
  detail: ResultDetail
  /** `buy_and_hold`, `faber`, `dual_momentum` or an indicator rule's name. */
  name: string
  label: string
}) {
  const r = detail.result
  const b = r ? benchmarkOf(r, name) : null
  const trades = b?.trades ?? []
  const bars = useFillBars(detail, trades)
  if (!r || !b) return null

  const tf = detail.timeframe
  const points = r.equityHistory ?? []
  const last = points.length ? points[points.length - 1].timestamp : null
  const lines = tradeLines(trades, detail.instrumentSymbol, bars.data ?? new Map(), last)
  const settled = reconcile(r.initialCapital, lines, b.finalEquity)
  const capital = r.initialCapital
  const final = b.finalEquity
  const n = b.metrics?.totalTrades ?? trades.length
  const days = b.metrics?.tradingDays ?? null
  const s = detail.settings
  const slip = s ? costText(s.slippage, 'the price') : 'the run\'s slippage (not recorded for this result)'
  const commission = s ? costText(s.commission, 'the trade') : 'the run\'s commission (not recorded)'
  const monthly = isMonthly(name)
  const early = name !== 'buy_and_hold' && boughtAtTheFirstOpen(trades, points)
  const meanHold = lines.length
    ? lines.reduce((a, l) => a + (l.holdDays ?? 0), 0) / lines.length : null
  const slug = `${detail.instrumentSymbol}-${name}-result-${detail.id}`

  return (
    <div className="space-y-3 text-sm py-2">
      <div>
        <h3 className="font-semibold">How {label}'s figures are made</h3>
        <ul className="list-disc ml-5 space-y-1 text-base-content/80">
          <li>
            {name === 'strategy'
              ? 'Decided by the strategy at each bar\'s close and traded at the next bar\'s open; a holding still open at the end is sold at the last close.'
              : name === 'buy_and_hold'
                ? 'Bought at the first open the strategy could trade at and sold at the last close.'
                : monthly
                  ? 'Decided at each month\'s last close (the table below) and traded at the next bar\'s open.'
                  : 'Decided at each daily close at its pinned settings and traded at the next bar\'s open, so each date below is the bar after the signal.'}
            {' '}A fill is the bar's price moved by the slippage, {slip}, against the trade; commission is {commission}.
            {name === 'dual_momentum'
              ? ' Out of the market its cash earns T-bills, so its equity grows on days it holds nothing.'
              : name !== 'buy_and_hold' ? ' Out of the market its cash earns nothing.' : ''}
            {s?.taxRatePct != null
              ? ` Tax: ${s.taxRatePct}% of each realized gain net of both commissions, a loss carried to offset later gains, the holding sold at the end taxed like any other.`
              : ' Scored before tax.'}
          </li>
          <li>
            Return: final equity {money(final)} ÷ capital {money(capital)} − 1 ={' '}
            <strong>{final !== null ? percent((final / capital - 1) * 100) : '—'}</strong>.
            {settled.end !== null && (
              <>
                {' '}The final equity, followed: capital {money(capital)} + the Net column's
                total {money(settled.netTotal)} = {money(settled.end)}
                {settled.rest !== null && Math.abs(settled.rest) >= 0.005
                  ? <>, and {money(settled.rest)} earned outside a trade{name === 'dual_momentum' ? ' (T-bill interest on its cash)' : ''}.</>
                  : <> <span className="text-success">✓</span></>}
              </>
            )}
          </li>
          <li>
            Trades: the {n} round trips below.
            {days ? <> Trades / yr: {n} ÷ ({days} trading days ÷ 252) = {grouped(n / (days / 252), 1)}.</> : null}
            {meanHold !== null ? <> Avg hold: the mean of the days column, {grouped(meanHold, 0)} calendar days.</> : null}
          </li>
          <li>
            From the equity column of the CSV below (one value a bar): Max DD is the largest fall
            below the highest equity so far, the capital counting as the first high, as a percent
            of that high. Sharpe is the mean of the bar-to-bar returns over their sample standard
            deviation, times √{tf === 'D1' ? '252' : '(252 × the bars a day)'}, with no risk-free rate.
          </li>
        </ul>
      </div>

      {early && (
        <div className="alert text-sm">
          Bought at the first open the window allows: the rule was holding at the window's first
          close, from a signal that day or one before it, as it would have been. A run of this
          rule from the Run tab starts flat and waits for a signal, so its first trade can differ.
        </div>
      )}

      {monthly && (
        <div>
          <h3 className="font-semibold mb-1">The month ends it decided at</h3>
          <p className="text-xs text-base-content/60 mb-1">
            Greyed rows are before the window, listed so the first average and 12-month return can be
            recomputed from the closes. A decision applies from the next bar's open until the next month end.
          </p>
          <MonthTable detail={detail} rule={name as 'faber' | 'dual_momentum'} />
        </div>
      )}

      <div>
        <h3 className="font-semibold mb-1">Its trades, beside the bars they filled at</h3>
        {bars.tooMany && (
          <p className="text-xs text-base-content/60">Too many trades to fetch their bars in one ask; the fills are listed without them.</p>
        )}
        {bars.error && <div className="alert alert-error text-sm">Bars: {bars.error}</div>}
        <div className="overflow-x-auto max-h-96">
          <table className="table table-xs table-pin-rows">
            <thead>
              <tr>
                <th>#</th>
                <th>Asset</th>
                <th>Bought</th>
                <th className="text-right">Bar price</th>
                <th className="text-right">Fill</th>
                <th className="text-right" title="The fill against the bar's price">Slip</th>
                <th>Sold</th>
                <th className="text-right">Bar price</th>
                <th className="text-right">Fill</th>
                <th className="text-right">Slip</th>
                <th className="text-right">Qty</th>
                <th className="text-right" title="Both legs: the buy's and the sale's">Comm.</th>
                <th className="text-right" title="Quantity × (sale fill − buy fill), before commission and tax">Gain</th>
                <th className="text-right">Tax</th>
                <th className="text-right" title="The gain less both commissions and the tax: what the trade added to the account">Net</th>
                <th className="text-right">Days</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l) => (
                <tr key={l.n}>
                  <td className="tabular-nums">{l.n}</td>
                  <td className="font-mono">{l.asset}</td>
                  <FillCells f={l.buy} side="buy" timeframe={tf} slippage={s?.slippage} />
                  <FillCells f={l.sell} side="sell" timeframe={tf} slippage={s?.slippage} />
                  <td className="text-right tabular-nums">{l.quantity === null ? '—' : grouped(l.quantity, 4)}</td>
                  <td className="text-right tabular-nums whitespace-nowrap">
                    {money(l.commission)}
                    <Agrees ok={agreesWith(l.commission, commissionPerSettings(l, s?.commission))}
                      expected={commissionPerSettings(l, s?.commission)} />
                  </td>
                  <td className="text-right tabular-nums">{money(l.pnl)}</td>
                  <td className="text-right tabular-nums">{money(l.tax)}</td>
                  <td className={`text-right tabular-nums ${(l.net ?? 0) < 0 ? 'text-error' : ''}`}>{money(l.net)}</td>
                  <td className="text-right tabular-nums">{l.holdDays === null ? '—' : grouped(l.holdDays, 0)}</td>
                </tr>
              ))}
              {lines.length === 0 && (
                <tr><td colSpan={16} className="text-center text-base-content/60">No trades: it held cash throughout.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <button className="btn btn-xs" onClick={() => download(`${slug}-trades.csv`, tradesCsv(lines))}>
          Trades as CSV
        </button>
        {b.equity && (
          <button className="btn btn-xs" onClick={() => download(`${slug}-equity.csv`, equityCsv(points, b.equity ?? []))}>
            Daily equity as CSV
          </button>
        )}
      </div>
    </div>
  )
}
