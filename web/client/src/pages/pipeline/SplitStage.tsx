import { api, type FeaturesReport } from '../../lib/api'
import { useApiData } from '../../lib/useApiData'
import { useTimeframe } from '../../lib/useTimeframe'
import StageShell, { type StageState } from './StageShell'

const BLOCK_PURPOSE: Record<string, string> = {
  train: 'The model learns patterns here',
  validation: 'Walk-forward folds choose the objective',
  test: 'Untouched final exam — read once, at the end',
}

const BLOCK_CLS: Record<string, string> = {
  train: 'bg-primary',
  validation: 'bg-warning',
  test: 'bg-error',
}

/**
 * Stage 4 — the chronological split.
 *
 * Always forward in time, never backward. The gate is that the blocks are
 * non-empty, contiguous and strictly ordered: an overlap would leak the
 * future into training, and an empty block means a boundary landed outside
 * the data.
 */
export default function SplitStage() {
  const [tf] = useTimeframe()
  const { data, error } = useApiData<FeaturesReport>((signal) => api.features(tf, signal), [tf])

  if (error) return <div className="alert alert-error">{error}</div>
  if (!data) return <span className="loading loading-spinner" />

  const split = data.split
  if (!split) {
    return (
      <StageShell
        state="empty"
        summary={`No set built for ${tf}`}
        detail="The split is cut from a built feature table, so there is nothing to divide yet."
      />
    )
  }

  const total = split.blocks.reduce((sum, b) => sum + b.rows, 0)
  const empty = split.blocks.filter((b) => b.rows === 0)

  // Strictly forward: each block must start after the previous one ended.
  const dated = split.blocks.filter((b) => b.fromDate && b.toDate)
  let overlap = false
  for (let i = 1; i < dated.length; i++) {
    if (dated[i].fromDate! <= dated[i - 1].toDate!) overlap = true
  }

  const state: StageState = empty.length > 0 || overlap ? 'failed' : 'passed'

  return (
    <StageShell
      state={state}
      summary={
        overlap
          ? 'Blocks overlap — the future is leaking backwards'
          : empty.length > 0
            ? `${empty.map((b) => b.block).join(', ')} is empty`
            : `${total.toLocaleString()} rows split forward in time, no overlap`
      }
      detail={
        <>
          Validation opens in {split.firstValidationYear}; {split.firstTestYear} onward is
          the holdout. Each fold retrains from the start up to its own edge, so every test
          is forward in time.
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
      <div className="text-xs text-base-content/60 mb-6">
        Roughly a 60 / 20 / 20 split; the boundaries can slide.
      </div>

      <div className="overflow-x-auto bg-base-200 rounded-box">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Block</th>
              <th>From</th>
              <th>To</th>
              <th className="text-right">Rows</th>
              <th className="text-right">Share</th>
              <th>Purpose</th>
            </tr>
          </thead>
          <tbody>
            {split.blocks.map((b) => (
              <tr key={b.block} className={b.rows === 0 ? 'bg-error/10' : ''}>
                <td className="font-medium capitalize">{b.block}</td>
                <td className="text-xs">{b.fromDate?.slice(0, 10) ?? '—'}</td>
                <td className="text-xs">{b.toDate?.slice(0, 10) ?? '—'}</td>
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

      <p className="text-sm text-base-content/60 mt-3">
        The boundaries shown are the API&apos;s configured defaults, not a record of what a
        given training run actually used — a run sets its own on the command line.
      </p>
    </StageShell>
  )
}
