import type { ServiceHealth } from '../lib/api'

type Props = {
  /** Per-service verdicts, or null when the API never answered / nothing is down. */
  services: ServiceHealth[] | null
  title: string
  /** What went wrong, in one line. */
  message: string
  /** Deliberate maintenance is not fixed by starting a container — hide the hint. */
  showComposeHint: boolean
  /** True while a retry is in flight — the button disables rather than vanishing. */
  checking: boolean
  onRetry: () => void
}

/**
 * Shown instead of the app when a dependency is down.
 *
 * The alternative is what this replaces: every page mounting, firing its own
 * request, and rendering its own network error — which reads as "the UI is
 * broken" when the UI is fine and a container is not. So this names the service
 * that is actually down, says what to do about it, and offers a retry that
 * re-runs the same check rather than making the user reload by hand.
 */
export default function MaintenancePage({
  services, title, message, showComposeHint, checking, onRetry,
}: Props) {
  const down = services?.filter((s) => !s.ok) ?? []

  return (
    <div className="min-h-screen bg-base-100 flex items-center justify-center p-6">
      <div className="card bg-base-200 w-full max-w-2xl shadow">
        <div className="card-body">
          <h1 className="card-title text-2xl">{title}</h1>
          <p className="text-base-content/70">{message}</p>

          {services && (
            <ul className="mt-4 divide-y divide-base-300">
              {services.map((s) => (
                <li key={s.name} className="flex items-center gap-3 py-2">
                  <span
                    className={`badge badge-sm ${s.ok ? 'badge-success' : 'badge-error'}`}
                    aria-label={s.ok ? 'up' : 'down'}
                  />
                  <span className="font-medium capitalize w-28">{s.name}</span>
                  <span className="text-sm text-base-content/60 flex-1">{s.detail}</span>
                  <span className="text-xs text-base-content/40 tabular-nums">{s.latencyMs} ms</span>
                </li>
              ))}
            </ul>
          )}

          {/* The compose service names match the health report's names, so the
              hint can be a command the user can actually paste. */}
          {showComposeHint && down.length > 0 && (
            <div className="mockup-code mt-4 text-xs">
              <pre data-prefix="$"><code>docker compose up -d {down.map((s) => composeService(s.name)).join(' ')}</code></pre>
            </div>
          )}
          {showComposeHint && !services && (
            <div className="mockup-code mt-4 text-xs">
              <pre data-prefix="$"><code>docker compose up -d</code></pre>
            </div>
          )}

          <div className="card-actions justify-end mt-4">
            <button className="btn btn-primary" onClick={onRetry} disabled={checking}>
              {checking && <span className="loading loading-spinner loading-sm" />}
              {checking ? 'Checking…' : 'Retry'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

/** The health report calls it "database"; docker compose calls it "timescaledb". */
function composeService(name: string): string {
  return name === 'database' ? 'timescaledb' : name
}
