import { useEffect, useState } from 'react'
import { isAbortError } from './api'

/**
 * Loads data from the API and keeps it in step with its inputs.
 *
 * Every page had grown the same block — an AbortController, a `.then(setData)`,
 * a `.catch` that swallows aborts, and a `setError(null)` at the top of the
 * effect to clear the previous failure. That last line is the problem this hook
 * exists to remove: setting state synchronously in an effect body renders twice
 * and is what `react-hooks/set-state-in-effect` flags.
 *
 * Instead of resetting state when the inputs change, the settled state carries
 * the `key` it was fetched for. If the current key differs, the stored result
 * belongs to a previous request and is simply not reported — a derivation
 * during render, not a second state write. So a filter change shows the
 * spinner again without clearing anything.
 *
 * `deps` are both the effect's dependencies and that key, so they must be
 * JSON-serialisable values (strings, numbers, booleans) — the same things that
 * would go in a dependency array anyway. Bump a counter in `deps` to refetch.
 */
export function useApiData<T>(
  load: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): { data: T | null; error: string | null; loading: boolean } {
  const key = JSON.stringify(deps)
  const [settled, setSettled] = useState<{ key: string; data: T | null; error: string | null }>(
    { key: '', data: null, error: null },
  )

  useEffect(() => {
    const ctrl = new AbortController()
    load(ctrl.signal)
      .then((data) => setSettled({ key, data, error: null }))
      .catch((e: Error) => {
        if (!isAbortError(e)) setSettled({ key, data: null, error: e.message })
      })
    return () => ctrl.abort()
    // `load` is a fresh closure every render, so it cannot be a dependency
    // without refetching forever; `key` is what actually decides whether a
    // refetch is owed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  const current = settled.key === key
  return {
    data: current ? settled.data : null,
    error: current ? settled.error : null,
    loading: !current,
  }
}
