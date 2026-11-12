import { describe, expect, it } from 'vitest'
import type { PipelineStatus, StageStatus } from './api'
import { mergeState, tabState } from './stageStatus'

const remote = (state: StageStatus['state']): StageStatus => ({ state, reasons: [] })

describe('mergeState', () => {
  it('a stale input makes a passing tab stale', () => {
    expect(mergeState('passed', remote('stale'))).toEqual({ state: 'stale', alsoStale: false })
  })
  it('keeps a failed check on top and says it is stale too', () => {
    expect(mergeState('failed', remote('stale'))).toEqual({ state: 'failed', alsoStale: true })
  })
  it('running wins over everything', () => {
    expect(mergeState('failed', remote('running')).state).toBe('running')
  })
  it('a tab with nothing built stays empty', () => {
    expect(mergeState('empty', remote('stale')).state).toBe('empty')
  })
  it('without a status the tab keeps its own verdict', () => {
    expect(mergeState('passed', null)).toEqual({ state: 'passed', alsoStale: false })
    expect(mergeState('unknown', remote('passed')).state).toBe('unknown')
  })
})

describe('tabState', () => {
  it('reads a stage from the status, or nothing before it loads', () => {
    expect(tabState(null, 'training')).toBeNull()
    const status = { training: remote('stale') } as unknown as PipelineStatus
    expect(tabState(status, 'training')).toBe('stale')
  })
})
