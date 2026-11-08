import type { SavedStrategy } from '../lib/api'
import { tipClass } from './FieldLabel'
import { hintFor } from '../lib/fieldHints'

/**
 * What a saved strategy resolved to, read-only.
 *
 * Architecture.md §8.1a, on the Train tab: "Runs a saved strategy and records
 * the run (§6.1). **No choices are made here**: it shows the strategy, the
 * exact feature list and windows it resolved to after per-feature
 * availability, then launches and reports progress."
 *
 * So this replaces the instrument / source / timeframe pickers rather than
 * pre-filling them. A strategy has already decided those, and asking again is
 * both noise and an invitation to contradict the record the run will point at
 * — train `AAPL` under a strategy that says `QQQ` and the run's own provenance
 * is a lie.
 *
 * The train window IS enforced: training is given exactly those bars, so the
 * validation and test blocks are never shown to the weights. What is not yet
 * built is generating walk-forward folds across the validation window from
 * these dates, so that block is still a declaration rather than something
 * training consumed — stated on the page rather than implied away.
 */
export default function ResolvedStrategy(
  { strategy: s, candleCount, from, to, windowBars, emptyWindows }: {
    strategy: SavedStrategy
    /** Stored candles for the series the strategy names. */
    candleCount: number
    from: string
    to: string
    /** Stored bars inside each named window, keyed by window name. */
    windowBars: Record<string, number>
    /** Windows that hold no stored bars at all. */
    emptyWindows: string[]
  },
) {
  const windows = Object.entries(s.windows)
  const hyper = Object.entries(s.hyperparams)

  return (
    <div className="bg-base-200 rounded-box p-4 mt-4 text-sm space-y-3">
      <div className="flex items-baseline gap-2">
        <span className="font-medium">Resolved from</span>
        <span className="font-mono">{s.name}</span>
        <span className="badge badge-ghost badge-sm">v{s.version}</span>
        <span className={`text-xs text-base-content/50 ${tipClass}`} data-tip={hintFor('train.readOnly')}>
          read-only
        </span>
      </div>

      <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2 text-xs">
        <Row label="Instrument" value={`${s.instrument} · ${s.source} · ${s.timeframe}`} />
        <Row label="Model" value={`${s.modelType.toUpperCase()} · ${s.validationScheme.replace('_', ' ')}`} />
        <Row
          label="Features"
          value={`${s.features.length} selected`}
          hint={s.features.join(', ')}
        />
        <Row
          label="Label"
          value={s.label ? `${s.label.kind} · ${s.label.horizonBars} bars ahead` : '—'}
        />
        <Row
          label="Stored series"
          value={`${candleCount.toLocaleString()} candles, ${from.slice(0, 10)} → ${to.slice(0, 10)}`}
        />
        <Row label="Saved by" value={`${s.createdBy} on ${s.createdAt.slice(0, 10)}`} />
      </dl>

      {windows.length > 0 && (
        <div>
          <div className="text-xs font-medium mb-1">Windows the strategy defines</div>
          <ul className="text-xs font-mono space-y-0.5">
            {windows.map(([key, w]) => {
              const n = windowBars[key]
              const empty = n === 0
              return (
                <li key={key} className={empty ? 'text-error' : undefined}>
                  <span className="inline-block w-24 capitalize not-italic">{key}</span>
                  {w.from} → {w.to}
                  {n !== undefined && (
                    <span className={empty ? '' : 'opacity-60'}>
                      {'  '}{n.toLocaleString()} bars{empty ? '  ← empty' : ''}
                    </span>
                  )}
                </li>
              )
            })}
          </ul>
          <p className="text-xs text-base-content/60 mt-1">
            Training fits the <span className="font-medium">train</span> window only — the
            validation and test blocks are not shown to the weights. Walk-forward folds
            across the validation window are not yet generated from these dates, so that
            block is still a declaration rather than something training consumed.
          </p>
        </div>
      )}

      <div>
        <div className="text-xs font-medium mb-1">Objective and costs</div>
        <p className="text-xs font-mono">
          {[
            `loss=${s.objective.loss}`,
            s.objective.loss === 'ce'
              ? `classWeights=${s.objective.classWeights}`
              : `positionThreshold=${s.objective.positionThreshold}`,
            `verdict=${s.objective.verdict}`,
            `cost=${s.objective.costBps} bps`,
            `park=${s.objective.park}`,
            s.objective.taxScore
              ? `tax=${s.objective.taxRatePct}% on realized gains`
                + (s.objective.taxTrain ? ', trained with it (signals differ)' : ', scoring only')
              : 'tax=off',
          ].join('  ·  ')}
        </p>
        {s.objectiveDefaulted && (
          <p className="text-xs text-warning mt-0.5">
            Saved before the objective was part of a strategy, so these are the defaults.
            Re-save it on the Strategy tab to record them as a choice.
          </p>
        )}
      </div>

      {hyper.length > 0 && (
        <div>
          <div className="text-xs font-medium mb-1">Hyperparameters set by the strategy</div>
          <p className="text-xs font-mono">
            {hyper.map(([k, v]) => `${k}=${v}`).join('  ·  ')}
          </p>
          <p className="text-xs text-base-content/50 mt-0.5">
            Anything not listed uses the model default.
          </p>
        </div>
      )}

      {emptyWindows.length > 0 && (
        <div className="alert alert-error py-2">
          <div className="text-xs">
            <p className="font-medium">
              {emptyWindows.join(', ')} window
              {emptyWindows.length === 1 ? '' : 's'} hold no stored bars.
            </p>
            <p className="mt-1">
              The series runs {from.slice(0, 10)} → {to.slice(0, 10)}, so{' '}
              {emptyWindows.includes('train')
                ? 'the model would be fitted on no samples at all.'
                : 'that window cannot be scored.'}
              {' '}Fix the dates on the Strategy tab — it will save a new version — or point the
              strategy at a series that covers them.
            </p>
          </div>
        </div>
      )}

      {s.notes && <p className="text-xs italic text-base-content/70">{s.notes}</p>}
    </div>
  )
}

function Row({ label, value, hint }: { label: string, value: string, hint?: string }) {
  return (
    <>
      <dt className="text-base-content/60">{label}</dt>
      <dd className={hint ? `${tipClass} font-mono cursor-help` : 'font-mono'} data-tip={hint}>
        {value}
      </dd>
    </>
  )
}
