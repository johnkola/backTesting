import { useState } from 'react'
import { api } from '../lib/api'
import { linePath } from '../lib/linePath'
import { describeSeries } from '../lib/seriesLabel'
import { useApiData } from '../lib/useApiData'

/** The windows the preview offers, in days back from the set's last day. */
const WINDOWS = [
  { days: 90, label: '3 months' },
  { days: 365, label: '1 year' },
  { days: 730, label: '2 years' },
  { days: 1825, label: '5 years' },
]

const W = 600
const H = 110

function Chart({ title, values, tone }: { title: string, values: (number | null)[], tone: string }) {
  const { d, min, max } = linePath(values, W, H)
  const missing = values.filter((v) => v === null).length
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="font-medium">{title}</span>
        <span className="opacity-60 font-mono">
          {min === null ? 'no values' : `${min.toPrecision(4)} .. ${max?.toPrecision(4)}`}
          {missing > 0 && ` · ${missing} day${missing === 1 ? '' : 's'} without a value`}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className={`w-full h-28 bg-base-100 rounded ${tone}`}>
        <path d={d} fill="none" stroke="currentColor" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  )
}

/**
 * One stored column before and after its normalization method, over the same
 * dates (`GET /api/nn/features/preview`). The design's Normalization tab asks
 * for these previews beside the distribution checks: a check says the output
 * has the right shape, the preview shows what the method did to get it there.
 */
export default function FeaturePreview({ setId, column, onClose }: {
  setId: number | string, column: string, onClose: () => void,
}) {
  const [days, setDays] = useState(730)
  const { data, error, loading } = useApiData(
    (signal) => api.featurePreview(setId, column, days, signal),
    [setId, column, days],
  )

  return (
    <div className="bg-base-200 rounded-box p-4 mt-4">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <h3 className="font-mono font-semibold">{column}</h3>
        {data && (
          <span className="text-xs opacity-70">
            {data.method}
            {Object.keys(data.params).length > 0 && ` ${JSON.stringify(data.params)}`}
            {' of '}{describeSeries(data.series)}
          </span>
        )}
        <div className="join ml-auto">
          {WINDOWS.map((w) => (
            <button
              key={w.days}
              type="button"
              className={`join-item btn btn-xs ${w.days === days ? 'btn-active' : ''}`}
              onClick={() => setDays(w.days)}
            >
              {w.label}
            </button>
          ))}
        </div>
        <button type="button" className="btn btn-xs btn-ghost" onClick={onClose} aria-label="Close the preview">✕</button>
      </div>

      {error && <div className="alert alert-error text-sm">{error}</div>}
      {loading && !data && <span className="loading loading-spinner" />}
      {data && (
        <>
          {!data.configMatches && (
            <div className="alert alert-warning text-sm mb-3">
              This set was built from another version of features.toml, so the input is read
              with today&apos;s alignment settings and may not be exactly what the set was given.
            </div>
          )}
          <div className="grid gap-3">
            <Chart title="Before: the input the method was given" values={data.points.map((p) => p.before)} tone="text-secondary" />
            <Chart title="After: the stored column" values={data.points.map((p) => p.after)} tone="text-primary" />
          </div>
          <div className="flex justify-between text-xs opacity-60 mt-1 font-mono">
            <span>{data.points[0]?.date ?? data.from}</span>
            <span>set {data.setId} · {data.points.length} days</span>
            <span>{data.points[data.points.length - 1]?.date ?? data.to}</span>
          </div>
        </>
      )}
    </div>
  )
}
