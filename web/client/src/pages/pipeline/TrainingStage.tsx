import { useCallback, useState } from 'react'
import {
  api,
  type TrainedModel,
  type WalkforwardRun,
  type WalkforwardFold,
} from '../../lib/api'
import { useApiData } from '../../lib/useApiData'
import { useTimeframe } from '../../lib/useTimeframe'
import {
  executionLine, isLeverageOnly, lineageLine, newestFinished, num, pp, stageSummary,
} from '../../lib/walkforwardView'
import StartWalkforward from '../../components/StartWalkforward'
import StageShell, { NotWired } from './StageShell'

type Loaded = { items: TrainedModel[], modelsDir: string }

/**
 * Stage 5 — training.
 *
 * The design wants this tab followed as a run happens: current fold, epoch, the
 * loss curve updating, and validation-fold results against buy-and-hold as they
 * land. The last of those is now real — the runner writes each fold to
 * `walkforward_folds` as it finishes, Java serves them, and the tables below are
 * that record. Per-epoch progress and the loss curve are still not persisted
 * anywhere, so the live view is still called out as missing rather than implied.
 *
 * The cost attribution is the reason this is worth a table rather than a
 * sentence. A fold's gap to buy-and-hold splits into the price of being out of
 * the market, the mechanical cost of trading, and the model's own timing — and
 * only the last of those is the model. Without the split, "the model lost 20
 * points" and "the model is absent and the structure lost 20 points" look the
 * same.
 */
export default function TrainingStage() {
  const [tf] = useTimeframe()
  const [includeTest, setIncludeTest] = useState(false)
  const [openRun, setOpenRun] = useState<number | null>(null)
  // Bumped when a run started here opens its row or ends, so the table re-reads.
  const [runsKey, setRunsKey] = useState(0)
  const bumpRuns = useCallback(() => setRunsKey((k) => k + 1), [])

  const { data: runsData, error: runsError, loading: runsLoading } = useApiData(
    (signal) => api.walkforwardRuns({ timeframe: tf, includeTest, limit: 50 }, signal),
    [tf, includeTest, runsKey],
  )

  const { data: detail, loading: detailLoading } = useApiData(
    (signal) => (openRun === null
      ? Promise.resolve(null)
      : api.walkforwardRun(openRun, signal)),
    [openRun],
  )
  // Strategy 2 (G5): the fold table swaps Strategy 1's attribution for margin figures.
  const margin = detail?.run.execution?.kind === 'margin'

  const { data, error } = useApiData<Loaded>((signal) => api.models(tf, signal), [tf])

  if (error) return <div className="alert alert-error">{error}</div>
  if (!data) return <span className="loading loading-spinner" />

  const models = data.items
  const runs = runsData?.items ?? []

  const newest = newestFinished(runs)
  const header = stageSummary(runs, models.length, tf)

  return (
    <StageShell
      state={header.state}
      summary={header.summary}
      detail={
        <>
          {header.detail}
          {newest && (
            <>
              {' '}
              The verdict covers one of this stage&apos;s gates — beating buy-and-hold on the
              validation folds. The others the design asks for (loss actually decreasing, no
              NaN loss) are not recorded per epoch, so they are not claimed here.
            </>
          )}
        </>
      }
    >
      <NotWired
        what="The live view — current epoch and the loss curve"
        how={'Fold results are recorded as they land and are listed below, but the runner '
          + 'still prints per-epoch loss to its terminal and persists none of it. A '
          + 'progress row written per epoch would be what this tab reads to draw a curve.'}
      />

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
                  {r.label && <span className="ml-2">{r.label}</span>}
                  {r.strategy && (
                    <div className="opacity-70" title={`Saved strategy id ${r.strategy.id}`}>
                      {r.strategy.name}
                      {r.strategy.version !== null && ` v${r.strategy.version}`}
                    </div>
                  )}
                  {r.isTest && (
                    <span
                      className="badge badge-xs badge-warning ml-2 tooltip"
                      data-tip="This run spent the holdout"
                    >
                      holdout
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
                </td>
                <td className="font-mono text-xs whitespace-nowrap">
                  {r.trainStart}→{r.firstVal}..{r.lastVal}
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
                    : `${r.taxRate}% on realized gains`
                      + (r.taxTrain ? ', trained with it too' : ', scoring only')}
                >
                  {r.taxRate === null ? 'off' : pp(r.meanGainsTax)}
                  {r.taxTrain && <span className="badge badge-xs badge-warning ml-1">trained</span>}
                </td>
                <td className={`text-right font-mono text-xs ${
                  r.execution?.kind === 'margin' || r.meanTiming == null ? ''
                    : r.meanTiming > 0 ? 'text-success' : 'text-error'}`}
                >
                  {r.execution?.kind === 'margin' ? <NotStrategy1 /> : pp(r.meanTiming)}
                </td>
                <td>
                  {r.inProgress
                    ? <span className="badge badge-sm badge-info">running</span>
                    : r.beatsHoldOnReturn === true
                      ? (
                        <>
                          <span className="badge badge-sm badge-success">beat hold</span>
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
              <div className="overflow-x-auto bg-base-200 rounded-box">
                <table className="table table-sm">
                  <thead>
                    <tr>
                      <th className="text-right">Fold</th>
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
                      <th className="text-right">Accuracy</th>
                      <th
                        className="text-right tooltip tooltip-left"
                        data-tip="Early stopping: the epoch kept / epochs trained"
                      >
                        Epochs
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.folds.map((f: WalkforwardFold) => {
                      const excess = f.stratReturn - f.holdReturn
                      return (
                        <tr key={f.fold}>
                          <td className="text-right">{f.fold}</td>
                          <td className="text-right font-mono">{f.valYear}</td>
                          <td className="font-mono text-xs whitespace-nowrap">
                            {f.trainFrom} .. {f.trainTo}
                          </td>
                          <td className="text-right text-xs">
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
                          <td className="text-right font-mono text-xs">{pp(f.commissions)}</td>
                          <td className="text-right font-mono text-xs">
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
                              : `${(f.accuracy * 100).toFixed(1)}%`}
                            {f.baselineAcc != null && (
                              <span className="opacity-60" title="Always predicting the training majority class">
                                {` / ${(f.baselineAcc * 100).toFixed(1)}%`}
                              </span>
                            )}
                          </td>
                          <td
                            className="text-right font-mono text-xs"
                            title={f.epochsRun === null
                              ? 'Trained a fixed number of epochs'
                              : `Kept epoch ${f.bestEpoch}; stopped after ${f.epochsRun}`}
                          >
                            {f.epochsRun === null ? '—' : `${f.bestEpoch}/${f.epochsRun}`}
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
              {detail.run.hyperparams && (
                <details className="mt-3">
                  <summary className="cursor-pointer text-sm">
                    Hyperparameters and configuration
                  </summary>
                  <div className="mt-2 text-xs font-mono bg-base-200 rounded-box p-3">
                    <div>
                      feature set {detail.run.featureSetId ?? '—'} · {detail.run.inputWidth} inputs
                      {' · '}cost {detail.run.costBps} bps · parked in {detail.run.park}
                      {detail.run.threshold != null && ` · threshold ${detail.run.threshold}`}
                    </div>
                    <div className="mt-1">
                      {Object.entries(detail.run.hyperparams)
                        .map(([k, v]) => `${k}=${v}`).join('  ')}
                    </div>
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
 * What a run trained on: the strategy behind it, its columns, and how much of
 * the train window those columns really covered. A partial column is kept but
 * moves the first train bar, which is easy to miss from the fold table alone.
 */
/** A Strategy 1 attribution term on a Strategy 2 run, where it does not apply. */
function NotStrategy1() {
  return (
    <span className="opacity-50" title="Strategy 1's long-or-out attribution; a Strategy 2 run is never out">
      —
    </span>
  )
}

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

/** G3: which inputs and code produced the run, and whether they still hold. */
function RunLineage({ run }: { run: WalkforwardRun }) {
  const { tone, text } = lineageLine(run.id, run.lineage)
  const cls = tone === 'warn' ? 'text-warning' : tone === 'muted' ? 'text-base-content/60' : ''
  return <p className={`${cls} break-words`}>{text}</p>
}
