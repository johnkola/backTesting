import { describe, expect, it } from 'vitest'
import type { FeedResult, FeedRunSummary } from './api'
import { heldDetail, resultBadge, runSummary, scheduleText } from './feedsView'

const held = (over: Partial<NonNullable<FeedResult['held']>> = {}): FeedResult => ({
  feed: 'QQQ/yahoo-adj',
  status: 'held',
  held: {
    days: 9, from: '2026-12-08', to: '2026-12-19', dropped: 0, largest: 0.0015,
    factor: null, waiting: 1, examples: [], ...over,
  },
})

describe('resultBadge', () => {
  it('says what a refresh did', () => {
    expect(resultBadge({ feed: 'x', status: 'extended', added: 1 }).label).toBe('+1 day')
    expect(resultBadge({ feed: 'x', status: 'extended', added: 3 }).label).toBe('+3 days')
    expect(resultBadge({ feed: 'x', status: 'extended+fixed', added: 1 }).label)
      .toBe('+1, last day settled')
  })
  it('flags a held series as needing Apply', () => {
    expect(resultBadge(held())).toEqual({ label: 'held: needs Apply', tone: 'warning' })
  })
  it('is neutral before any run', () => {
    expect(resultBadge(null)).toEqual({ label: 'not run yet', tone: 'ghost' })
  })
})

describe('heldDetail', () => {
  it('names a uniform rescale as a dividend or split', () => {
    const text = heldDetail(held({ factor: 0.9985 }))
    expect(text).toContain('rescaled by 0.998500 (-0.150%)')
    expect(text).toContain('dividend re-adjustment or a split')
    expect(text).toContain('1 new day waiting')
  })
  it('reports other changes with their size', () => {
    const text = heldDetail(held({ days: 2, from: '2026-11-09', to: '2026-11-10', largest: 0.7456 }))
    expect(text).toBe('the provider now reports 2 stored days differently '
      + '(2026-11-09 to 2026-11-10), largest change 74.6%; 1 new day waiting')
  })
  it('says when the provider dropped days', () => {
    expect(heldDetail(held({ days: 1, from: '2026-11-10', to: '2026-11-10', dropped: 1, waiting: 0 })))
      .toBe('the provider no longer has 1 stored day (2026-11-10)')
  })
})

describe('scheduleText', () => {
  it('shows the next run in New York time', () => {
    const text = scheduleText({
      enabled: true, times: ['18:30'], days: 'Mon-Fri', timezone: 'America/New_York',
      nextRun: '2026-11-12T18:30:00-05:00',
    }, 'en-GB')
    expect(text).toMatch(/^Daily at 18:30 New York, Mon-Fri; next Thu 12 Nov,? 18:30$/)
  })
  it('says when there is no schedule', () => {
    expect(scheduleText({ enabled: false })).toBe('No daily run (FEEDS_SCHEDULE is off)')
  })
})

describe('runSummary', () => {
  const run = (over: Partial<FeedRunSummary> = {}): FeedRunSummary => ({
    id: 1, trigger: 'schedule', status: 'done', feeds: null, applyFeed: null,
    startedAt: null, finishedAt: null, error: null,
    counts: { extended: 9, 'extended+fixed': 4, 'up-to-date': 9, held: 2 },
    rebuild: { ok: true, last: '2026-11-11' }, ...over,
  })
  it('counts what changed and says where the feature table now ends', () => {
    expect(runSummary(run()))
      .toBe('13 updated · 9 up to date · 2 held; feature table rebuilt to 2026-11-11')
  })
  it('surfaces a failed rebuild and a failed run', () => {
    expect(runSummary(run({ rebuild: { ok: false } }))).toContain('feature rebuild FAILED')
    expect(runSummary(run({ status: 'failed', error: 'Busy' }))).toBe('failed: Busy')
  })
})
