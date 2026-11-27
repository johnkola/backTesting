import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_TIMEFRAME, readTimeframe, TIMEFRAME_EVENT, TIMEFRAMES, writeTimeframe } from './timeframe'

/** A localStorage of the test's own: the tests run in Node, which has none. */
function storage(blocked = false) {
  const m = new Map<string, string>()
  const fail = () => { throw new Error('blocked') }
  return {
    getItem: (k: string) => (blocked ? fail() : m.get(k) ?? null),
    setItem: (k: string, v: string) => (blocked ? fail() : void m.set(k, v)),
    removeItem: (k: string) => (blocked ? fail() : void m.delete(k)),
    m,
  }
}

describe('the timeframe the pipeline reads', () => {
  let events: string[]
  beforeEach(() => {
    events = []
    const target = new EventTarget()
    target.addEventListener(TIMEFRAME_EVENT, (e) => events.push((e as CustomEvent).detail))
    vi.stubGlobal('window', target)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('is daily until another is chosen, and a stored value it does not know reads as daily', () => {
    const s = storage()
    vi.stubGlobal('localStorage', s)
    expect(readTimeframe()).toBe('D1')
    s.m.set('backtest.timeframe', 'W1')
    expect(readTimeframe()).toBe(DEFAULT_TIMEFRAME)
  })

  it('remembers a choice, tells the app, and stores nothing for the default', () => {
    const s = storage()
    vi.stubGlobal('localStorage', s)
    writeTimeframe('M30')
    expect(readTimeframe()).toBe('M30')
    writeTimeframe('D1')
    expect(s.m.has('backtest.timeframe')).toBe(false)
    expect(events).toEqual(['M30', 'D1'])
  })

  it('still tells the app when storage is blocked, and reads daily', () => {
    vi.stubGlobal('localStorage', storage(true))
    writeTimeframe('H1')
    expect(events).toEqual(['H1'])
    expect(readTimeframe()).toBe('D1')
  })

  it('offers the three timeframes the plan runs on', () => {
    // Step 8: the same path on daily, hourly and 30-minute bars.
    expect(TIMEFRAMES).toEqual(expect.arrayContaining(['D1', 'H1', 'M30']))
  })
})
