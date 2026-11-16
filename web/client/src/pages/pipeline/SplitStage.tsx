import { useState } from 'react'
import { api } from '../../lib/api'
import { usePipelineStatus } from '../../lib/pipelineStatusContext'
import { splitProblems } from '../../lib/split'
import { mergeState } from '../../lib/stageStatus'
import { useApiData } from '../../lib/useApiData'
import StageShell, { type StageState } from './StageShell'

const BLOCK_PURPOSE: Record<string, string> = {
  train: 'The model learns patterns here; each fold trains from its start',
  validation: 'Walk-forward folds, one year each, choose the variant',
  test: 'Untouched final exam: scored once, at the end (step 8)',
  prediction: 'Ahead of the test block, for forward predictions',
}

const BLOCK_CLS: Record<string, string> = {
  train: 'bg-primary',
  validation: 'bg-warning',
  test: 'bg-error',
  prediction: 'bg-neutral',
}

/**
 * Stage 4: a saved strategy's chronological split, as a run of it makes it
 * (G37).
 *
 * The windows are the strategy's and the rows its own feature set's, after its
 * columns' gaps drop bars; the folds are the runner's, from the runner's code
 * (`GET /api/nn/walkforward/split`, `nn/split.py`). The gate is that every
 * block holds rows and each starts after the one before it ends: an overlap
 * would leak the future into training.
 */
export default function SplitStage() {
  const { status } = usePipelineStatus()
  const { data: saved, error: savedError } = useApiData(
    (signal) => api.savedStrategies({ latest: true }, signal), [],
  )
  const [strategyId, setStrategyId] = useState<string>('')

  // The most recently saved strategy until one is chosen.
  const items = saved?.items ?? []
  const chosen = strategyId || (items.length
    ? [...items].sort((a, b) => Number(b.id) - Number(a.id))[0].id : '')
  const { data: answer, error } = useApiData(
    (signal) => (chosen ? api.walkforwardSplit(chosen, signal) : Promise.resolve(null)), [chosen],
  )

  if (savedError) return <div className="alert alert-error">{savedError}</div>
  if (!saved) return <span className="loading loading-spinner" />
  if (items.length === 0) {
    return (
      <StageShell
        state="empty"
        summary="No saved strategy"
        detail="The split is a strategy's: save one on the Strategy tab to see how a run cuts it."
      />
    )
  }

  const picker = (
    <label className="form-control max-w-md mb-4">
      <span className="label-text text-xs">Strategy</span>
      <select
        className="select select-bordered select-sm"
        value={chosen}
        onChange={(e) => setStrategyId(e.target.value)}
      >
        {items.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name} v{s.version}: {s.instrument} {s.timeframe}
          </option>
        ))}
      </select>
    </label>
  )

  if (error) return <>{picker}<div className="alert alert-error">{error}</div></>
  if (!answer) return <>{picker}<span className="loading loading-spinner" /></>
  if (!answer.ok) {
    return (
      <>
        {picker}
        <StageShell
          state="failed"
          summary={answer.error}
          detail={answer.reasons.length ? (
            <ul className="list-disc ml-5">{answer.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          ) : undefined}
        />
      </>
    )
  }

  const split = answer.split
  const problems = splitProblems(split.blocks)
  const own: StageState = problems.length ? 'failed' : 'passed'
  const merged = mergeState(own, status?.split)
  const total = split.blocks.reduce((sum, b) => sum + b.rows, 0)

  return (
    <>
      {picker}
      <StageShell
        state={merged.state}
        alsoStale={merged.alsoStale}
        reasons={[...problems.map((text) => ({ kind: 'split', text })),
          ...(status?.split.reasons ?? [])]}
        summary={problems.length
          ? problems[0]
          : `${split.strategy.name} v${split.strategy.version}: ${total.toLocaleString()} rows `
            + 'split forward in time, no overlap'}
        detail={
          <>
            Set {split.setId}, {split.columns} columns, rows {split.firstRow} to {split.lastRow}
            {' '}({split.rows.toLocaleString()} after its columns&apos; gaps). Each fold trains
            from {split.folds[0]?.trainFrom ?? 'the train start'} up to its year, its last{' '}
            {split.purgeBars} training bars purged; the last fold stops {split.purgeBars} bars
            before {split.windowEnd}, so nothing reads the test block (G38).
            {split.patience != null && (
              <> With early stopping (patience {split.patience}) the network fits all but the
                last training year and decides when to stop on it; the tree engines train on
                every training row.</>
            )}
          </>
        }
      >
        <div className="flex h-8 rounded-box overflow-hidden mb-2">
          {split.blocks.map((b) => (
            total > 0 && b.rows > 0 && (
              <div
                key={b.block}
                className={`${BLOCK_CLS[b.block]} flex items-center justify-center text-xs text-base-100 font-medium`}
                style={{ width: `${(b.rows / total) * 100}%` }}
                title={`${b.block}: ${b.rows} rows`}
              >
                {(b.rows / total) >= 0.08 ? b.block : ''}
              </div>
            )
          ))}
        </div>

        <div className="overflow-x-auto bg-base-200 rounded-box mb-6">
          <table className="table table-sm">
            <thead>
              <tr>
                <th>Block</th>
                <th>Window</th>
                <th>Rows from</th>
                <th>Rows to</th>
                <th className="text-right">Rows</th>
                <th className="text-right">Share</th>
                <th>Purpose</th>
              </tr>
            </thead>
            <tbody>
              {split.blocks.map((b) => (
                <tr key={b.block} className={b.rows === 0 && b.block !== 'prediction' ? 'bg-error/10' : ''}>
                  <td className="font-medium capitalize">{b.block}</td>
                  <td className="text-xs font-mono">{b.from} → {b.to}</td>
                  <td className="text-xs">{b.firstRow ?? '—'}</td>
                  <td className="text-xs">{b.lastRow ?? '—'}</td>
                  <td className="text-right">{b.rows.toLocaleString()}</td>
                  <td className="text-right">
                    {total > 0 ? `${((b.rows / total) * 100).toFixed(1)}%` : '—'}
                  </td>
                  <td className="text-xs text-base-content/70">{BLOCK_PURPOSE[b.block]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <h3 className="font-medium mb-2">Walk-forward folds</h3>
        <div className="overflow-x-auto bg-base-200 rounded-box">
          <table className="table table-sm">
            <thead>
              <tr>
                <th>Fold</th>
                <th>Trains on</th>
                <th className="text-right">Rows</th>
                {split.patience != null && <th>Network fits, then stops on</th>}
                <th>Scores</th>
                <th className="text-right">Rows</th>
              </tr>
            </thead>
            <tbody>
              {split.folds.map((f) => (
                <tr key={f.fold}>
                  <td>{f.fold} ({f.valYear})</td>
                  <td className="text-xs font-mono">{f.trainFrom} → {f.trainTo}</td>
                  <td className="text-right">{f.trainRows.toLocaleString()}</td>
                  {split.patience != null && (
                    <td className="text-xs">
                      {f.earlyStopping ? (
                        <>
                          <span className="font-mono">{f.earlyStopping.fitFrom} → {f.earlyStopping.fitTo}</span>
                          {' '}({f.earlyStopping.fitRows.toLocaleString()}), then{' '}
                          <span className="font-mono">{f.earlyStopping.stopFrom} → {f.earlyStopping.stopTo}</span>
                          {' '}({f.earlyStopping.stopRows.toLocaleString()})
                        </>
                      ) : '—'}
                    </td>
                  )}
                  <td className="text-xs font-mono">{f.valFrom} → {f.valTo}</td>
                  <td className="text-right">{f.valRows.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="text-sm text-base-content/60 mt-3">
          These are the strategy&apos;s saved windows and the folds a run of it makes, from
          the runner&apos;s own code; each run also records the dates it covered. The
          holdout fold is step 8&apos;s, made once from a terminal.
        </p>
      </StageShell>
    </>
  )
}
