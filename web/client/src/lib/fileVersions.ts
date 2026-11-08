import type { ChangeKind, SliceDiff } from './api'

/**
 * How the Imports page names a file version and a changed year.
 *
 * The loader decides what a change is (python/loader/csv_import.py): an
 * `update` only adds days after the stored ones, a `fix` changes or drops
 * stored bars. These are the words and colours for those decisions, kept in
 * one place so the upload result and the version history cannot disagree.
 */

export const CHANGE_KIND_LABEL: Record<ChangeKind, string> = {
  new: 'new series',
  update: 'update',
  fix: 'data fix',
  'update+fix': 'update + data fix',
  same: 'same data',
}

/** A fix is the one that changes history, so it is the one that stands out. */
export const CHANGE_KIND_BADGE: Record<ChangeKind, string> = {
  new: 'badge-info',
  update: 'badge-success',
  fix: 'badge-warning',
  'update+fix': 'badge-warning',
  same: 'badge-ghost',
}

export const CHANGE_KIND_HINT: Record<ChangeKind, string> = {
  new: 'The first file loaded for this series.',
  update: 'Only days after the stored ones were added. Nothing already stored changed.',
  fix: 'Stored bars were changed or removed: the provider revised history, or the data was corrected. Anything built on the old values is out of date.',
  'update+fix': 'New days were added and stored bars were changed or removed.',
  same: 'The bytes differ (line endings, spacing, number formatting) but every bar is identical.',
}

/** "+3 added · 2 changed · 1 removed", or '' when nothing differs. */
export function diffSummary(diff: SliceDiff | null | undefined): string {
  if (!diff) return ''
  const parts: string[] = []
  if (diff.added) parts.push(`+${diff.added} added`)
  if (diff.changed) parts.push(`${diff.changed} changed`)
  if (diff.removed) parts.push(`${diff.removed} removed`)
  return parts.join(' · ')
}

/** True when the year changes history rather than only extending it. */
export function isFix(diff: SliceDiff | null | undefined): boolean {
  return !!diff && (diff.changed > 0 || diff.removed > 0)
}

/** The example timestamps as dates, for a compact "first changed" note. */
export function exampleDates(diff: SliceDiff | null | undefined): string[] {
  return (diff?.examples ?? []).map((ts) => ts.slice(0, 10))
}

export type SliceCounts = { created: number, extended: number, overwritten: number, skipped: number }

/** Tally of committed slice statuses. Unknown statuses are ignored, not NaN. */
export function countByStatus(rows: { status: string }[]): SliceCounts {
  const c: SliceCounts = { created: 0, extended: 0, overwritten: 0, skipped: 0 }
  for (const r of rows) if (r.status in c) c[r.status as keyof SliceCounts]++
  return c
}

/** "24 year slices: 1 extended · 23 skipped". */
export function summarize(c: SliceCounts, total: number): string {
  const parts: string[] = []
  if (c.created) parts.push(`${c.created} created`)
  if (c.extended) parts.push(`${c.extended} extended`)
  if (c.overwritten) parts.push(`${c.overwritten} overwritten`)
  if (c.skipped) parts.push(`${c.skipped} skipped`)
  return `${total} year ${total === 1 ? 'slice' : 'slices'}: ${parts.join(' · ')}`
}
