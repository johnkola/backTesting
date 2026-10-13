import { api, type FeatureColumnStats, type FeaturesReport } from '../../lib/api'
import { useApiData } from '../../lib/useApiData'
import { useTimeframe } from '../../lib/useTimeframe'
import StageShell, { type StageState } from './StageShell'

/**
 * The design's normalization gate, as a check per method.
 *
 * Each method promises a shape, and a violated promise is a real bug rather
 * than a cosmetic one: a rescaled column outside its band means the declared
 * min/max was wrong, and a cyclical column outside -1..1 is not a sine. The
 * z-score bound is deliberately loose — a rolling z-score genuinely spikes
 * past 6 in a crash, so only something absurd is worth flagging.
 */
function checkColumn(method: string, s: FeatureColumnStats): string | null {
  if (s.n === 0) return 'no values at all'
  const { min, max, mean } = s
  if (min === null || max === null || mean === null) return null

  switch (method) {
    case 'cyclical':
      return min < -1.0001 || max > 1.0001 ? `outside -1..1 (${min.toFixed(2)}..${max.toFixed(2)})` : null
    case 'rescale':
      return min < -0.0001 || max > 1.0001 ? `outside 0..1 (${min.toFixed(2)}..${max.toFixed(2)})` : null
    case 'flag':
      return min < 0 || max > 1 ? `not a 0/1 flag (${min}..${max})` : null
    case 'rolling_zscore':
      if (Math.abs(mean) > 1) return `mean ${mean.toFixed(2)} is far from 0`
      return Math.abs(min) > 25 || Math.abs(max) > 25 ? `implausible extreme (${min.toFixed(1)}..${max.toFixed(1)})` : null
    case 'ratio_to_rolling_mean':
      return min < 0 ? `negative ratio (${min.toFixed(2)})` : null
    default:
      return null
  }
}

/** Stage 3 — normalization: the method each column got, and whether the
 *  output distribution actually looks like that method's output. */
export default function NormalizationStage() {
  const [tf] = useTimeframe()
  const { data, error } = useApiData<FeaturesReport>((signal) => api.features(tf, signal), [tf])

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
  const state: StageState = problems.length === 0 ? 'passed' : 'failed'

  return (
    <StageShell
      state={state}
      summary={
        problems.length === 0
          ? `All ${rows.length} columns are shaped as their method promises`
          : `${problems.length} of ${rows.length} columns fall outside their method's range`
      }
      detail={
        problems.length === 0
          ? 'Cyclical columns bounded to -1..1, rescaled columns inside 0..1, z-scores centred near zero.'
          : 'A column outside its method’s range means the transform or its parameters are wrong.'
      }
    >
      <div className="overflow-x-auto bg-base-200 rounded-box">
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
