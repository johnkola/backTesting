import { useEffect, useState } from 'react'
import {
  INSTRUMENT_EVENT, parseInstrument, readInstrument, writeInstrument, type InstrumentChoice,
} from './instrument'

/** The pipeline's instrument, and a setter every listener sees; as `useTimeframe`. */
export function useInstrument(): [InstrumentChoice | null, (c: InstrumentChoice | null) => void] {
  const [choice, setChoice] = useState<InstrumentChoice | null>(readInstrument)

  useEffect(() => {
    const onLocal = (e: Event) => setChoice(parseInstrument((e as CustomEvent<string>).detail))
    const onStorage = () => setChoice(readInstrument())
    window.addEventListener(INSTRUMENT_EVENT, onLocal)
    window.addEventListener('storage', onStorage)
    return () => {
      window.removeEventListener(INSTRUMENT_EVENT, onLocal)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  return [choice, writeInstrument]
}
