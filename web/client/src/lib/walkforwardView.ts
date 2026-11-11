import type { WalkforwardLineage, WalkforwardRun } from './api'

/**
 * The pure logic behind the Training tab's walk-forward tables.
 *
 * Kept out of the component for the same reason as `strategyForm.ts`: it can be
 * tested without rendering anything, and the rules it encodes are ones the
 * browser must not get wrong. Two of them matter more than the rest.
 *
 * A RUN THAT MEASURED NOTHING HAS NOT PASSED. A run with no folds, or one still
 * executing, must read as undecided — never as a verdict. The API already
 * returns null for every average in that case; this is the display half of the
 * same rule.
 *
 * ONLY THE `return` OBJECTIVE IS DECIDED HERE. `sharpe` and `drawdown` are the
 * runner's to judge: the drawdown verdict is two conditions, not one, because a
 * smaller drawdown is trivially bought by staying flat — an earlier version of
 * that check declared a win for a model that shaved 1.65 points off a 49%
 * drawdown while giving up 12 points of return a year. Re-deriving it in the
 * browser would be a second copy of the rule, free to drift.
 */

/** Mirrors `StageState` in pages/pipeline/StageShell. */
export type Verdict = 'passed' | 'failed' | 'unknown'

/** Signed percentage points, so a gain and a cost never look alike. */
export function pp(n: number | null | undefined, digits = 2): string {
  return n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(digits)}`
}

/** A plain number, or an em dash where the API said null. */
export function num(n: number | null | undefined, digits = 2): string {
  return n == null ? '—' : n.toFixed(digits)
}

export function verdictOf(run: WalkforwardRun): { state: Verdict, detail: string } {
  if (run.folds === 0) {
    return run.inProgress
      ? { state: 'unknown', detail: 'Running. No fold has been scored yet.' }
      : {
        state: 'failed',
        detail: 'Finished without scoring a fold — it died before the first one completed.',
      }
  }
  if (run.inProgress) {
    return {
      state: 'unknown',
      detail: `Still running: ${run.folds} fold${run.folds === 1 ? '' : 's'} scored so far.`,
    }
  }
  if (run.objective !== 'return' || run.beatsHoldOnReturn == null) {
    return {
      state: 'unknown',
      detail: `Scored on ${run.objective}, which the runner decides — `
        + 'the folds below carry the numbers it decided on.',
    }
  }
  return run.beatsHoldOnReturn
    ? {
      state: 'passed',
      detail: `Beat buy-and-hold on the validation folds: ${pp(run.meanExcess)} pp per fold `
        + `across ${run.folds}, winning ${run.foldsWon}.`,
    }
    : {
      state: 'failed',
      detail: `Did not beat buy-and-hold: ${pp(run.meanExcess)} pp per fold across `
        + `${run.folds}, winning ${run.foldsWon}.`,
    }
}

/**
 * The newest run that actually concluded something.
 *
 * Not simply the newest run: one still executing has nothing to conclude, and
 * letting it set the stage badge would flip the whole tab to "not recorded"
 * every time someone starts a job. Runs arrive newest first.
 */
/**
 * One line on whether a run's inputs are still what it read (G3).
 *
 * `warn` only when the store says the values changed: a rebuild that wrote
 * the same values is not news. The values hash covers every column of the
 * set, so "changed" can be a column the run did not use; the check command
 * named here is the exact answer.
 */
export function lineageLine(
  runId: number,
  l: WalkforwardLineage | null,
): { tone: 'muted' | 'ok' | 'warn', text: string } {
  if (!l) {
    return {
      tone: 'muted',
      text: 'No input snapshot: recorded before runs kept one, so it cannot be checked.',
    }
  }
  const snap = `Input snapshot ${l.inputSnapshot.slice(0, 12)}`
  const code = l.codeHash ? `, code ${l.codeHash.slice(0, 12)}` : ''
  const libs = l.libVersions?.torch ? `, torch ${l.libVersions.torch}` : ''
  const check = `python -m nn.walkforward --check-snapshot ${runId}`
  if (l.valuesChangedSince) {
    return {
      tone: 'warn',
      text: `${snap}${code}. The feature set was rebuilt with different values since `
        + `(build ${l.featureRunId} then, ${l.currentBuildId} now), so a rerun may not `
        + `reproduce it. Exact answer: ${check}`,
    }
  }
  const state = l.setRebuiltSince
    ? ' The set was rebuilt since, with the same values.'
    : l.setRebuiltSince === false ? ' Its feature set has not been rebuilt since.' : ''
  return { tone: 'ok', text: `${snap}${code}${libs}.${state}` }
}

export function newestFinished(runs: readonly WalkforwardRun[]): WalkforwardRun | undefined {
  return runs.find((r) => !r.inProgress && r.folds > 0)
}

/** What the stage header says, given the runs and how many models are on disk. */
export function stageSummary(
  runs: readonly WalkforwardRun[],
  modelCount: number,
  timeframe: string,
): { state: Verdict | 'empty', summary: string, detail: string } {
  if (runs.length === 0) {
    return modelCount === 0
      ? {
        state: 'empty',
        summary: `Nothing trained at ${timeframe}`,
        detail: 'Nothing has been trained at this timeframe yet.',
      }
      : {
        state: 'unknown',
        summary: `${modelCount} model${modelCount === 1 ? '' : 's'} on disk, `
          + 'no walk-forward run recorded',
        detail: 'These models predate the run record, or were trained outside the '
          + 'walk-forward runner. Run `python -m nn.walkforward` to record folds '
          + 'against buy-and-hold.',
      }
  }

  const newest = newestFinished(runs)
  if (newest) {
    const v = verdictOf(newest)
    return {
      state: v.state,
      summary: newest.label
        ? `Newest finished run: ${newest.label}`
        : `Newest finished run #${newest.id}`,
      detail: v.detail,
    }
  }

  const running = runs.filter((r) => r.inProgress).length
  return {
    state: 'unknown',
    summary: `${running} run${running === 1 ? '' : 's'} in progress, none finished`,
    detail: 'A run is recorded from the moment it starts, so its folds appear as they '
      + 'are scored rather than only at the end.',
  }
}

/**
 * Strategy 2's settings and what they cost, in one line, or null under
 * Strategy 1 (G5). A margin call or a day with the core at risk is named,
 * never folded into a total: those are the events the 50% ceiling exists to
 * prevent.
 */
export function executionLine(run: WalkforwardRun): string | null {
  const e = run.execution
  if (!e || e.kind !== 'margin') return null
  const parts = [
    `long ${e.leveragePct}% / PSQ ${e.hedgePct}% of the core`,
    `Fed funds + ${e.marginSpreadPct}`,
    `call below ${e.maintenancePct}%`,
  ]
  if (e.longThreshold !== null) parts.push(`long at position >= ${e.longThreshold}`)
  if (e.marginTrain) parts.push('trained on this curve')
  let line = `Strategy 2: ${parts.join(', ')}`
  if (run.folds > 0) {
    line += ` · interest ${pp(e.meanMarginInterest)} pts a fold`
      + `, ${e.marginCalls ?? 0} margin call${e.marginCalls === 1 ? '' : 's'}`
      + `, ${e.trims ?? 0} trim${e.trims === 1 ? '' : 's'}`
    if (e.coreAtRisk) line += `, core at risk on ${e.coreAtRisk} day${e.coreAtRisk === 1 ? '' : 's'}`
    const timing = leveredTiming(run)
    if (timing !== null) {
      line += ` · vs always long on margin ${pp(timing)} pp a fold`
        + ` (${e.foldsBeatLevered ?? 0}/${run.folds} folds)`
    }
  }
  return line
}

/**
 * G25: what the model's timing added over borrowing alone, in points a fold:
 * mean strategy return minus mean always-long-on-margin return. Null when the
 * run has no baseline (Strategy 1, or folds scored before G25).
 */
export function leveredTiming(run: WalkforwardRun): number | null {
  const e = run.execution
  if (!e || e.kind !== 'margin' || e.meanLeveredReturn == null || run.meanStratReturn == null) {
    return null
  }
  return run.meanStratReturn - e.meanLeveredReturn
}

/**
 * True when a Strategy 2 run beat buy-and-hold on return but not always long
 * on margin: its excess is the borrowing, not the model. The badge that says
 * so sits beside "beat hold" rather than replacing it, because buy-and-hold
 * stays the design's benchmark.
 */
export function isLeverageOnly(run: WalkforwardRun): boolean {
  return run.beatsHoldOnReturn === true && run.beatsLeveredOnReturn === false
}
