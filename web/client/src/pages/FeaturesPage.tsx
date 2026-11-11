import { useMemo, useState } from 'react'
import {
  api, isAbortError,
  type CatalogueFeature, type CatalogueSet, type FeatureCoverage, type FeatureGaps,
} from '../lib/api'
import FieldLabel, { tipClass } from '../components/FieldLabel'
import { hintFor } from '../lib/fieldHints'
import { useApiData } from '../lib/useApiData'

/**
 * The Features tab — the catalogue and the coverage view (Architecture.md §8.1a).
 *
 * "The feature catalogue from `feature_definitions`: every feature with its
 * range, cadence, scope, status, cost, units and lineage, plus a coverage view:
 * pick an instrument and a date window and see, per feature, where it has
 * values and where it has gaps."
 *
 * Two things this page refuses to blur.
 *
 * Coverage is measured in **bars**, never calendar days. The feature table has
 * one row per trading day, so a one-year window holds about 252 bars; dividing
 * by 365 would report every healthy feature as 70% covered.
 *
 * And a feature can be missing in three different ways, kept apart: it has a
 * number, it has a row saying "not computable yet" (a rolling window that is
 * not full), or it has no row at all. The first two are normal at the start of
 * a series; the third means the column was never built here. Collapsing them
 * into one "missing" figure is how a short history gets mistaken for a broken
 * pipeline.
 *
 * Disabled and blocked columns are listed, not filtered out. Design.md keeps a
 * blocked column in the table on purpose — it is a recorded decision, and the
 * tab has to be able to say *why* a feature is unavailable.
 */
export default function FeaturesPage() {
  const { data, error, loading } = useApiData(
    (signal) => api.featureCatalogue(signal), [],
  )

  const [setId, setSetId] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [scopeFilter, setScopeFilter] = useState('')

  const sets = useMemo(() => data?.sets ?? [], [data])
  // Default to the newest set, derived rather than seeded from an effect.
  const activeSet: CatalogueSet | undefined =
    sets.find((s) => s.id === setId) ?? sets[0]

  const { data: coverage, loading: coverageLoading } = useApiData(
    (signal) => (activeSet
      ? api.featureCoverage(
        { set: activeSet.id, ...(from ? { from } : {}), ...(to ? { to } : {}) }, signal,
      )
      : Promise.resolve(null)),
    [activeSet?.id, from, to],
  )

  const coverageByName = useMemo(() => {
    const m = new Map<string, FeatureCoverage>()
    for (const c of coverage?.items ?? []) m.set(c.name, c)
    return m
  }, [coverage])

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return (data?.items ?? []).filter((f) => {
      if (statusFilter && f.status !== statusFilter) return false
      if (scopeFilter && f.scope !== scopeFilter) return false
      if (!needle) return true
      return f.name.includes(needle)
        || f.method.includes(needle)
        || (f.note ?? '').toLowerCase().includes(needle)
    })
  }, [data, search, statusFilter, scopeFilter])

  const counts = useMemo(() => {
    const all = data?.items ?? []
    return {
      total: all.length,
      active: all.filter((f) => f.status === 'active').length,
      dataOnly: all.filter((f) => f.status === 'data-only').length,
      undecided: all.filter((f) => f.status === 'undecided').length,
      blocked: all.filter((f) => f.status === 'blocked').length,
      paid: all.filter((f) => f.cost === 'paid').length,
      // What the network actually eats: enabled columns times their width, so
      // the three cyclical columns count twice each.
      inputWidth: all.filter((f) => f.enabled).reduce((n, f) => n + f.width, 0),
    }
  }, [data])

  if (loading) {
    return <div className="flex justify-center p-12"><span className="loading loading-lg" /></div>
  }
  if (error) {
    return <div className="alert alert-error"><span>{error}</span></div>
  }

  return (
    <section>
      <h1 className="text-2xl font-semibold mb-1">Features</h1>
      <p className="text-base-content/70 mb-5 max-w-3xl">
        Every column the pipeline can produce, what it is made of, and where it actually has
        values. A column that is off stays listed with the reason — that is a recorded
        decision, not an absence.
      </p>

      <div className="stats stats-horizontal shadow mb-5 text-sm">
        <Stat label="Catalogue" value={counts.total} hint="features.catalogueCount" />
        <Stat label="Active" value={counts.active} hint="features.active" />
        <Stat label="Data only" value={counts.dataOnly} hint="features.dataOnly" />
        <Stat label="Undecided" value={counts.undecided} hint="features.undecided" />
        <Stat label="Blocked" value={counts.blocked} hint="features.blocked" />
        <Stat label="Input width" value={counts.inputWidth} hint="features.inputWidth" />
      </div>

      <div className="bg-base-200 rounded-box p-4 mb-5 grid gap-3 md:grid-cols-5">
        <label className="form-control md:col-span-2">
          <FieldLabel text="Coverage against" hint={hintFor('features.set')} />
          <select
            className="select select-bordered select-sm"
            value={activeSet?.id ?? ''}
            onChange={(e) => setSetId(e.target.value)}
          >
            {sets.length === 0 && <option value="">no feature set built yet</option>}
            {sets.map((s) => (
              <option key={s.id} value={s.id}>
                set #{s.id} — {s.symbol} {s.sourceName} {s.timeframe},{' '}
                {s.rowCount.toLocaleString()} bars, built {s.builtAt.slice(0, 10)}
              </option>
            ))}
          </select>
        </label>
        <label className="form-control">
          <FieldLabel text="From" hint={hintFor('features.window')} />
          <input
            type="date" className="input input-bordered input-sm"
            value={from} onChange={(e) => setFrom(e.target.value)}
          />
        </label>
        <label className="form-control">
          <FieldLabel text="To" hint={hintFor('features.window')} />
          <input
            type="date" className="input input-bordered input-sm"
            value={to} onChange={(e) => setTo(e.target.value)}
          />
        </label>
        <div className="form-control justify-end">
          <button
            type="button" className="btn btn-sm"
            onClick={() => { setFrom(''); setTo('') }}
            disabled={!from && !to}
          >
            Whole set
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-3 items-end mb-3">
        <label className={`form-control ${tipClass}`} data-tip={hintFor('features.search')}>
          <input
            className="input input-bordered input-sm w-56"
            placeholder="filter by name, method or note"
            value={search} onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <select
          className="select select-bordered select-sm" value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">all statuses</option>
          <option value="active">active</option>
          <option value="data-only">data only</option>
          <option value="undecided">undecided</option>
          <option value="blocked">blocked</option>
        </select>
        <select
          className="select select-bordered select-sm" value={scopeFilter}
          onChange={(e) => setScopeFilter(e.target.value)}
        >
          <option value="">all scopes</option>
          <option value="instrument">instrument</option>
          <option value="market">market</option>
        </select>
        <span className="text-sm text-base-content/60">
          {rows.length} of {counts.total}
          {coverage && (
            <> · coverage over <span className="font-mono">{coverage.bars.toLocaleString()}</span> bars
              {coverage.windowFrom && (
                <> ({coverage.windowFrom.slice(0, 10)} → {coverage.windowTo?.slice(0, 10)})</>
              )}
            </>
          )}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="table table-sm table-zebra">
          <thead>
            <tr>
              <Th label="Feature" hint="features.col.name" />
              <Th label="Scope" hint="features.col.scope" />
              <Th label="Units" hint="features.col.units" />
              <Th label="Method" hint="features.col.method" />
              <Th label="Cadence" hint="features.col.cadence" />
              <Th label="Status" hint="features.col.status" />
              <Th label="Own range" hint="features.col.range" />
              <Th label="Coverage" hint="features.col.coverage" />
              <Th label="Lineage" hint="features.col.lineage" />
            </tr>
          </thead>
          <tbody>
            {rows.map((f) => (
              <Row
                key={f.name}
                feature={f}
                coverage={coverageByName.get(f.name)}
                coverageLoading={coverageLoading}
                setId={activeSet?.id}
                from={from}
                to={to}
              />
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <p className="text-sm text-base-content/60 p-4">
            Nothing matches. Clear the filters, or check that{' '}
            <span className="font-mono">python -m nn.feature_catalog --sync</span> has run.
          </p>
        )}
      </div>
    </section>
  )
}

function Stat({ label, value, hint }: { label: string, value: number, hint: string }) {
  return (
    <div className="stat py-2">
      <div className={`stat-title text-xs ${tipClass}`} data-tip={hintFor(hint)}>{label}</div>
      <div className="stat-value text-2xl tabular-nums">{value.toLocaleString()}</div>
    </div>
  )
}

function Th({ label, hint }: { label: string, hint: string }) {
  return (
    <th>
      <span className={tipClass} data-tip={hintFor(hint)}>
        <span className="cursor-help underline decoration-dotted decoration-base-content/30 underline-offset-4">
          {label}
        </span>
      </span>
    </th>
  )
}

const STATUS_STYLE: Record<string, string> = {
  active: 'badge-success',
  'data-only': 'badge-info',
  undecided: 'badge-warning',
  blocked: 'badge-error',
}

/** One catalogue row, expandable to its gaps. */
function Row(
  { feature: f, coverage, coverageLoading, setId, from, to }: {
    feature: CatalogueFeature
    coverage?: FeatureCoverage
    coverageLoading: boolean
    setId?: string
    from: string
    to: string
  },
) {
  const [open, setOpen] = useState(false)
  const [gaps, setGaps] = useState<FeatureGaps | null>(null)
  const [gapsError, setGapsError] = useState<string | null>(null)
  const [gapsBusy, setGapsBusy] = useState(false)

  // Gaps are fetched per feature on expand, not for every column up front: a gap list
  // for every feature over 6,900 bars is a report nobody reads.
  async function toggle() {
    const next = !open
    setOpen(next)
    if (!next || gaps || !setId) return
    setGapsBusy(true); setGapsError(null)
    try {
      setGaps(await api.featureGaps({
        set: setId, feature: f.name, ...(from ? { from } : {}), ...(to ? { to } : {}),
      }))
    } catch (err) {
      if (!isAbortError(err as Error)) setGapsError((err as Error).message)
    } finally {
      setGapsBusy(false)
    }
  }

  return (
    <>
      <tr className="hover cursor-pointer" onClick={toggle}>
        <td className="font-mono text-xs">
          <span className="opacity-40 mr-1">{open ? '▾' : '▸'}</span>
          {f.name}
          {f.width > 1 && (
            <span className={`${tipClass} opacity-50 ml-1`} data-tip={hintFor('features.width')}>
              ×{f.width}
            </span>
          )}
        </td>
        <td className="text-xs">{f.scope}</td>
        <td className="text-xs">{f.units}</td>
        <td className="font-mono text-xs opacity-70">{f.method}</td>
        <td className="text-xs">{f.cadence}</td>
        <td>
          <span className={`badge badge-sm ${STATUS_STYLE[f.status] ?? 'badge-ghost'}`}>
            {f.status}
          </span>
          {f.cost === 'paid' && (
            <span className={`badge badge-sm badge-outline ml-1 ${tipClass}`} data-tip={hintFor('features.paid')}>
              paid
            </span>
          )}
        </td>
        <td className="text-xs font-mono whitespace-nowrap">
          {f.rangeStart
            ? `${f.rangeStart.slice(0, 10)} → ${f.rangeEnd?.slice(0, 10)}`
            : <span className="opacity-50">never built</span>}
        </td>
        <td className="min-w-32">
          {coverageLoading && <span className="loading loading-xs" />}
          {!coverageLoading && coverage && <CoverageBar c={coverage} />}
          {!coverageLoading && !coverage && <span className="opacity-50 text-xs">—</span>}
        </td>
        <td className="text-xs opacity-60 whitespace-nowrap">
          <span className={tipClass} data-tip={`Computed by version ${f.codeVersion}. Catalogue row written by ${f.updatedBy}.`}>
            <span className="font-mono">{f.codeVersion}</span>
          </span>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={9} className="bg-base-200">
            <div className="p-3 text-sm space-y-2">
              {f.note && <p className="italic text-base-content/80">{f.note}</p>}
              <p className="text-xs text-base-content/70">
                Source <span className="font-mono">{f.source}</span> ·{' '}
                {f.enabled ? 'fed to the model' : 'defined but not fed to the model'} ·{' '}
                catalogue row updated {f.updatedAt.slice(0, 10)} by {f.updatedBy}
              </p>
              {coverage && (
                <p className="text-xs">
                  <span className="font-mono">{coverage.withValue.toLocaleString()}</span> bars with
                  a value,{' '}
                  <span className="font-mono">{coverage.storedNull.toLocaleString()}</span> stored
                  but not yet computable,{' '}
                  <span className="font-mono">{coverage.absent.toLocaleString()}</span> with no row
                  at all
                  {coverage.firstValue && (
                    <> · first value {coverage.firstValue.slice(0, 10)}</>
                  )}
                </p>
              )}
              {gapsBusy && <span className="loading loading-xs" />}
              {gapsError && <p className="text-error text-xs">{gapsError}</p>}
              {gaps && (
                gaps.totalGaps === 0
                  ? <p className="text-xs text-success">No gaps in this window.</p>
                  : (
                    <div className="text-xs">
                      <p className="mb-1">
                        {gaps.totalGaps.toLocaleString()} gap(s),{' '}
                        {gaps.missingBars.toLocaleString()} bars without a value
                        {gaps.truncated && ' — longest shown'}:
                      </p>
                      <ul className="font-mono space-y-0.5">
                        {gaps.items.map((g) => (
                          <li key={g.from}>
                            {g.from.slice(0, 10)} → {g.to.slice(0, 10)}
                            <span className="opacity-60"> ({g.bars} bars)</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}

/**
 * Coverage as a bar, with the three states kept visually distinct.
 *
 * A single percentage would read the same for "this feature starts in 2003" and
 * "this feature was never built", which are very different problems.
 */
function CoverageBar({ c }: { c: FeatureCoverage }) {
  if (c.bars === 0) return <span className="opacity-50 text-xs">no bars</span>
  const pct = (n: number) => `${(n / c.bars) * 100}%`
  const label = c.share === null ? '—' : `${(c.share * 100).toFixed(1)}%`
  return (
    <span
      className={`${tipClass} flex items-center gap-2`}
      data-tip={
        `${c.withValue.toLocaleString()} of ${c.bars.toLocaleString()} bars have a value. `
        + `${c.storedNull.toLocaleString()} stored without one (not yet computable), `
        + `${c.absent.toLocaleString()} with no row at all.`
      }
    >
      <span className="flex h-2 w-20 rounded-full overflow-hidden bg-base-300">
        <span className="bg-success" style={{ width: pct(c.withValue) }} />
        <span className="bg-warning" style={{ width: pct(c.storedNull) }} />
      </span>
      <span className="tabular-nums text-xs">{label}</span>
    </span>
  )
}
