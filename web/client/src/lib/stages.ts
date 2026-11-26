/**
 * The pipeline's stages, in the order they run.
 *
 * In its own module rather than beside the page because a file that exports a
 * component should export only components — mixing a constant in breaks fast
 * refresh for the whole file.
 *
 * The order is load-bearing: each stage reads the previous one's stored output,
 * so the tab order is also the dependency order, and a stage marked stale
 * invalidates everything after it.
 */

export const STAGES = [
  { slug: 'data', n: 1, label: 'Data', blurb: 'Series pulled and stored' },
  // Five stages (R84): normalization is the last step of the feature build (R68).
  { slug: 'features', n: 2, label: 'Features', blurb: 'Built and normalized by the bars before each one' },
  { slug: 'split', n: 3, label: 'Split', blurb: 'Chronological train / validation / test' },
  { slug: 'training', n: 4, label: 'Training', blurb: 'Walk-forward folds' },
  { slug: 'backtest', n: 5, label: 'Backtest', blurb: 'Scored against buy-and-hold' },
] as const

export type StageSlug = (typeof STAGES)[number]['slug']
