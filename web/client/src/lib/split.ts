/**
 * The Split tab's checks on a strategy's split (G37): every block holds rows
 * and each starts after the one before it ends. The windows are the strategy's
 * and the rows the run's own (`nn/split.py`); an overlap would leak the future
 * into training, and an empty block means a window landed outside the data.
 */
import type { SplitBlock, SplitFold, SplitHoldout } from './api'

/** What is wrong with the blocks, in order; empty when they pass. */
export function splitProblems(blocks: readonly SplitBlock[]): string[] {
  const out: string[] = []
  for (const b of blocks) {
    // The prediction window runs ahead of the data, so it may hold none yet.
    if (b.rows === 0 && b.block !== 'prediction') out.push(`${b.block} holds no rows`)
  }
  for (let i = 1; i < blocks.length; i++) {
    if (blocks[i].from <= blocks[i - 1].to) {
      out.push(`${blocks[i].block} starts ${blocks[i].from}, before ${blocks[i - 1].block} ends ${blocks[i - 1].to}`)
    }
  }
  return out
}

/**
 * What is wrong with the folds (step 3: no overlap): a fold that trains on a
 * day it is scored on, two folds whose scored years touch, the validation
 * folds reaching the test block, or the holdout training on its test days.
 * The runner makes them so none of this can happen; the tab says so if it does.
 */
export function foldProblems(folds: readonly SplitFold[], holdout: SplitHoldout | null | undefined,
  windowEnd: string | null): string[] {
  const out: string[] = []
  for (const f of folds) {
    if (f.trainTo >= f.valFrom) out.push(`fold ${f.fold} trains to ${f.trainTo}, on or after its first scored day ${f.valFrom}`)
  }
  for (let i = 1; i < folds.length; i++) {
    if (folds[i].valFrom <= folds[i - 1].valTo) {
      out.push(`fold ${folds[i].fold} scores from ${folds[i].valFrom}, before fold ${folds[i - 1].fold} ends ${folds[i - 1].valTo}`)
    }
  }
  const last = folds[folds.length - 1]
  if (last && windowEnd && last.valTo >= windowEnd) {
    out.push(`the last fold scores to ${last.valTo}, into the test block (window end ${windowEnd})`)
  }
  if (holdout) {
    if (holdout.trainTo >= holdout.testFrom) out.push(`the holdout trains to ${holdout.trainTo}, on or after its test starts ${holdout.testFrom}`)
    if (last && last.valTo >= holdout.testFrom) out.push(`the last fold scores to ${last.valTo}, into the test block (${holdout.testFrom})`)
  }
  return out
}
