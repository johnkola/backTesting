import { describe, expect, it } from 'vitest'
import type { WalkforwardJob } from './api'
import { canCancel, jobBadge, jobNotes } from './walkforwardJob'

const job = (over: Partial<WalkforwardJob>): WalkforwardJob => ({
  id: 1, strategyId: 7, label: '', command: 'python -m nn.walkforward --strategy-id 7',
  status: 'running', queuedAt: '2026-11-23T10:00:00+00:00', startedAt: null, finishedAt: null,
  running: true, attempts: 1, cancelRequested: false, error: null, exitCode: null, runId: null,
  output: [], ...over,
})

describe('jobBadge', () => {
  it('says a queued job is waiting, and a running one is running', () => {
    expect(jobBadge(job({ status: 'queued' }))).toEqual({ text: 'waiting for a worker', tone: 'ghost' })
    expect(jobBadge(job({}))).toEqual({ text: 'running', tone: 'info' })
  })
  it('shows a cancel that was asked for and not yet heard', () => {
    expect(jobBadge(job({ cancelRequested: true })).text).toBe('stopping')
    expect(jobBadge(job({ status: 'queued', cancelRequested: true })).text).toBe('cancelling')
  })
  it('names each ending', () => {
    expect(jobBadge(job({ status: 'done', running: false, exitCode: 0 })).text).toBe('finished')
    expect(jobBadge(job({ status: 'cancelled', running: false })).text).toBe('cancelled')
    expect(jobBadge(job({ status: 'failed', running: false, exitCode: 2 })).text).toBe('exit 2')
    expect(jobBadge(job({ status: 'failed', running: false })).text).toBe('failed')
  })
})

describe('canCancel', () => {
  it('is offered while the job waits or runs, once', () => {
    expect(canCancel(job({}))).toBe(true)
    expect(canCancel(job({ status: 'queued' }))).toBe(true)
    expect(canCancel(job({ cancelRequested: true }))).toBe(false)
    expect(canCancel(job({ status: 'done', running: false }))).toBe(false)
  })
})

describe('jobNotes', () => {
  it('is empty for a job that ran once and recorded its run', () => {
    expect(jobNotes(job({ status: 'done', running: false, exitCode: 0, runId: 31 }))).toEqual([])
  })
  it('says when the job was taken again', () => {
    expect(jobNotes(job({ attempts: 2 }))[0]).toContain('attempt 2 of 3')
  })
  it('says a cancelled run keeps its folds', () => {
    expect(jobNotes(job({ status: 'cancelled', running: false, runId: 31 })))
      .toEqual(['run 31 keeps the folds it finished and is marked cancelled'])
  })
  it('gives the reason a job failed, and that no run was recorded', () => {
    expect(jobNotes(job({ status: 'failed', running: false, exitCode: 2, error: 'exit 2' })))
      .toEqual(['exit 2', 'no run was recorded; the output says why'])
  })
})
