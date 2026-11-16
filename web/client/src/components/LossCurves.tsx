import type { EpochCurve, WalkforwardFold } from '../lib/api'
import { engineTitle } from '../lib/engines'
import { sparkPath } from '../lib/epochs'

const W = 160
const H = 40

/**
 * Each engine's loss curve on each fold (G11): the training loss solid, the
 * network's early-stopping loss dashed. One small chart per fold and engine,
 * each on its own scale, with its first and last loss and whether it fell.
 */
export default function LossCurves({ curves, folds }: {
  curves: readonly EpochCurve[]
  folds: readonly WalkforwardFold[]
}) {
  if (curves.length === 0) return null
  const loss = new Map(folds.map((f) => [`${f.fold}:${f.engine}`, f.loss]))
  const years = new Map(folds.map((f) => [f.fold, f.valYear]))
  return (
    <div className="mt-4">
      <h3 className="font-medium mb-2">Loss, epoch by epoch</h3>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {curves.map((c) => {
          const all = [...c.train, ...(c.stop ?? [])].filter((v): v is number => v != null)
          const lo = Math.min(...all)
          const hi = Math.max(...all)
          const l = loss.get(`${c.fold}:${c.engine}`)
          return (
            <div key={`${c.fold}:${c.engine}`} className="bg-base-200 rounded-box p-2">
              <div className="text-xs flex justify-between gap-2">
                <span>
                  Fold {c.fold}{years.get(c.fold) ? ` (${years.get(c.fold)})` : ''},{' '}
                  {engineTitle(c.engine)}
                </span>
                {l && (
                  <span
                    className={l.decreased ? 'text-success' : 'text-error'}
                    title={`Lowest ${l.lowest.toFixed(4)} over ${l.epochs} epochs`}
                  >
                    {l.first.toFixed(3)} → {l.last.toFixed(3)} {l.decreased ? '✓' : '✗ did not fall'}
                  </span>
                )}
              </div>
              <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-10" preserveAspectRatio="none"
                role="img" aria-label={`loss curve, fold ${c.fold}, ${c.engine}`}>
                <path d={sparkPath(c.train, W, H, lo, hi)} fill="none"
                  className="stroke-primary" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
                {c.stop && (
                  <path d={sparkPath(c.stop, W, H, lo, hi)} fill="none" strokeDasharray="3 2"
                    className="stroke-warning" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
                )}
              </svg>
            </div>
          )
        })}
      </div>
      <p className="text-xs text-base-content/60 mt-2">
        Training loss solid; dashed is the network&apos;s early-stopping year, which decides
        when it stops. A tree engine&apos;s curve is its boosting rounds. A NaN or infinite
        loss stops a run and says where.
      </p>
    </div>
  )
}
