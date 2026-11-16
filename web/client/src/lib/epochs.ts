/**
 * The Training tab's live view (G11): the loss curves each run records epoch by
 * epoch, and the progress line of a run that is still going. Pure, so tested
 * without rendering.
 */
import type { RunProgress } from './api'

/**
 * An SVG path through `values` over a `w` x `h` box, the y axis spanning `lo`
 * to `hi` (default: the values' own range). A null value breaks the line.
 */
export function sparkPath(
  values: readonly (number | null)[], w: number, h: number, lo?: number, hi?: number,
): string {
  const finite = values.filter((v): v is number => v != null && Number.isFinite(v))
  if (finite.length === 0) return ''
  const min = lo ?? Math.min(...finite)
  const max = hi ?? Math.max(...finite)
  const span = max - min || 1
  const step = values.length > 1 ? w / (values.length - 1) : 0
  let out = ''
  let pen = false
  values.forEach((v, i) => {
    if (v == null || !Number.isFinite(v)) { pen = false; return }
    const x = (i * step).toFixed(1)
    const y = (h - ((v - min) / span) * h).toFixed(1)
    out += `${pen ? 'L' : 'M'}${x},${y} `
    pen = true
  })
  return out.trim()
}

/** "45s", "3m 10s", "1h 02m". */
export function duration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
}

/** "fold 3, network epoch 42, loss 1.0213 · 4 of 18 fold rows · 3m 10s, about 6m 05s left". */
export function progressLine(p: RunProgress, title: (engine: string) => string): string {
  const where = p.fold == null
    ? 'starting'
    : `fold ${p.fold}, ${title(p.engine ?? '')} epoch ${p.epoch}`
      + (p.trainLoss == null ? '' : `, loss ${p.trainLoss.toFixed(4)}`)
  return `${where} · ${p.unitsDone} of ${p.unitsPlanned} fold rows · ${duration(p.elapsedSeconds)}`
    + (p.etaSeconds == null ? '' : `, about ${duration(p.etaSeconds)} left`)
}
