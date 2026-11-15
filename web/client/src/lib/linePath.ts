/**
 * An SVG path for one series of values on a fixed box, for the small charts
 * on the Normalization tab's preview. A missing value breaks the line rather
 * than joining across it, because a gap in a feature column (a warm-up day,
 * a day a series did not print) is information, not noise.
 */
export type LinePath = { d: string, min: number | null, max: number | null }

export function linePath(values: readonly (number | null)[], width: number, height: number): LinePath {
  const present = values.filter((v): v is number => v !== null && Number.isFinite(v))
  if (present.length === 0) return { d: '', min: null, max: null }
  const min = Math.min(...present)
  const max = Math.max(...present)
  const span = max - min || 1
  const step = values.length > 1 ? width / (values.length - 1) : 0
  let d = ''
  let pen = false
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) {
      pen = false
      return
    }
    const x = (i * step).toFixed(1)
    // A flat series sits in the middle rather than on the floor.
    const y = (max === min ? height / 2 : height - ((v - min) / span) * height).toFixed(1)
    d += `${pen ? 'L' : 'M'}${x},${y}`
    pen = true
  })
  return { d, min, max }
}
