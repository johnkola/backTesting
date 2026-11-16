/**
 * The Split tab's checks on a strategy's split (G37): every block holds rows
 * and each starts after the one before it ends. The windows are the strategy's
 * and the rows the run's own (`nn/split.py`); an overlap would leak the future
 * into training, and an empty block means a window landed outside the data.
 */
import type { SplitBlock } from './api'

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
