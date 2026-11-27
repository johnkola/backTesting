import { describe, expect, it } from 'vitest'
import { engineWindow, sorted, trainBody } from './api'

/**
 * The loader's own row filter, in JavaScript.
 *
 * `python/nn/data.py` builds `timestamp >= %(since)s AND timestamp < %(until)s`,
 * so a window is only correct if it selects every stored candle under exactly
 * that predicate. Asserting on the returned strings alone would have passed
 * happily while the old one-bar shift dropped the first candle of every series,
 * which is why these tests check selection rather than arithmetic.
 */
function selectedBy(window: { since: string; until: string }, stored: string[]): string[] {
  const since = new Date(window.since).getTime()
  const until = new Date(window.until).getTime()
  return stored.filter((ts) => {
    const t = new Date(ts).getTime()
    return t >= since && t < until
  })
}

/** Daily closes as `GET /api/instruments` reports them: UTC midnight, ISO-8601. */
const DAILY = [
  '2019-12-02T00:00:00.000Z',
  '2019-12-03T00:00:00.000Z',
  '2019-12-04T00:00:00.000Z',
  '2019-12-05T00:00:00.000Z',
  '2019-12-06T00:00:00.000Z',
]

describe('engineWindow', () => {
  it('selects every stored candle, first and last included', () => {
    const w = engineWindow(DAILY[0], DAILY[DAILY.length - 1])
    expect(selectedBy(w, DAILY)).toEqual(DAILY)
  })

  it('starts at the first stored candle rather than one bar past it', () => {
    const w = engineWindow(DAILY[0], DAILY[DAILY.length - 1])
    expect(w.since).toBe('2019-12-02T00:00:00.000Z')
    expect(selectedBy(w, DAILY)[0]).toBe(DAILY[0])
  })

  it('ends just past the last stored candle, so the half-open bound keeps it', () => {
    const w = engineWindow(DAILY[0], DAILY[DAILY.length - 1])
    expect(w.until).toBe('2019-12-06T00:00:00.001Z')
    expect(selectedBy(w, DAILY)).toContain('2019-12-06T00:00:00.000Z')
  })

  it('excludes candles outside the stored range', () => {
    const w = engineWindow(DAILY[1], DAILY[3])
    expect(selectedBy(w, DAILY)).toEqual([DAILY[1], DAILY[2], DAILY[3]])
  })

  it('does not widen the window by a whole bar at either end', () => {
    const w = engineWindow(DAILY[0], DAILY[DAILY.length - 1])
    const dayAfterLast = '2019-12-07T00:00:00.000Z'
    const dayBeforeFirst = '2019-12-01T00:00:00.000Z'
    expect(selectedBy(w, [dayBeforeFirst, ...DAILY, dayAfterLast])).toEqual(DAILY)
  })

  it('handles a single-candle series', () => {
    const only = DAILY[2]
    expect(selectedBy(engineWindow(only, only), DAILY)).toEqual([only])
  })

  it('is independent of bar size — intraday endpoints behave the same', () => {
    const intraday = [
      '2026-03-02T14:30:00.000Z',
      '2026-03-02T14:31:00.000Z',
      '2026-03-02T14:32:00.000Z',
    ]
    const w = engineWindow(intraday[0], intraday[intraday.length - 1])
    expect(selectedBy(w, intraday)).toEqual(intraday)
  })

  it('accepts the date-only form the CLI passes', () => {
    const w = engineWindow('2001-05-15', '2021-08-19')
    expect(w.since).toBe('2001-05-15T00:00:00.000Z')
    expect(w.until).toBe('2021-08-19T00:00:00.001Z')
    expect(selectedBy(w, ['2001-05-15T00:00:00.000Z', '2021-08-19T00:00:00.000Z']))
      .toHaveLength(2)
  })

  /**
   * The Java side computes the same window from the same series:
   * `NeuralNetworkStrategy.buildIndicators` takes `barDate(bar)` for the first
   * and last bar and nudges `until` by `plusMillis(1)`. If these two ever
   * disagree, a model's recorded provenance stops describing the bars it saw.
   */
  it('agrees with the engine: first stored timestamp, last plus one milli', () => {
    const first = DAILY[0]
    const last = DAILY[DAILY.length - 1]
    const javaSince = new Date(first).toISOString()
    const javaUntil = new Date(new Date(last).getTime() + 1).toISOString()
    expect(engineWindow(first, last)).toEqual({ since: javaSince, until: javaUntil })
  })
})

describe('sorted', () => {
  it('writes a key and a direction as one sort field', () => {
    expect(sorted({ sort: 'rows', dir: 'asc', limit: 5 })).toEqual({ sort: 'rows', limit: 5 })
    expect(sorted({ sort: 'rows', dir: 'desc' })).toEqual({ sort: '-rows' })
  })
  it('reads a key with no direction as descending, and no key as no sort', () => {
    expect(sorted({ sort: 'imported' })).toEqual({ sort: '-imported' })
    expect(sorted({ dir: 'asc', limit: 5 })).toEqual({ limit: 5 })
  })
})

describe('trainBody', () => {
  const network = { engine: 'mlp', fields: { lookbackWindow: 'lookback_window',
    hiddenLayerSize: 'hidden_size', numHiddenLayers: 'num_hidden', learningRate: 'learning_rate' } }
  const tree = { engine: 'xgboost', fields: { lookbackWindow: 'lookback_window' },
    otherParameters: 'engine_settings' }
  const req = {
    symbol: 'QQQ', source: 'yahoo-adj', timeframe: 'D1', firstCandle: '2003-01-02',
    lastCandle: '2015-12-31', mode: 'force' as const,
  }

  it('names each parameter as the engine does, never by converting its case', () => {
    const body = trainBody({ ...req, training: network,
      parameters: { lookbackWindow: '20', hiddenLayerSize: '128', numHiddenLayers: '3', learningRate: '0.01' } })
    expect(body.engine).toBe('mlp')
    expect(body).toMatchObject({ lookback_window: 20, hidden_size: 128, num_hidden: 3, learning_rate: 0.01 })
    expect(body).not.toHaveProperty('hidden_layer_size')
    expect(body).not.toHaveProperty('num_hidden_layers')
    expect(body).not.toHaveProperty('engine_settings')
  })

  it('trains a tree engine as itself, its own settings as engine_settings', () => {
    const body = trainBody({ ...req, training: tree,
      parameters: { lookbackWindow: '20', maxDepth: '4', nEstimators: '' } })
    expect(body.engine).toBe('xgboost')
    expect(body.lookback_window).toBe(20)
    expect(body.engine_settings).toEqual({ maxDepth: 4 })
  })

  it('leaves out what the table does not place, as the engine does', () => {
    const body = trainBody({ ...req, training: network, parameters: { cacheKey: 'abc', other: '3' } })
    expect(Object.keys(body).sort()).toEqual(['engine', 'mode', 'since', 'source', 'symbol',
      'timeframe', 'until'])
  })
})
