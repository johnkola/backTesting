import { useEffect, useState, type ReactNode } from 'react'
import { api, isAbortError, FORCE_MAINTENANCE, type HealthReport } from '../lib/api'
import MaintenancePage from '../pages/MaintenancePage'

type Verdict =
  | { kind: 'up'; report: HealthReport }
  | { kind: 'degraded'; report: HealthReport }
  | { kind: 'maintenance'; report: HealthReport }
  | { kind: 'unreachable'; message: string }

/**
 * Checks every service once before the app renders, and shows the maintenance
 * page instead if any of them is down.
 *
 * Why gate at startup rather than let pages fail on their own: the API service
 * sat dead for three days and the only symptom was pages rendering empty — no
 * page distinguishes "no data yet" from "nothing is answering". One check up
 * front turns that into a sentence.
 *
 * It deliberately does not poll. A retry is a button, because the fix is
 * usually the user starting something, and a background poll that silently
 * swaps the page out from under them is worse than a press they chose to make.
 *
 * The settled verdict carries the attempt it came from, so a retry can render
 * the previous verdict with a spinning button without writing state twice.
 */
export default function ServiceGate({ children }: { children: ReactNode }) {
  const [attempt, setAttempt] = useState(0)
  // Hooks must run unconditionally, so the build-time switch is applied below
  // rather than with an early return here.

  const [settled, setSettled] = useState<{ attempt: number; verdict: Verdict } | null>(null)

  useEffect(() => {
    const ctrl = new AbortController()
    api.health(ctrl.signal)
      .then((report) => setSettled({ attempt, verdict: verdictFor(report) }))
      .catch((e: Error) => {
        if (isAbortError(e)) return
        setSettled({ attempt, verdict: { kind: 'unreachable', message: e.message } })
      })
    return () => ctrl.abort()
  }, [attempt])

  const checking = settled === null || settled.attempt !== attempt

  if (FORCE_MAINTENANCE) {
    return (
      <MaintenancePage
        services={null}
        title="Down for maintenance"
        message="This build of the client was published with maintenance mode switched on."
        showComposeHint={false}
        checking={false}
        onRetry={() => window.location.reload()}
      />
    )
  }

  // First load: nothing to show behind the check yet.
  if (settled === null) {
    return (
      <div className="min-h-screen flex items-center justify-center gap-3 text-base-content/60">
        <span className="loading loading-spinner" />
        Checking services…
      </div>
    )
  }

  const { verdict } = settled
  if (verdict.kind === 'up') return <>{children}</>

  return (
    <MaintenancePage
      // During maintenance the service list is noise — everything is up.
      services={verdict.kind === 'degraded' ? verdict.report.services : null}
      title={verdict.kind === 'maintenance' ? 'Down for maintenance' : 'Backtest is unavailable'}
      message={messageFor(verdict)}
      showComposeHint={verdict.kind !== 'maintenance'}
      checking={checking}
      onRetry={() => setAttempt((n) => n + 1)}
    />
  )
}

/** Maintenance beats a bad probe: it is the deliberate state, so say so first. */
function verdictFor(report: HealthReport): Verdict {
  if (report.maintenance) return { kind: 'maintenance', report }
  return report.ok ? { kind: 'up', report } : { kind: 'degraded', report }
}

function messageFor(verdict: Verdict): string {
  switch (verdict.kind) {
    case 'maintenance':
      return verdict.report.maintenanceMessage ?? 'The system is down for scheduled maintenance.'
    case 'degraded': {
      const down = verdict.report.services.filter((s) => !s.ok).map((s) => s.name)
      return `${listify(down)} ${down.length > 1 ? 'are' : 'is'} not responding. Everything else is up — start it and retry.`
    }
    case 'unreachable':
      return `The API did not answer (${verdict.message}). Nothing can load until it is back up.`
    case 'up':
      return ''
  }
}

/** "engine" · "loader and engine" · "database, loader and engine". */
function listify(names: string[]): string {
  if (names.length <= 1) return names[0] ?? 'A service'
  return `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
}
