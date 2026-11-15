import { useState } from 'react'
import FeaturePreview from '../../components/FeaturePreview'
import { api, type FeaturesReport } from '../../lib/api'
import { checkColumn } from '../../lib/normalizationCheck'
import { useApiData } from '../../lib/useApiData'
import { useTimeframe } from '../../lib/useTimeframe'
import { usePipelineStatus } from '../../lib/pipelineStatusContext'
import { mergeState } from '../../lib/stageStatus'
import StageShell, { type StageState } from './StageShell'

/** Stage 3 — normalization: the method each column got, and whether the
 *  output distribution actually looks like that method's output. */
export default function NormalizationStage() {
  const [tf] = useTimeframe()
  const { status } = usePipelineStatus()
  const { data, error } = useApiData<FeaturesReport>((signal) => api.features(tf, signal), [tf])
  const [previewing, setPreviewing] = useState<string | null>(null)

  if (error) return <div className="alert alert-error">{error}</div>
  if (!data) return <span className="loading loading-spinner" />

  const newest = data.sets[0] ?? null
  if (!newest) {
    return (
      <StageShell
        state="empty"
        summary={`No set built for ${tf}`}
        detail={<>Nothing to check. Run <span className="font-mono">python -m nn.feature_build</span>.</>}
      />
    )
  }

  // The built set carries its own spec, so the method shown is the one that
  // was actually applied — not whatever features.toml says today.
  const methodOf = new Map(newest.columns.map((c) => [c.name, c.method]))
  const stats = [...data.columnStats].sort((a, b) => a.name.localeCompare(b.name))

  const rows = stats.map((s) => {
    const method = methodOf.get(s.name) ?? '—'
    return { stats: s, method, problem: checkColumn(method, s) }
  })
  const problems = rows.filter((r) => r.problem)
  const own: StageState = problems.length === 0 ? 'passed' : 'failed'
  const merged = mergeState(own, status?.normalization)

  return (
    <StageShell
      state={merged.state}
      alsoStale={merged.alsoStale}
      reasons={status?.normalization.reasons}
      summary={
        problems.length === 0
          ? `All ${rows.length} columns are shaped as their method promises`
          : `${problems.length} of ${rows.length} columns fall outside their method's range`
      }
      detail={
        problems.length === 0
          ? 'Cyclical columns bounded to -1..1, rescaled columns inside 0..1, flags only 0 or 1, z-scores centred near zero, daily returns and changes plausible, and no NaN or infinite value. Preview a column to see its input before and after its method.'
          : 'A column outside its method’s range means the transform or its parameters are wrong.'
      }
    >
      {previewing && (
        <FeaturePreview setId={newest.id} column={previewing} onClose={() => setPreviewing(null)} />
      )}

      <div className="overflow-x-auto bg-base-200 rounded-box mt-4">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Column</th>
              <th>Method</th>
              <th className="text-right">N</th>
              <th className="text-right">Nulls</th>
              <th className="text-right">Min</th>
              <th className="text-right">Max</th>
              <th className="text-right">Mean</th>
              <th className="text-right">Stdev</th>
              <th>Check</th>
              <th><span className="sr-only">Preview</span></th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ stats: s, method, problem }) => (
              <tr key={s.name} className={problem ? 'bg-error/10' : ''}>
                <td className="font-mono">{s.name}</td>
                <td className="font-mono text-xs">{method}</td>
                <td className="text-right">{s.n.toLocaleString()}</td>
                <td className="text-right">{s.nulls > 0 ? s.nulls : '—'}</td>
                <td className="text-right font-mono text-xs">{s.min?.toFixed(2) ?? '—'}</td>
                <td className="text-right font-mono text-xs">{s.max?.toFixed(2) ?? '—'}</td>
                <td className="text-right font-mono text-xs">{s.mean?.toFixed(3) ?? '—'}</td>
                <td className="text-right font-mono text-xs">{s.stdev?.toFixed(3) ?? '—'}</td>
                <td className="text-xs">
                  {problem
                    ? <span className="text-error">{problem}</span>
                    : <span className="text-success">ok</span>}
                </td>
                <td>
                  <button
                    type="button"
                    className={`btn btn-xs ${previewing === s.name ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() => setPreviewing(previewing === s.name ? null : s.name)}
                  >
                    Preview
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="text-sm text-base-content/60 mt-3">
        Nulls at the warm-up edge of a rolling window are expected, not a fault: the value
        is genuinely unknown until the window fills, which is why it is stored as NULL
        rather than zero.
      </p>
    </StageShell>
  )
}
