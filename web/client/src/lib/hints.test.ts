import { describe, expect, it } from 'vitest'
import { COHESION_CATEGORIES, categoryHint } from './cohesionHints'
import { fieldHints, hintFor } from './fieldHints'
import { metricHint, metricHints } from './metricHints'
import { WINDOW_KEYS } from './strategyForm'

// Every tooltip a page asks for has to be in a dictionary. `hintFor` answers an
// unknown key with an empty string and `metricHint` an unknown label with
// nothing, so a renamed field or tile otherwise loses its tooltip without a
// word. The pages are read as text; so are the two lists the dictionaries
// follow from elsewhere: the service's hyperparameters and label thresholds,
// and the loader's finding categories.

const sources = import.meta.glob(['../**/*.{ts,tsx}', '!../**/*.test.{ts,tsx}'], {
  query: '?raw', import: 'default', eager: true,
}) as Record<string, string>
const [strategyRules] = Object.values(import.meta.glob(
  '../../../../server/src/main/java/com/bazarbozorg/backtest/server/strategies/StrategyRules.java',
  { query: '?raw', import: 'default', eager: true },
)) as string[]
const [cohesion] = Object.values(import.meta.glob('../../../../python/loader/cohesion.py', {
  query: '?raw', import: 'default', eager: true,
})) as string[]

function source(file: string): string {
  const found = Object.entries(sources).find(([path]) => path.endsWith(file))
  if (!found) throw new Error(`no source ${file}`)
  return found[1]
}

function matches(text: string, pattern: RegExp): string[] {
  return [...text.matchAll(pattern)].map((m) => m[1])
}

const all = Object.values(sources).join('\n')
/** Keys named outright: `hintFor('a.b')`, and `hint="a.b"` on the components that look one up. */
const named = new Set([
  ...matches(all, /hintFor\('([^']+)'\)/g),
  ...matches(all, /\bhint="([a-z]+(?:\.[A-Za-z]+)+)"/g),
])
const params = matches(strategyRules, /\bparam\("(\w+)"/g)
const labelParams = matches(strategyRules, /\.put\("(\w+)", labelParam\(/g)
const marginKeys = matches(source('pages/StrategyPage.tsx'),
  /\['(\w+)', '[^']*', objectiveSpec\.marginDefaults\./g)

describe('field hints', () => {
  it('has every key a page names', () => {
    expect(named.size).toBeGreaterThan(40)
    expect([...named].filter((k) => !(k in fieldHints))).toEqual([])
  })

  it('has every key a page builds from a list', () => {
    expect(params).toHaveLength(13)
    expect(labelParams).toEqual(['buyThreshold', 'sellThreshold', 'minReturn'])
    expect(marginKeys).toEqual(['leveragePct', 'hedgePct', 'marginSpreadPct', 'maintenancePct'])
    const built = [
      ...WINDOW_KEYS.map((k) => `window.${k}`),
      ...params.map((k) => `param.${k}`),
      ...labelParams.map((k) => `label.${k}`),
      ...marginKeys.map((k) => `objective.${k}`),
    ]
    expect(built.filter((k) => !hintFor(k))).toEqual([])
  })

  it('holds nothing no page asks for', () => {
    const asked = new Set([
      ...named,
      ...WINDOW_KEYS.map((k) => `window.${k}`),
      ...params.map((k) => `param.${k}`),
      ...labelParams.map((k) => `label.${k}`),
      ...marginKeys.map((k) => `objective.${k}`),
    ])
    expect(Object.keys(fieldHints).filter((k) => !asked.has(k))).toEqual([])
  })
})

describe('metric hints', () => {
  const runTiles = matches(source('pages/RunPage.tsx'), /<Stat label="([^"]+)"/g)
  const resultBlock = source('pages/ResultDetailPage.tsx')
    .split('const metrics: Array<[string, string]> = [')[1]?.split('\n  ]\n')[0] ?? ''
  const resultTiles = matches(resultBlock, /^\s*\['([^']+)', /gm)

  it('has every tile the Run and result pages show', () => {
    expect(runTiles).toHaveLength(8)
    expect(resultTiles).toHaveLength(12)
    expect([...runTiles, ...resultTiles].filter((label) => !metricHint(label))).toEqual([])
  })

  it('holds no metric neither page shows', () => {
    const shown = new Set([...runTiles, ...resultTiles].map((label) => {
      const key = label.toLowerCase()
      return metricHints[key] === undefined ? { return: 'total return', final: 'final equity' }[key] : key
    }))
    expect(Object.keys(metricHints).filter((k) => !shown.has(k))).toEqual([])
  })
})

describe('cohesion hints', () => {
  it('explains every category the loader reports, and no other', () => {
    const tuple = cohesion.match(/^CATEGORIES = \(([^)]*)\)/m)?.[1] ?? ''
    const categories = matches(tuple, /"(\w+)"/g)
    expect(categories).toHaveLength(5)
    expect(Object.keys(COHESION_CATEGORIES).sort()).toEqual([...categories].sort())
    expect(categories.filter((c) => !categoryHint(c))).toEqual([])
  })
})
