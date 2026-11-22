import { useCallback, useEffect, useState } from 'react'
import BenchmarkTable from '../../components/BenchmarkTable'
import {
  api,
  type EngineSummary,
  type TrainedModel,
  type WalkforwardRun,
  type WalkforwardFold,
} from '../../lib/api'
import {
  NETWORK, engineBenchmarkRows, engineRecordLine, engineTitle, foldEngine, isNetworkAlone,
  runEnginesNote,
} from '../../lib/engines'
import { useApiData } from '../../lib/useApiData'
import { useTimeframe } from '../../lib/useTimeframe'
import {
  accuracyMeaning, executionLine, isLeverageOnly, lineageLine, naiveFlag, newestFinished, num, pp,
  stageSummary,
} from '../../lib/walkforwardView'
import StartWalkforward from '../../components/StartWalkforward'
import { usePipelineStatus } from '../../lib/pipelineStatusContext'
import { mergeState } from '../../lib/stageStatus'
import StageShell from './StageShell'
import LossCurves from '../../components/LossCurves'
import { progressLine } from '../../lib/epochs'
import { labelLine, runLabel, runName, runTarget, scoreLabel } from '../../lib/targets'

type Loaded = { items: TrainedModel[], modelsDir: string }

/**
 * Stage 5 — training.
 *
 * The design wants this tab followed as a run happens: current fold, epoch, the
 * loss curve updating, and validation-fold results against buy-and-hold as they
 * land. The last of those is now real — the runner writes each fold to
 * `walkforward_folds` as it finishes, Java serves them, and the tables below are
 * that record. Every epoch is recorded too (G11): the run list says where a
 * running run is and how long it has left, an open run draws each fold's loss
 * curve, and both re-read every ten seconds while a run is going.
 *
 * The cost attribution is the reason this is worth a table rather than a
 * sentence. A fold's gap to buy-and-hold splits into the price of being out of
 * the market, the mechanical cost of trading, and the model's own timing — and
 * only the last of those is the model. Without the split, "the model lost 20
 * points" and "the model is absent and the structure lost 20 points" look the
 * same.
 *
 * A run of several engines (B9) has one fold row per engine, an engine summary
 * and one strategy line per engine beside the benchmarks, which are the fold's
 * and counted once. Its line in the run list is its first engine's.
 */
export default function TrainingStage() {
  const [tf] = useTimeframe()
  const { status } = usePipelineStatus()
  const [includeTest, setIncludeTest] = useState(false)
  const [openRun, setOpenRun] = useState<number | null>(null)
  // Bumped when a run started here opens its row or ends, so the table re-reads.
  const [runsKey, setRunsKey] = useState(0)
  const bumpRuns = useCallback(() => setRunsKey((k) => k + 1), [])

  // G11: while a run is going, the list and an open run re-read on a clock.
  const [tick, setTick] = useState(0)
  const { data: runsData, error: runsError, loading: runsLoading } = useApiData(
    (signal) => api.walkforwardRuns({ timeframe: tf, includeTest, limit: 50 }, signal),
    [tf, includeTest, runsKey, tick],
  )
  const anyRunning = runsData?.items.some((r) => r.inProgress) ?? false
  useEffect(() => {
    if (!anyRunning) return
    const t = setInterval(() => setTick((x) => x + 1), 10_000)
    return () => clearInterval(t)
  }, [anyRunning])

  const { data: detail, loading: detailLoading } = useApiData(
    (signal) => (openRun === null
      ? Promise.resolve(null)
      : api.walkforwardRun(openRun, signal)),
    [openRun, anyRunning ? tick : 0],
  )
  // Strategy 2 (G5): the fold table swaps Strategy 1's attribution for margin figures.
  const margin = detail?.run.execution?.kind === 'margin'
  // B9: a run of several engines has one fold row per engine, so the fold
  // table names the engine. A run of the network alone reads as before.
  const engineColumn = !!detail && !isNetworkAlone(detail.run)
  // G49: a run's accuracy figures are an accuracy, a hit rate, or a return
  // forecast's mean absolute error, by what it predicted.
  const score = scoreLabel(detail?.run.target)

  const { data, error } = useApiData<Loaded>((signal) => api.models(tf, signal), [tf])

  if (error) return <div className="alert alert-error">{error}</div>
  if (!data) return <span className="loading loading-spinner" />

  const models = data.items
  const runs = runsData?.items ?? []

  const newest = newestFinished(runs)
  const header = stageSummary(runs, models.length, tf)
  // Each strategy's newest run, checked for whether a rerun would read the same
  // inputs and parameters (G12); older runs of a strategy are superseded.
  const staleness = new Map((status?.training.runs ?? []).map((r) => [r.runId, r]))
  const merged = mergeState(header.state, status?.training)
  const reasons = [
    ...(status?.training.reasons ?? []),
    ...(status?.training.runs ?? []).filter((r) => r.state === 'stale').flatMap((r) =>
      r.reasons.map((x) => ({
        ...x,
        text: `run #${r.runId}${r.strategy ? ` (${r.strategy} v${r.version})` : ''}: ${x.text}`,
      }))),
  ]

  return (
    <StageShell
      state={merged.state}
      alsoStale={merged.alsoStale}
      reasons={reasons}
      summary={header.summary}
      detail={
        <>
          {header.detail}
          {newest && (
            <>
              {' '}
              The verdict covers one of this stage&apos;s gates — beating buy-and-hold on the
              validation folds. The others are recorded epoch by epoch (G11): each fold says
              whether its training loss fell, and a NaN or infinite loss stops a run and says
              where.
            </>
          )}
        </>
      }
    >
      <StartWalkforward onRunChange={bumpRuns} />

      <div className="flex items-center justify-between flex-wrap gap-3 mt-6 mb-2">
        <h2 className="text-lg font-semibold">Walk-forward runs</h2>
        <label className="label cursor-pointer gap-2">
          <input
            type="checkbox"
            className="checkbox checkbox-sm"
            checked={includeTest}
            onChange={(e) => setIncludeTest(e.target.checked)}
          />
          <span
            className="label-text text-sm tooltip tooltip-left"
            data-tip={'The holdout may be spent once, to confirm a variant already chosen on '
              + 'the validation folds. Comparing variants on it turns it into training data.'}
          >
            include holdout runs
          </span>
        </label>
      </div>

      {runsError && <div className="alert alert-error mb-4">{runsError}</div>}
      {runsLoading && <span className="loading loading-spinner loading-sm" />}

      <div className="overflow-x-auto bg-base-200 rounded-box mb-2">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Run</th>
              <th>Instrument</th>
              <th>Objective</th>
              <th>Windows</th>
              <th className="text-right">Folds won</th>
              <th className="text-right">Mean excess</th>
              <th className="text-right">Worst DD</th>
              <th className="text-right">Trades</th>
              {/* Only `timing` is the model; the other two are the cost of the
                  structure around it. */}
              <th className="text-right">Drift</th>
              <th className="text-right">Comm</th>
              <th
                className="text-right tooltip tooltip-bottom"
                data-tip="Tax on realized gains, when the run was scored after tax"
              >
                Tax
              </th>
              <th className="text-right">Timing</th>
              <th>State</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className={r.inProgress ? 'opacity-70' : ''}>
                <td className="text-xs">
                  <span className="font-mono">#{r.id}</span>
                  {runName(r) && <span className="ml-2">{runName(r)}</span>}
                  {r.strategy && (
                    <div className="opacity-70" title={`Saved strategy id ${r.strategy.id}`}>
                      {r.strategy.name}
                      {r.strategy.version !== null && ` v${r.strategy.version}`}
                    </div>
                  )}
                  <RunEngines run={r} />
                  {r.isTest && (
                    <span
                      className="badge badge-xs badge-warning ml-2 tooltip"
                      data-tip="This run spent the holdout"
                    >
                      holdout
                    </span>
                  )}
                  {r.recalculatedFrom != null && (
                    <span
                      className="badge badge-xs badge-info ml-2 tooltip tooltip-right"
                      data-tip={`Scored from run #${r.recalculatedFrom}'s stored signals under `
                        + 'this strategy version\'s settings. Nothing was retrained (D9).'}
                    >
                      from #{r.recalculatedFrom}
                    </span>
                  )}
                  {r.execution?.kind === 'margin' && (
                    <span
                      className="badge badge-xs badge-secondary ml-2 tooltip tooltip-right"
                      data-tip={executionLine(r) ?? ''}
                    >
                      Strategy 2
                    </span>
                  )}
                </td>
                <td className="font-mono text-xs">{r.symbol}</td>
                <td className="text-xs">
                  {r.objective}
                  {r.loss !== r.objective && (
                    <span className="opacity-60" title="What the network trained on">
                      {` / ${r.loss}`}
                    </span>
                  )}
                  {/* G49: named only when it is not the 3-class label, as every
                      run before G49 was. */}
                  {runTarget(r) !== 'class' && (
                    <div
                      className="opacity-70 tooltip tooltip-right text-left"
                      data-tip={`Predicts the ${runTarget(r)}; scored by `
                        + `${scoreLabel(r.target).header.toLowerCase()} against `
                        + `${scoreLabel(r.target).baselineWords}`}
                    >
                      {runTarget(r)} target
                    </div>
                  )}
                </td>
                <td className="font-mono text-xs whitespace-nowrap">
                  {r.trainStart}→{r.firstVal}..{r.lastVal}
                  {r.window?.kind === 'sliding' && (
                    <div className="text-base-content/60">
                      sliding {r.window.trainYears}y, step {r.window.stepYears}y
                    </div>
                  )}
                </td>
                <td className="text-right">
                  {r.folds === 0 ? '—' : `${r.foldsWon}/${r.folds}`}
                </td>
                <td className={`text-right font-mono ${
                  r.meanExcess == null ? '' : r.meanExcess > 0 ? 'text-success' : 'text-error'}`}
                >
                  {pp(r.meanExcess)}
                </td>
                <td className="text-right font-mono">{num(r.worstStratDrawdown, 1)}</td>
                <td className="text-right">{r.folds === 0 ? '—' : r.totalTrades}</td>
                <td className="text-right font-mono text-xs">
                  {r.execution?.kind === 'margin' ? <NotStrategy1 /> : pp(r.meanDriftTax)}
                </td>
                <td className="text-right font-mono text-xs">{pp(r.meanCommissions)}</td>
                <td
                  className="text-right font-mono text-xs"
                  title={r.taxRate === null
                    ? 'Scored before tax'
                    : `${r.taxRate}% on realized gains, scoring only`}
                >
                  {r.taxRate === null ? 'off' : pp(r.meanGainsTax)}
                </td>
                <td className={`text-right font-mono text-xs ${
                  r.execution?.kind === 'margin' || r.meanTiming == null ? ''
                    : r.meanTiming > 0 ? 'text-success' : 'text-error'}`}
                >
                  {r.execution?.kind === 'margin' ? <NotStrategy1 /> : pp(r.meanTiming)}
                </td>
                <td>
                  {r.failedReason
                    ? (
                      <span className="badge badge-sm badge-error tooltip tooltip-left" data-tip={r.failedReason}>
                        failed
                      </span>
                    )
                    : r.inProgress
                      ? (
                        <>
                          <span className="badge badge-sm badge-info">running</span>
                          {r.progress && (
                            <div className="text-xs text-base-content/70 mt-1">
                              {progressLine(r.progress, (e) => engineTitle(e))}
                            </div>
                          )}
                        </>
                      )
                      : r.beatsHoldOnReturn === true
                        ? (
                          <>
                            <span className="badge badge-sm badge-success">beat hold</span>
                            {naiveFlag(r) && (
                              <span
                                className="badge badge-sm badge-error ml-1 tooltip tooltip-left"
                                data-tip={naiveFlag(r) ?? ''}
                              >
                                below naive
                              </span>
                            )}
                            {isLeverageOnly(r) && (
                              <span
                                className="badge badge-sm badge-warning ml-1 tooltip tooltip-left"
                                data-tip="Always long on margin, with no model, earned more: the excess is the borrowing, not the timing"
                              >
                                leverage, not timing
                              </span>
                            )}
                          </>
                        )
                        : r.beatsHoldOnReturn === false
                          ? <span className="badge badge-sm badge-error">lost to hold</span>
                          : <span className="badge badge-sm badge-ghost">not decided</span>}
                  {staleness.get(r.id)?.state === 'stale' && (
                    <span
                      className="badge badge-sm badge-warning ml-1 tooltip tooltip-left"
                      data-tip={staleness.get(r.id)!.reasons.map((x) => x.text).join('; ')}
                    >
                      stale
                    </span>
                  )}
                </td>
                <td>
                  <button
                    type="button"
                    className="btn btn-ghost btn-xs"
                    disabled={r.folds === 0}
                    onClick={() => setOpenRun(openRun === r.id ? null : r.id)}
                  >
                    {openRun === r.id ? 'hide' : 'folds'}
                  </button>
                </td>
              </tr>
            ))}
            {runs.length === 0 && !runsLoading && (
              <tr>
                <td colSpan={14} className="text-center text-base-content/60">
                  No walk-forward runs recorded at {tf}
                  {!includeTest && ' (holdout runs are hidden)'}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="text-sm text-base-content/60 mb-6">
        Folds won, mean excess, worst drawdown and the attribution are computed from the
        stored folds on every read, never saved beside the run — so they cannot disagree
        with the folds they came from. <strong>Only <span className="font-mono">Timing</span> is
          the model:</strong> drift is the price of being out of the market at all and
        commissions are mechanical, so a run can lose heavily with its timing term near zero.
        Drift and timing describe Strategy 1&apos;s long-or-out rule; a Strategy 2 run is never
        out of the market, so it shows its margin interest and calls in its folds instead.
      </p>

      {openRun !== null && (
        <div className="mb-6">
          <h3 className="text-base font-semibold mb-2">
            Folds of run #{openRun}
            {detailLoading && <span className="loading loading-spinner loading-xs ml-2" />}
          </h3>
          {detail && (
            <>
              <RunProvenance run={detail.run} />
              {engineColumn && detail.engineSummaries && (
                <EngineSummaries run={detail.run} summaries={detail.engineSummaries} />
              )}
              <p className="text-xs text-base-content/70 mb-1">
                Beside the benchmarks, a fold&apos;s mean under the run&apos;s own costs and tax
                (worst drawdown of any fold){engineColumn && ', one strategy line per engine'}:
              </p>
              <BenchmarkTable
                rows={engineBenchmarkRows(detail.folds)} unit="trading days"
                tradesNote="Position changes a fold, every row alike: each buy and each sale, the last at the fold's end."
              />
              <div className="overflow-x-auto bg-base-200 rounded-box">
                <table className="table table-sm">
                  <thead>
                    <tr>
                      <th className="text-right">Fold</th>
                      {engineColumn && <th>Engine</th>}
                      <th className="text-right">Val year</th>
                      <th>Trained on</th>
                      <th className="text-right">Rows</th>
                      <th className="text-right">Strategy</th>
                      <th className="text-right">Buy &amp; hold</th>
                      <th className="text-right">Excess</th>
                      <th className="text-right">Sharpe</th>
                      <th className="text-right">Max DD</th>
                      <th className="text-right">Trades</th>
                      <th className="text-right">Days out</th>
                      <th className="text-right">Drift</th>
                      <th className="text-right">Comm</th>
                      <th className="text-right">Tax</th>
                      <th className="text-right">Timing</th>
                      {margin && (
                        <>
                          <th
                            className="text-right tooltip tooltip-left"
                            data-tip="Bars held long on margin / in PSQ"
                          >
                            Long / PSQ
                          </th>
                          <th
                            className="text-right tooltip tooltip-left"
                            data-tip="Strategy return minus the same fold always long on margin, with no model: what the timing added over borrowing alone"
                          >
                            vs always long
                          </th>
                          <th className="text-right">Interest</th>
                          <th
                            className="text-right tooltip tooltip-left"
                            data-tip="Margin calls / cuts back to the 50% ceiling"
                          >
                            Calls / trims
                          </th>
                        </>
                      )}
                      <th
                        className="text-right tooltip tooltip-left"
                        data-tip={accuracyMeaning(detail.run.loss, detail.run.target).header}
                      >
                        {score.header}
                      </th>
                      <th
                        className="text-right tooltip tooltip-left"
                        data-tip="Early stopping: the epoch kept / epochs trained. The network's only."
                      >
                        Epochs
                      </th>
                      <th
                        className="text-right tooltip tooltip-left"
                        data-tip="Training loss, first epoch to last (G11); ✓ when it fell. Recorded since 2026-11-16."
                      >
                        Loss
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.folds.map((f: WalkforwardFold, i) => {
                      const excess = f.stratReturn - f.holdReturn
                      const engine = foldEngine(f)
                      // The fold's own columns repeat on its second engine's
                      // row on; dimmed, so each fold reads as one group.
                      const again = i > 0 && detail.folds[i - 1].fold === f.fold
                        ? 'opacity-40' : ''
                      return (
                        <tr key={`${f.fold}:${engine}`}>
                          <td className={`text-right ${again}`}>{f.fold}</td>
                          {engineColumn && (
                            <td className="text-xs whitespace-nowrap">{engineTitle(engine)}</td>
                          )}
                          <td className={`text-right font-mono ${again}`}>{f.valYear}</td>
                          <td className={`font-mono text-xs whitespace-nowrap ${again}`}>
                            {f.trainFrom} .. {f.trainTo}
                          </td>
                          <td className={`text-right text-xs ${again}`}>
                            {f.nTrain.toLocaleString()} / {f.nVal.toLocaleString()}
                          </td>
                          <td className="text-right font-mono">{num(f.stratReturn)}</td>
                          <td className="text-right font-mono">{num(f.holdReturn)}</td>
                          <td className={`text-right font-mono ${
                            excess > 0 ? 'text-success' : 'text-error'}`}
                          >
                            {pp(excess)}
                          </td>
                          <td className="text-right font-mono text-xs">
                            {num(f.stratSharpe)}
                            <span className="opacity-60">{` / ${num(f.holdSharpe)}`}</span>
                          </td>
                          <td className="text-right font-mono text-xs">
                            {num(f.stratMaxdd, 1)}
                            <span className="opacity-60">{` / ${num(f.holdMaxdd, 1)}`}</span>
                          </td>
                          <td className="text-right">{f.trades}</td>
                          <td className="text-right">{margin ? <NotStrategy1 /> : f.daysOut}</td>
                          <td className="text-right font-mono text-xs">
                            {margin ? <NotStrategy1 /> : pp(f.driftTax)}
                          </td>
                          <td
                            className="text-right font-mono text-xs"
                            title={`Buy-and-hold paid ${pp(f.holdCommissions)} (B6)`}
                          >
                            {pp(f.commissions)}
                          </td>
                          <td
                            className="text-right font-mono text-xs"
                            title={detail.run.taxRate === null ? undefined
                              : `Buy-and-hold paid ${pp(f.holdGainsTax)} on its final sale (D8)`}
                          >
                            {detail.run.taxRate === null ? 'off' : pp(f.gainsTax)}
                          </td>
                          <td className={`text-right font-mono text-xs ${
                            margin ? '' : f.timing > 0 ? 'text-success' : 'text-error'}`}
                          >
                            {margin ? <NotStrategy1 /> : pp(f.timing)}
                          </td>
                          {margin && (
                            <>
                              <td className="text-right text-xs">
                                {f.daysLong} / {f.daysHedged}
                              </td>
                              <td
                                className={`text-right font-mono text-xs ${
                                  f.leveredReturn == null ? ''
                                    : f.stratReturn > f.leveredReturn ? 'text-success' : 'text-error'}`}
                                title={f.leveredReturn == null
                                  ? undefined
                                  : `Always long on margin: ${num(f.leveredReturn)}%`}
                              >
                                {f.leveredReturn == null ? '—' : pp(f.stratReturn - f.leveredReturn)}
                              </td>
                              <td className="text-right font-mono text-xs">{pp(f.marginInterest)}</td>
                              <td
                                className={`text-right text-xs ${
                                  f.marginCalls || f.coreAtRisk ? 'text-error' : ''}`}
                                title={f.coreAtRisk ? `Core at risk on ${f.coreAtRisk} day(s)` : undefined}
                              >
                                {f.marginCalls} / {f.trims}
                                {!!f.coreAtRisk && ' · core at risk'}
                              </td>
                            </>
                          )}
                          <td className="text-right text-xs">
                            {f.accuracy == null
                              ? <span className="opacity-60" title="Money-trained: no class to be right about">—</span>
                              : score.format(f.accuracy)}
                            {f.baselineAcc != null && (
                              <span
                                className="opacity-60"
                                title={accuracyMeaning(detail.run.loss, detail.run.target).baseline}
                              >
                                {` / ${score.format(f.baselineAcc)}`}
                              </span>
                            )}
                          </td>
                          <td
                            className="text-right font-mono text-xs"
                            title={engine !== NETWORK
                              ? `${engineTitle(engine)} has no epochs: they are the network's`
                              : f.epochsRun === null
                                ? 'Trained a fixed number of epochs'
                                : `Kept epoch ${f.bestEpoch}; stopped after ${f.epochsRun}`}
                          >
                            {engine !== NETWORK || f.epochsRun === null
                              ? '—'
                              : `${f.bestEpoch}/${f.epochsRun}`}
                          </td>
                          <td
                            className={`text-right font-mono text-xs ${f.loss == null ? ''
                              : f.loss.decreased ? 'text-success' : 'text-error'}`}
                            title={f.loss == null ? 'No epochs recorded: run before G11'
                              : `Lowest ${f.loss.lowest.toFixed(4)} over ${f.loss.epochs} epochs`}
                          >
                            {f.loss == null ? '—'
                              : `${f.loss.first.toFixed(3)} → ${f.loss.last.toFixed(3)} ${f.loss.decreased ? '✓' : '✗'}`}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <p className="text-sm text-base-content/60 mt-2">
                Paired columns show the strategy against buy-and-hold over the same window.
                A negative <span className="font-mono">Timing</span> means the days the model
                sat out were <em>better</em> than average — it picked wrong. The three
                attribution terms approximate Excess rather than summing to it exactly,
                because they are additive and the returns are geometric.
              </p>
              {detail.epochs && <LossCurves curves={detail.epochs} folds={detail.folds} />}
              {(detail.run.hyperparams || detail.run.engines) && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-sm">
                    Hyperparameters{detail.run.engines ? ', engines' : ''} and configuration
                  </summary>
                  <div className="mt-2 text-xs font-mono bg-base-200 rounded-box p-3">
                    <div>
                      feature set {detail.run.featureSetId ?? '—'} · {detail.run.inputWidth} inputs
                      {' · '}cost {detail.run.costBps} bps · parked in {detail.run.park}
                      {detail.run.threshold != null && ` · threshold ${detail.run.threshold}`}
                    </div>
                    {detail.run.hyperparams && (
                      <div className="mt-1">
                        {Object.entries(detail.run.hyperparams)
                          .map(([k, v]) => `${k}=${v}`).join('  ')}
                      </div>
                    )}
                    {/* B9: what each engine was trained with, so a rerun can match it. */}
                    {detail.run.engines?.map((e) => (
                      <div key={e.engine} className="mt-1 break-words">{engineRecordLine(e)}</div>
                    ))}
                  </div>
                </details>
              )}
            </>
          )}
        </div>
      )}

      <h2 className="text-lg font-semibold mb-2">Trained models on disk</h2>
      <div className="overflow-x-auto bg-base-200 rounded-box">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Model</th>
              <th>Strategy</th>
              <th>Instrument</th>
              <th>Timeframe</th>
              <th className="text-right">Bars</th>
              <th className="text-right">Val. acc</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.versionId ?? m.cacheKey}>
                <td className="font-mono text-xs">{(m.versionId ?? m.cacheKey ?? '').slice(0, 16)}</td>
                <td className="text-xs">{m.strategyName ?? '—'}</td>
                <td className="font-mono text-xs">{m.instrumentSymbol ?? '—'}</td>
                <td className="text-xs">
                  {m.timeframe
                    ? <span className="font-mono">{m.timeframe}</span>
                    : <span className="opacity-60" title="The loader does not record it">not recorded</span>}
                </td>
                <td className="text-right">{m.trainingBarCount?.toLocaleString() ?? '—'}</td>
                <td className="text-right">
                  {m.validationAccuracyPct != null ? `${m.validationAccuracyPct.toFixed(1)}%` : '—'}
                </td>
                <td className="text-xs">
                  {m.createdAt ? new Date(m.createdAt).toLocaleString() : '—'}
                </td>
              </tr>
            ))}
            {models.length === 0 && (
              <tr>
                <td colSpan={7} className="text-center text-base-content/60">
                  Nothing trained at {tf}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="text-sm text-base-content/60 mt-3">
        A model on disk is not linked to the run that produced it — the loader does not record
        the run id with the artifact, so these two tables cannot be joined yet.
      </p>
      <p className="text-sm text-base-content/60 mt-1 font-mono">{data.modelsDir}</p>
    </StageShell>
  )
}

/**
 * The engines a run trained, under its name in the run list (B9). The line's
 * figures are the first engine's, which the tooltip says; nothing is shown for
 * a run of the network alone.
 */
function RunEngines({ run }: { run: WalkforwardRun }) {
  const note = runEnginesNote(run)
  if (!note) return null
  return (
    <div className="opacity-70 tooltip tooltip-right text-left" data-tip={note.tip}>
      {note.text}
    </div>
  )
}

/**
 * One line per engine over the run's folds (B9), each scored by the same profit
 * step, with the badges the run list uses. Library, version and seed are the
 * run's record of what trained it.
 */
function EngineSummaries({ run, summaries }: { run: WalkforwardRun, summaries: EngineSummary[] }) {
  // G49: an accuracy, a hit rate or a mean absolute error, by the run's target.
  const score = scoreLabel(run.target)
  const meaning = accuracyMeaning(run.loss, run.target)
  return (
    <>
      <p className="text-xs text-base-content/70 mb-1">
        Each engine over the same folds, rows and labels, scored with the same costs and tax.
        The run&apos;s line in the list is the first engine&apos;s.
      </p>
      <div className="overflow-x-auto bg-base-200 rounded-box mb-4">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Engine</th>
              <th>Library</th>
              <th className="text-right">Seed</th>
              <th className="text-right">Folds won</th>
              <th className="text-right">Mean excess</th>
              <th className="text-right">Mean return</th>
              <th className="text-right">Sharpe</th>
              <th className="text-right">Worst DD</th>
              <th className="text-right">Trades</th>
              <th className="text-right tooltip tooltip-left" data-tip={meaning.header}>
                {score.header}
              </th>
              <th>State</th>
            </tr>
          </thead>
          <tbody>
            {summaries.map((s) => {
              const rec = run.engines?.find((e) => e.engine === s.engine)
              const flag = naiveFlag({ ...s, loss: run.loss, target: run.target })
              return (
                <tr key={s.engine}>
                  <td className="text-xs whitespace-nowrap font-medium">{engineTitle(s.engine)}</td>
                  <td className="text-xs whitespace-nowrap">
                    {rec ? `${rec.library} ${rec.version ?? '(version not recorded)'}` : '—'}
                  </td>
                  <td className="text-right font-mono text-xs">{rec?.seed ?? '—'}</td>
                  <td className="text-right">{s.folds === 0 ? '—' : `${s.foldsWon}/${s.folds}`}</td>
                  <td className={`text-right font-mono ${
                    s.meanExcess == null ? '' : s.meanExcess > 0 ? 'text-success' : 'text-error'}`}
                  >
                    {pp(s.meanExcess)}
                  </td>
                  <td className="text-right font-mono text-xs">{num(s.meanStratReturn)}</td>
                  <td className="text-right font-mono text-xs">{num(s.meanStratSharpe)}</td>
                  <td className="text-right font-mono text-xs">{num(s.worstStratDrawdown, 1)}</td>
                  <td className="text-right">{s.folds === 0 ? '—' : s.totalTrades}</td>
                  <td className="text-right text-xs">
                    {score.format(s.meanAccuracy)}
                    {s.meanBaselineAcc != null && (
                      <span className="opacity-60" title={meaning.baseline}>
                        {` / ${score.format(s.meanBaselineAcc)}`}
                      </span>
                    )}
                  </td>
                  <td>
                    {run.inProgress
                      ? <span className="badge badge-sm badge-info">running</span>
                      : s.beatsHoldOnReturn === true
                        ? (
                          <>
                            <span className="badge badge-sm badge-success">beat hold</span>
                            {flag && (
                              <span
                                className="badge badge-sm badge-error ml-1 tooltip tooltip-left"
                                data-tip={flag}
                              >
                                below naive
                              </span>
                            )}
                          </>
                        )
                        : s.beatsHoldOnReturn === false
                          ? <span className="badge badge-sm badge-error">lost to hold</span>
                          : <span className="badge badge-sm badge-ghost">not decided</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

/** A Strategy 1 attribution term on a Strategy 2 run, where it does not apply. */
function NotStrategy1() {
  return (
    <span className="opacity-50" title="Strategy 1's long-or-out attribution; a Strategy 2 run is never out">
      —
    </span>
  )
}

/**
 * What a run trained on: the strategy behind it, its columns, and how much of
 * the train window those columns really covered. A partial column is kept but
 * moves the first train bar, which is easy to miss from the fold table alone.
 */
function RunProvenance({ run }: { run: WalkforwardRun }) {
  const s = run.strategy
  if (!s && !run.columns) {
    return (
      <p className="text-xs text-base-content/60 mb-2">
        Launched from flags before the column list was recorded: it read every column of
        feature set {run.featureSetId ?? '(deleted)'}.
      </p>
    )
  }
  return (
    <div className="text-xs mb-2 space-y-1">
      {s
        ? (
          <p>
            Strategy <strong>{s.name}</strong>{s.version !== null && ` v${s.version}`}{' '}
            <span className="opacity-60">(id {s.id})</span>
            {s.trainBars !== null && s.trainBarsUsed !== null && (
              <>
                {' '}· first fold trained on {s.trainBarsUsed.toLocaleString()} of{' '}
                {s.trainBars.toLocaleString()} bars{s.trainFirst && `, from ${s.trainFirst}`}
                {s.trainBarsUsed < s.trainBars && (
                  <span className="text-warning"> (short: a partial column starts late)</span>
                )}
              </>
            )}
            {s.testEnd && <> · holdout scored to {s.testEnd}</>}
          </p>
        )
        : <p>Launched from flags, not a saved strategy.</p>}
      {executionLine(run) && <p>{executionLine(run)}</p>}
      <RunTarget run={run} />
      <p className={run.purgeBars === null ? 'text-warning' : ''}>
        {run.purgeBars === null
          ? 'Trained before the purge: the last training bars, whose targets look into the '
            + 'scored year, were fitted on, so these folds read slightly optimistic.'
          : `The last ${run.purgeBars} bars of each training window were purged: their `
            + 'targets look into the scored year.'}
      </p>
      {run.columns && (
        <p>
          {run.columns.length} column{run.columns.length === 1 ? '' : 's'}:{' '}
          <span className="font-mono">{run.columns.join(', ')}</span>
        </p>
      )}
      {s?.dropped && s.dropped.length > 0 && (
        <p className="text-warning">
          Dropped: {s.dropped.map((d) => `${d.name} (${d.why})`).join('; ')}
        </p>
      )}
      {s?.partial && s.partial.length > 0 && (
        <p className="text-warning">
          Partial: {s.partial.map((p) => `${p.name} ${p.values} of ${p.bars} bars`).join('; ')}
        </p>
      )}
      <RunLineage run={run} />
    </div>
  )
}

/**
 * What the run predicted and the thresholds that turned it into calls (G49),
 * with the score its folds carry and the naive predictor that score is held
 * to. A run from before recorded no label: it was the 3-class label.
 */
function RunTarget({ run }: { run: WalkforwardRun }) {
  const target = runTarget(run)
  const label = runLabel(run)
  const score = scoreLabel(target)
  // A money-trained model on the 3-class label makes one call, out or long (G43).
  const naive = target === 'class' && run.loss !== 'ce'
    ? 'the training majority of out or long'
    : score.baselineWords
  return (
    <p>
      Target: {label
        ? labelLine(label, target, run.hyperparams)
        : 'the 3-class label (recorded before runs kept their label and its thresholds)'}
      {' · '}scored by {score.header.toLowerCase()} against {naive}
      {score.lowerIsBetter && ', lower is better'}
    </p>
  )
}

/** G3: which inputs and code produced the run, and whether they still hold. */
function RunLineage({ run }: { run: WalkforwardRun }) {
  const { tone, text } = lineageLine(run.id, run.lineage)
  const cls = tone === 'warn' ? 'text-warning' : tone === 'muted' ? 'text-base-content/60' : ''
  return <p className={`${cls} break-words`}>{text}</p>
}
