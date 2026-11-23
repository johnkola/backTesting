/**
 * The prediction check of a walk-forward run, as one line (R62, G77).
 *
 * Each fold's network is exported to ONNX and run in ONNX Runtime by the Java
 * side; its output is compared with PyTorch's on the same inputs. A fold that
 * differs is a warning, never a failure of the run. A run with no rows was
 * never checked: it kept no exported network (every run before 2026-11-23), or
 * the engine could not be reached when it finished.
 */
import type { OnnxFoldCheck } from './api'

export type OnnxCheckLine = {
  /** `ok` every fold agreed; `warning` one differed or could not be checked; `none` never checked. */
  tone: 'ok' | 'warning' | 'none'
  text: string
  /** One sentence per fold that differed or could not be checked. */
  details: string[]
}

export function onnxCheckLine(checks: readonly OnnxFoldCheck[] | null | undefined): OnnxCheckLine {
  if (!checks || checks.length === 0) {
    return { tone: 'none', text: 'Not checked in ONNX Runtime.', details: [] }
  }
  const agreed = checks.filter((c) => c.agreed).length
  const compared = checks.filter((c) => c.problem === null && c.maxDiff !== null)
  const worst = compared.reduce((m, c) => Math.max(m, c.maxDiff as number), 0)
  const runtime = checks.find((c) => c.runtimeVersion)?.runtimeVersion
  const details = checks.filter((c) => !c.agreed).map((c) => (c.problem
    ? `Fold ${c.fold}: not checked, ${c.problem}.`
    : `Fold ${c.fold}: differs from PyTorch by ${(c.maxDiff as number).toPrecision(3)} on ${c.bars} bars (tolerance ${c.tolerance}).`))
  return {
    tone: agreed === checks.length ? 'ok' : 'warning',
    text: `ONNX Runtime${runtime ? ` ${runtime}` : ''}: ${agreed} of ${checks.length} folds agree with PyTorch`
      + (compared.length ? `, largest difference ${worst.toPrecision(3)}.` : '.'),
    details,
  }
}
