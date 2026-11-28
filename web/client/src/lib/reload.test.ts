import { describe, expect, it } from 'vitest'
import type { ReloadResult, ReloadYear } from './api'
import { reloadWrites, reloadYearLines } from './reload'

function year(over: Partial<ReloadYear>): ReloadYear {
  return {
    year: 2023, archivePath: 'yahoo/QQQ/2023/D1.csv', bars: 250, recorded: true, restored: 0,
    changed: 0, notInArchive: 0, notInArchiveFrom: null, notInArchiveTo: null, examples: [],
    ...over,
  }
}

function result(years: ReloadYear[], recordedAgain: number[] = []): ReloadResult {
  const sum = (k: 'bars' | 'restored' | 'changed' | 'notInArchive') =>
    years.reduce((n, y) => n + y[k], 0)
  return {
    symbol: 'QQQ', source: 'yahoo', timeframe: 'D1', dryRun: true, years,
    bars: sum('bars'), restored: sum('restored'), changed: sum('changed'),
    notInArchive: sum('notInArchive'), recordedAgain, written: false, rollups: null, lines: [],
  }
}

describe('a reload check', () => {
  it('has nothing to write when the stored bars are the archive\'s', () => {
    const same = result([year({ year: 2022 }), year({})])
    expect(reloadWrites(same)).toBe(false)
    expect(reloadYearLines(same)).toEqual([])
  })

  it('writes for a missing or different bar, or an undone year', () => {
    expect(reloadWrites(result([year({ restored: 2 })]))).toBe(true)
    expect(reloadWrites(result([year({ changed: 1 })]))).toBe(true)
    expect(reloadWrites(result([year({ recorded: false })], [2023]))).toBe(true)
  })

  it('a bar no file holds is reported, not written', () => {
    const extra = result([year({ notInArchive: 1 })])
    expect(reloadWrites(extra)).toBe(false)
    expect(reloadYearLines(extra)).toEqual(['2023: 250 in the archive, 1 stored but in no file (kept)'])
  })

  it('says per year what it found', () => {
    expect(reloadYearLines(result([
      year({ year: 2022 }),
      year({ restored: 4, changed: 1, recorded: false }),
    ], [2023]))).toEqual([
      '2023: 250 in the archive, 4 missing, 1 different, no audit row (recorded again)',
    ])
  })
})
