import { useState } from 'react'
import { api } from '../lib/api'

/**
 * Delete control for a result inside a table row.
 *
 * Shared by the Results page and the pipeline's Backtest stage, which list the
 * same rows in two places — a second copy of this would be a second chance to
 * get the confirm step wrong.
 *
 * Distinct from the control on the result detail page: that one is a
 * page-level action and navigates away once the result it is showing is gone,
 * whereas this one stays put and asks its parent to refetch the list.
 *
 * Two steps on purpose. A saved result is not recoverable — its trades and
 * equity curve live only in that row, so the run would have to be repeated to
 * get it back — and in a sortable table the rows move under the cursor when
 * the sort changes, which makes a one-click delete a mis-click waiting to
 * happen.
 */
export default function DeleteResultRowButton({
  id,
  onDeleted,
}: {
  id: string
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
      await api.deleteResult(id)
      onDeleted()
    } catch (err) {
      // Reported inline and the row stays: a failed delete must never look
      // like a successful one.
      setError((err as Error).message)
      setDeleting(false)
      setConfirming(false)
    }
  }

  if (error) {
    return (
      <span className="flex items-center gap-1">
        <span className="text-error text-xs" title={error}>failed</span>
        <button type="button" className="btn btn-ghost btn-xs" onClick={() => setError(null)}>
          dismiss
        </button>
      </span>
    )
  }

  if (confirming) {
    return (
      <span className="flex items-center gap-1">
        <button
          type="button"
          className="btn btn-error btn-xs"
          onClick={remove}
          disabled={deleting}
          title={'Permanently removes this result — its metrics, trade table and equity '
            + 'curve. It cannot be undone.'}
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
      aria-label={`Delete result ${id}`}
    >
      delete
    </button>
  )
}
