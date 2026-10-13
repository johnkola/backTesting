import { useEffect, useState } from 'react'
import {
  readTimeframe,
  TIMEFRAME_EVENT,
  writeTimeframe,
  type Timeframe,
} from './timeframe'

/**
 * The selected timeframe, and a setter that every listener sees.
 *
 * Deliberately an event on `window` rather than a React context: the picker
 * lives in the navbar and the readers are pages under an <Outlet />, so a
 * provider would have to wrap the whole router to join them. The event keeps
 * both ends independent, and `storage` covers other tabs for free.
 *
 * The value is read on first render, not in an effect, so a page's first fetch
 * already uses the stored timeframe instead of fetching D1 and then refetching.
 */
export function useTimeframe(): [Timeframe, (tf: Timeframe) => void] {
  const [tf, setTf] = useState<Timeframe>(readTimeframe)

  useEffect(() => {
    const onLocal = (e: Event) => {
      const next = (e as CustomEvent<Timeframe>).detail
      if (next) setTf(next)
    }
    // Another tab changed it: localStorage is already updated, so re-read.
    const onStorage = () => setTf(readTimeframe())

    window.addEventListener(TIMEFRAME_EVENT, onLocal)
    window.addEventListener('storage', onStorage)
    return () => {
      window.removeEventListener(TIMEFRAME_EVENT, onLocal)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  return [tf, writeTimeframe]
}
