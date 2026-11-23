import type { WalkforwardJob } from './api'

/**
 * How a walk-forward job reads on the Train tab (G16 step 16.1; R67, R70).
 *
 * The job is a row in the jobs table: it waits, a worker claims it, and it ends
 * done, failed or cancelled. A cancel is a request the worker hears at its next
 * heartbeat, so between the request and the stop the job still runs.
 */
export type JobBadge = { text: string, tone: 'info' | 'success' | 'error' | 'warning' | 'ghost' }

export function jobBadge(job: WalkforwardJob): JobBadge {
  if (job.status === 'queued') {
    return job.cancelRequested
      ? { text: 'cancelling', tone: 'warning' }
      : { text: 'waiting for a worker', tone: 'ghost' }
  }
  if (job.status === 'running') {
    return job.cancelRequested
      ? { text: 'stopping', tone: 'warning' }
      : { text: 'running', tone: 'info' }
  }
  if (job.status === 'done') return { text: 'finished', tone: 'success' }
  if (job.status === 'cancelled') return { text: 'cancelled', tone: 'warning' }
  return { text: job.exitCode === null ? 'failed' : `exit ${job.exitCode}`, tone: 'error' }
}

/** A job can be asked to stop while it waits or runs, once. */
export function canCancel(job: WalkforwardJob): boolean {
  return job.running && !job.cancelRequested
}

/** What to say beside the badge when the job did not simply run once and finish. */
export function jobNotes(job: WalkforwardJob): string[] {
  const notes: string[] = []
  if (job.attempts > 1) {
    notes.push(`attempt ${job.attempts} of 3: its worker went silent and the job was taken again`)
  }
  if (job.status === 'cancelled' && job.runId !== null) {
    notes.push(`run ${job.runId} keeps the folds it finished and is marked cancelled`)
  }
  if (job.status === 'failed' && job.error) notes.push(job.error)
  if (!job.running && job.status !== 'cancelled' && job.runId === null) {
    notes.push('no run was recorded; the output says why')
  }
  return notes
}
