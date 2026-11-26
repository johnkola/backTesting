import { useState } from 'react'
import { type RemovalCounts, sentence } from '../lib/removal'

/**
 * Delete control for an instrument or a source: the two removals that can take
 * years of bars with them.
 *
 * Three steps, one more than a row's delete. The first press asks the API what
 * would go and deletes nothing; the counts are then on screen, in words, and
 * only a second press of a button that says "delete" removes them. The count
 * is read at the moment of asking, never guessed from the page.
 */
export default function RemoveSeriesButton({
  label, name, preview, remove, onDeleted,
}: {
  /** The button's own words: "Delete instrument". */
  label: string
  /** What is being removed, for the sentence: "AAPL", "yahoo". */
  name: string
  preview: () => Promise<{ counts: RemovalCounts }>
  remove: () => Promise<unknown>
  onDeleted: () => void
}) {
  const [counts, setCounts] = useState<RemovalCounts | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function act(step: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await step()
    } catch (err) {
      setError((err as Error).message)
      setCounts(null)
    } finally {
      setBusy(false)
    }
  }

  if (counts) {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-warning">
          Delete {name}? {sentence(counts)} This cannot be undone.
        </span>
        <button
          type="button" className="btn btn-error btn-xs" disabled={busy}
          onClick={() => act(async () => { await remove(); setCounts(null); onDeleted() })}
        >
          {busy && <span className="loading loading-spinner loading-xs" />}
          delete all of it
        </button>
        <button type="button" className="btn btn-ghost btn-xs" disabled={busy} onClick={() => setCounts(null)}>
          cancel
        </button>
      </div>
    )
  }

  return (
    <span className="flex flex-wrap items-center gap-2">
      <button
        type="button" className="btn btn-xs btn-outline btn-error" disabled={busy}
        onClick={() => act(async () => setCounts((await preview()).counts))}
      >
        {busy && <span className="loading loading-spinner loading-xs" />}
        {label}
      </button>
      {error && <span className="text-error text-xs">{error}</span>}
    </span>
  )
}
