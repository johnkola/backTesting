import { TIMEFRAME_LABELS, TIMEFRAMES } from '../lib/timeframe'
import { useTimeframe } from '../lib/useTimeframe'

/**
 * Bar-size control for the pipeline views.
 *
 * Sits beside the theme picker because it is the same kind of thing: one app
 * setting, chosen once, read everywhere. It changes what the stage tabs *look
 * at*, never what exists — see lib/timeframe.ts.
 */
export default function TimeframePicker() {
  const [tf, setTf] = useTimeframe()

  return (
    <div className="dropdown dropdown-end">
      <div
        tabIndex={0}
        role="button"
        className="btn btn-ghost btn-sm"
        aria-label="Change timeframe"
      >
        <span className="font-mono">{tf}</span>
        <svg
          width="12" height="12" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="3" aria-hidden="true"
        >
          <path d="M6 9l6 6 6-6" />
        </svg>
      </div>
      <ul
        tabIndex={0}
        className="dropdown-content menu bg-base-200 rounded-box z-20 mt-2 w-56 p-2 shadow"
      >
        {TIMEFRAMES.map((t) => (
          <li key={t}>
            <button
              type="button"
              className={t === tf ? 'active' : ''}
              onClick={() => setTf(t)}
            >
              <span className="font-mono w-10">{t}</span>
              <span className="text-xs opacity-70">{TIMEFRAME_LABELS[t]}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
