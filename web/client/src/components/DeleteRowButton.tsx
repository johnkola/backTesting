import { useState } from 'react'

/**
 * Delete control for an item listed in a table row or beside a picker: a
 * walk-forward run, a trained model version, a built feature set.
 *
 * The same two steps as the result's own button, for the same reasons: what
 * goes does not come back, and in a table that re-sorts the rows move under
 * the cursor, so one click must never be enough. `what` is the sentence the
 * confirm step shows on hover, saying exactly what is removed.
 *
 * A failed delete is reported in place and the row stays: it must never look
 * like one that worked.
 */
export default function DeleteRowButton({
  label, what, onDelete, onDeleted,
}: {
  /** What is being deleted, for screen readers: "walk-forward run 12". */
  label: string
  /** What goes with it, in a sentence. */
  what: string
  onDelete: () => Promise<unknown>
  /** Called only after the server confirms the delete. */
  onDeleted: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function remove() {
    setDeleting(true)
    setError(null)
    try {
      await onDelete()
      setDeleting(false)
      setConfirming(false)
      onDeleted()
    } catch (err) {
      setError((err as Error).message)
      setDeleting(false)
      setConfirming(false)
    }
  }

  if (error) {
    return (
      <span className="flex items-center gap-1 whitespace-nowrap">
        <span className="text-error text-xs max-w-48 truncate" title={error}>{error}</span>
        <button type="button" className="btn btn-ghost btn-xs" onClick={() => setError(null)}>
          dismiss
        </button>
      </span>
    )
  }

  if (confirming) {
    return (
      <span className="flex items-center gap-1 whitespace-nowrap">
        <button
          type="button"
          className="btn btn-error btn-xs"
          onClick={remove}
          disabled={deleting}
          title={what}
        >
          {deleting && <span className="loading loading-spinner loading-xs" />}
          confirm
        </button>
        <button
          type="button"
          className="btn btn-ghost btn-xs"
          onClick={() => setConfirming(false)}
          disabled={deleting}
        >
          cancel
        </button>
      </span>
    )
  }

  return (
    <button
      type="button"
      className="btn btn-xs btn-outline btn-error"
      onClick={() => setConfirming(true)}
      aria-label={`Delete ${label}`}
      title={what}
    >
      delete
    </button>
  )
}
