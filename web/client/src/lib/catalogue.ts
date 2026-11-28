/**
 * The catalogue (R74): every engine and benchmark with its library and
 * version, what it does and what checks it, in the same words on the Strategy,
 * Results and Docs tabs and in the tooltips. The entries are the runner's
 * (`nn/engines.py`, `nn/benchmarks.py`); nothing here describes one.
 */
import { api, type BenchmarkEntry, type EngineEntry } from './api'
import { useApiData } from './useApiData'

export type CatalogueEntry = BenchmarkEntry | EngineEntry

/** What it is computed with: the library and its version. */
export function libraryLine(e: CatalogueEntry): string {
  return e.version ? `${e.library} ${e.version}` : `${e.library}, not installed`
}

/** An entry as one tooltip: what it does, what it is computed with, what checks it. */
export function catalogueTip(e: CatalogueEntry): string {
  return `${e.description} Computed with ${libraryLine(e)}. Checked by: ${e.checkedBy}.`
}

/** Entries by name. */
type Named = { name: string }

export function byName<T extends Named>(items: readonly T[] | null | undefined): Record<string, T> {
  return Object.fromEntries((items ?? []).map((e) => [e.name, e]))
}

/** The benchmarks' entries by name, once fetched; null until then or when they did not load. */
export function useBenchmarks(): Record<string, BenchmarkEntry> | null {
  const { data } = useApiData((signal) => api.benchmarks(signal).then((r) => r.items), [])
  return data ? byName(data) : null
}
