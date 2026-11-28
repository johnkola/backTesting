import type { ReloadResult } from './api'

/** True when a reload would write something: bars to put back or rewrite, or a year to record again. */
export function reloadWrites(r: ReloadResult): boolean {
  return r.restored + r.changed > 0 || r.recordedAgain.length > 0
}

/** One line per archived year the reload would touch or report; the rest are summed in the first line. */
export function reloadYearLines(r: ReloadResult): string[] {
  return r.years
    .filter((y) => y.restored || y.changed || y.notInArchive || !y.recorded)
    .map((y) => [
      `${y.year}: ${y.bars} in the archive`,
      y.restored ? `${y.restored} missing` : null,
      y.changed ? `${y.changed} different` : null,
      y.notInArchive ? `${y.notInArchive} stored but in no file (kept)` : null,
      y.recorded ? null : 'no audit row (recorded again)',
    ].filter(Boolean).join(', '))
}
