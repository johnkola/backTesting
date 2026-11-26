import { grouped } from './numbers'

/** What a removal takes with it, as the API counts it. */
export type RemovalCounts = {
  candles: number
  imports: number
  fileVersions: number
  featureSets: number
  walkforwardRuns: number
  results: number
}

/** The counts as a sentence, leaving out what there is none of. */
export function sentence(c: RemovalCounts): string {
  const parts = [
    [c.candles, 'bar'], [c.imports, 'import'], [c.fileVersions, 'file version'],
    [c.featureSets, 'feature set'], [c.walkforwardRuns, 'walk-forward run'],
    [c.results, 'backtest result'],
  ].filter(([n]) => (n as number) > 0)
    .map(([n, word]) => `${grouped(n as number, 0)} ${word}${n === 1 ? '' : 's'}`)
  return parts.length === 0 ? 'Nothing is stored under it.' : `It takes ${parts.join(', ')} with it.`
}
