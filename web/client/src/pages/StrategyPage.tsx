import { useMemo, useState } from 'react'
import {
  api, isAbortError,
  type FeatureCoverage, type Instrument, type ParamSpec, type SavedStrategy,
  type SavedStrategiesResponse,
} from '../lib/api'
import CoverageWarning, { FeatureListNotUsedNote } from '../components/CoverageWarning'
import FieldLabel, { tipClass } from '../components/FieldLabel'
import { hintFor, paramHint } from '../lib/fieldHints'
import { useApiData } from '../lib/useApiData'
import { useTimeframe } from '../lib/useTimeframe'
import {
  WINDOW_KEYS,
  classifyCoverage, designDefaultWindows, fitWindowsToSeries, horizonSpan, isRealDate,
  marginProblems, resolveFeatures,
  objectiveFromForm, objectiveToForm, windowDays, windowErrors,
  type ObjectiveForm, type WindowKey, type Windows,
} from '../lib/strategyForm'

/**
 * The Strategy tab — the builder (Architecture.md §8.1a / §8.2a).
 *
 * Compose a strategy *before* anything is trained: the feature subset, the four
 * date windows, the validation scheme, the model type, the label definition,
 * the hyperparameters, and the objective: the loss, the verdict metric, the cost
 * charged and where money parks. Saving stores a named, versioned record; Train executes
 * one and makes no choices of its own.
 *
 * Why this is a separate tab rather than more fields on Train: the Train page
 * trains on the whole stored series with no feature list and no window split,
 * so its result cannot be explained or compared. Those choices are a strategy;
 * training is the act of executing one. Keeping them apart also means one
 * strategy can be trained many times and the runs compared.
 *
 * Saving never overwrites. An edit stores a new version, so a run always points
 * at exactly the configuration it was trained from.
 *
 * Removing is a different act and is allowed: the no-overwrite rule protects a
 * run's provenance, and a strategy nothing was trained from has none to
 * protect. Remove deletes every version of a name, says so before doing it, and
 * leaves trained models and past runs untouched.
 */
export default function StrategyPage() {
  const [tf] = useTimeframe()

  // Bumped after a save or a remove so the saved list refetches.
  const [listVersion, setListVersion] = useState(0)
  const [removed, setRemoved] = useState<string | null>(null)

  const { data: saved, error: savedError, loading: savedLoading } =
    useApiData<SavedStrategiesResponse>(
      (signal) => api.savedStrategies({ latest: true }, signal), [listVersion],
    )
  const { data: instrumentsResp } = useApiData(
    (signal) => api.instruments(tf, signal), [tf],
  )
  // The feature catalogue. Its `config.columns` is the list a strategy picks
  // from; only enabled columns are offered, because a disabled one has no
  // values in the store to select.
  const { data: features } = useApiData((signal) => api.features(tf, signal), [tf])

  const instruments = useMemo(() => instrumentsResp?.items ?? [], [instrumentsResp])

  const [name, setName] = useState('')
  const [notes, setNotes] = useState('')
  const [modelType, setModelType] = useState('mlp')
  const [scheme, setScheme] = useState('walkforward')
  const [selected, setSelected] = useState<string[]>([])
  const [horizon, setHorizon] = useState('5')
  const [params, setParams] = useState<Record<string, string>>({})
  // Null until touched, then derived from the API's defaults like the windows.
  const [editedObjective, setEditedObjective] = useState<ObjectiveForm | null>(null)

  // The series fields and the windows are stored as "what the user chose, or
  // null for not yet chosen", and the effective value is derived during render.
  //
  // The alternative -- seed them from an effect once the lists arrive -- is
  // what `react-hooks/set-state-in-effect` flags, and this client already
  // removed that pattern once (it is why `useApiData` exists). Deriving also
  // means a picked instrument with no matching slice falls back visibly
  // instead of leaving the form in a state no effect reset.
  const [pickedSymbol, setPickedSymbol] = useState<string | null>(null)
  const [pickedSource, setPickedSource] = useState<string | null>(null)
  const [pickedTimeframe, setPickedTimeframe] = useState<string | null>(null)
  const [editedWindows, setEditedWindows] = useState<Windows | null>(null)

  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<SavedStrategy | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const instrument: Instrument | undefined =
    instruments.find((i) => i.symbol === pickedSymbol) ?? instruments[0]
  const symbol = instrument?.symbol ?? ''
  const sourcesFor = useMemo(
    () => [...new Set(instrument?.sources.map((s) => s.sourceName) ?? [])],
    [instrument],
  )
  const source = pickedSource && sourcesFor.includes(pickedSource)
    ? pickedSource
    : sourcesFor[0] ?? ''
  const timeframesFor = useMemo(
    () => (instrument?.sources.filter((s) => s.sourceName === source) ?? [])
      .map((s) => s.timeframe),
    [instrument, source],
  )
  const timeframe = pickedTimeframe && timeframesFor.includes(pickedTimeframe)
    ? pickedTimeframe
    : timeframesFor[0] ?? ''
  const slice = instrument?.sources.find(
    (s) => s.sourceName === source && s.timeframe === timeframe,
  )

  // Until a date is touched, offer the design's own split extended to the
  // newest stored bar. Derived, so changing instrument re-derives it.
  const windows: Windows = editedWindows ?? designDefaultWindows(slice?.toDate)
  // G21: the same four windows cut from this series' own whole years, offered
  // as a button rather than the default so the design's split stays the
  // starting point wherever the series can hold it.
  const fitted = slice ? fitWindowsToSeries(slice.fromDate, slice.toDate) : null
  const alreadyFitted = !!fitted && JSON.stringify(fitted) === JSON.stringify(windows)

  const enabledColumns = useMemo(
    () => (features?.config?.columns ?? []).filter((c) => c.enabled),
    [features],
  )

  // Bars per window. §8.2a asks for the bar count beside each window, and
  // calendar days are the wrong number — a year-long window holds ~252 trading
  // days, not 365.
  //
  // Counted by asking the candles endpoint for each window's range and reading
  // `total`, which is the row count BEFORE the response is strided down. The
  // obvious alternative — fetch the series once and count timestamps — is
  // silently wrong: that endpoint caps at 4000 bars and downsamples, so QQQ's
  // 6,930 daily bars come back as 3,466 at stride 2, and every window would
  // read about half its true size.
  const windowsKey = JSON.stringify(windows)
  const { data: barCounts } = useApiData<Partial<Record<WindowKey, number>>>(
    async (signal) => {
      if (!symbol || !source || !timeframe) return {}
      const out: Partial<Record<WindowKey, number>> = {}
      for (const key of WINDOW_KEYS) {
        const w = windows[key]
        if (!w || !isRealDate(w.from) || !isRealDate(w.to)) continue
        // `to` is inclusive on the server (`timestamp <= $n`), and a daily bar
        // is stored at midnight, so the bare date already includes that day.
        const r = await api.candles(
          { instrument: symbol, source, timeframe, from: w.from, to: w.to }, signal,
        )
        out[key] = r.total
      }
      return out
    },
    [symbol, source, timeframe, windowsKey],
  )

  // Coverage before training (§8.1a): "When features and windows are chosen,
  // the builder shows a coverage grid... A feature with no values in the train
  // window is flagged and dropped from the resolved list, as §6.2 requires; a
  // partial one is flagged but kept. Nothing is padded."
  //
  // Measured over the TRAIN window specifically: a feature absent there cannot
  // teach the model anything, whatever it does later. Matched to a built
  // feature set by (symbol, source, timeframe), because coverage is a question
  // about stored values rather than about config.
  const { data: catalogue } = useApiData((signal) => api.featureCatalogue(signal), [])
  const featureSet = useMemo(
    () => (catalogue?.sets ?? []).find(
      (st) => st.symbol === symbol && st.sourceName === source && st.timeframe === timeframe,
    ),
    [catalogue, symbol, source, timeframe],
  )
  const trainWindow = windows.train
  const { data: trainCoverage } = useApiData(
    (signal) => (featureSet && trainWindow
      && isRealDate(trainWindow.from) && isRealDate(trainWindow.to)
      ? api.featureCoverage(
        { set: featureSet.id, from: trainWindow.from, to: trainWindow.to }, signal,
      )
      : Promise.resolve(null)),
    [featureSet?.id, trainWindow?.from, trainWindow?.to],
  )

  const coverageByName = useMemo(() => {
    const m: Record<string, FeatureCoverage> = {}
    for (const c of trainCoverage?.items ?? []) m[c.name] = c
    return m
  }, [trainCoverage])

  // What a run would actually use, after §6.2 is applied to the train window.
  const resolved = useMemo(
    () => resolveFeatures(selected, coverageByName), [selected, coverageByName],
  )

  /**
   * Windows that hold no stored bars.
   *
   * A window can be perfectly well-formed and still contain nothing: the
   * design's 2003 train window against a series that only starts in 2019 is
   * zero bars. `windowErrors` cannot see this -- it checks dates, not data --
   * so it is a separate warning rather than a validation failure, and it stays
   * a warning because the dates may be right for a series not yet imported.
   */
  const emptyWindows = useMemo(
    () => WINDOW_KEYS.filter((k) => barCounts?.[k] === 0), [barCounts],
  )

  const windowProblems = windowErrors(windows)
  const paramSpec = saved?.paramSpec ?? {}
  const objectiveSpec = saved?.objectiveSpec
  const objectiveForm: ObjectiveForm | null = editedObjective
    ?? (objectiveSpec ? objectiveToForm(objectiveSpec.defaults) : null)
  const setObjective = (part: Partial<ObjectiveForm>) =>
    setEditedObjective((o) => ({ ...(o ?? objectiveForm!), ...part }))
  const marginIssues = objectiveSpec && objectiveForm
    ? marginProblems(objectiveForm, objectiveSpec, symbol)
    : []
  const ready = !!(name.trim() && slice && selected.length > 0
    && windowProblems.length === 0 && marginIssues.length === 0
    && objectiveSpec && objectiveForm && !busy)

  function setWindow(key: WindowKey, part: Partial<{ from: string, to: string }>) {
    setEditedWindows((w) => {
      const base = w ?? windows
      return { ...base, [key]: { ...(base[key] ?? { from: '', to: '' }), ...part } }
    })
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!ready || !slice || !objectiveSpec || !objectiveForm) return
    setBusy(true); setSubmitError(null); setResult(null)
    try {
      const hyperparams: Record<string, string> = {}
      for (const [k, v] of Object.entries(params)) if (v !== '') hyperparams[k] = v
      const stored = await api.saveStrategy({
        name: name.trim(),
        instrument: symbol,
        source,
        timeframe,
        modelType,
        validationScheme: scheme,
        features: selected,
        windows,
        label: { kind: 'forward_return_3class', horizonBars: Number(horizon) },
        hyperparams,
        objective: objectiveFromForm(objectiveForm, objectiveSpec),
        notes: notes.trim() || null,
      })
      setResult(stored)
      setListVersion((n) => n + 1)
    } catch (err) {
      if (!isAbortError(err as Error)) setSubmitError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  /** Remove every version of a saved strategy, after confirming what goes. */
  async function removeStrategy(s: SavedStrategy) {
    // Named in the prompt, and the version count stated: "remove baseline" is
    // a different act when there are six versions of it.
    const ok = window.confirm(
      `Remove "${s.name}"? This deletes every saved version of it `
      + `(currently showing v${s.version}).\n\n`
      + 'Trained models and past runs are not touched.',
    )
    if (!ok) return
    setSubmitError(null)
    try {
      const gone = await api.deleteStrategy(s.id, true)
      setListVersion((n) => n + 1)
      setResult(null)
      setRemoved(`Removed ${gone.name ?? s.name} — ${gone.versionsDeleted} version(s).`)
    } catch (err) {
      setSubmitError((err as Error).message)
    }
  }

  /** Load a saved strategy back into the form, to vary one choice at a time. */
  function cloneFrom(s: SavedStrategy) {
    setName(s.name)
    setNotes(s.notes ?? '')
    setPickedSymbol(s.instrument)
    setPickedSource(s.source)
    setPickedTimeframe(s.timeframe)
    setModelType(s.modelType); setScheme(s.validationScheme)
    setSelected(s.features)
    setEditedWindows(s.windows as Windows)
    setHorizon(String(s.label?.horizonBars ?? 5))
    setParams(Object.fromEntries(
      Object.entries(s.hyperparams).map(([k, v]) => [k, String(v)]),
    ))
    setEditedObjective(objectiveToForm(s.objective))
    setResult(null); setSubmitError(null)
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold mb-1">Strategy</h1>
      <p className="text-base-content/70 mb-6 max-w-3xl">
        Compose a strategy before anything is trained. Everything a run decides lives here —
        which features go in, which bars are used to fit, to tune, to score and to predict —
        so a result can be explained and two runs can be compared. Saving stores a new
        version rather than replacing one, so a trained model always points at exactly what
        it learned from.
      </p>

      <div className="grid gap-6 lg:grid-cols-[18rem_1fr]">
        <SavedList
          saved={saved}
          loading={savedLoading}
          error={savedError}
          onClone={cloneFrom}
          onRemove={removeStrategy}
        />

        <form onSubmit={submit} className="space-y-5">
          <Section title="1 · Name it">
            <div className="grid gap-4 md:grid-cols-2">
              <label className="form-control">
                <FieldLabel
                  text="Strategy name"
                  hint={hintFor('strategy.name')}
                />
                <input
                  className="input input-bordered" value={name} placeholder="baseline"
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
              <label className="form-control">
                <FieldLabel
                  text="Notes"
                  hint={hintFor('strategy.notes')}
                />
                <input
                  className="input input-bordered" value={notes}
                  placeholder="longer lookback, same features"
                  onChange={(e) => setNotes(e.target.value)}
                />
              </label>
            </div>
          </Section>

          <Section title="2 · What to learn from">
            <div className="grid gap-4 md:grid-cols-3">
              <label className="form-control">
                <FieldLabel text="Instrument" hint={hintFor('strategy.instrument')} />
                <select
                  className="select select-bordered" value={symbol}
                  onChange={(e) => {
                    // Clear the source and timeframe so they re-derive from
                    // the new instrument's own slices.
                    setPickedSymbol(e.target.value)
                    setPickedSource(null)
                    setPickedTimeframe(null)
                  }}
                >
                  {instruments.map((i) => <option key={i.id} value={i.symbol}>{i.symbol}</option>)}
                </select>
              </label>
              <label className="form-control">
                <FieldLabel text="Source" hint={hintFor('strategy.source')} />
                <select
                  className="select select-bordered" value={source}
                  onChange={(e) => { setPickedSource(e.target.value); setPickedTimeframe(null) }}
                >
                  {sourcesFor.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
              <label className="form-control">
                <FieldLabel text="Timeframe" hint={hintFor('strategy.timeframe')} />
                <select
                  className="select select-bordered" value={timeframe}
                  onChange={(e) => setPickedTimeframe(e.target.value)}
                >
                  {timeframesFor.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
            </div>
            {slice && (
              <p className="text-sm text-base-content/60 mt-3">
                Stored: <span className="font-mono">{slice.candleCount.toLocaleString()}</span> bars,{' '}
                {slice.fromDate.slice(0, 10)} to {slice.toDate.slice(0, 10)}.
              </p>
            )}
          </Section>

          <Section
            title="3 · Features"
            note="Pick the subset the network sees. The list is the feature table's enabled columns; a run records exactly what went in, so two runs with different features are two different experiments."
          >
            <FeaturePicker
              columns={enabledColumns}
              selected={selected}
              onChange={setSelected}
              coverage={coverageByName}
            />
            <CoverageWarning
              resolved={resolved}
              haveCoverage={!!trainCoverage}
              hasSet={!!featureSet}
              bars={trainCoverage?.bars ?? 0}
              coverage={coverageByName}
            />
            <FeatureListNotUsedNote />
          </Section>

          <Section
            title="4 · Windows"
            note="Train fits the weights, validation tunes without fitting, test is scored once, prediction is the forward window. They must not overlap and must run in that order."
          >
            <div className="space-y-3">
              {WINDOW_KEYS.map((key) => {
                const w = windows[key]
                const bars = barCounts?.[key] ?? null
                const days = windowDays(w)
                return (
                  <div key={key} className="grid gap-2 sm:grid-cols-[8rem_1fr_1fr_auto] items-center">
                    <span className={tipClass} data-tip={hintFor(`window.${key}`)}>
                      <span className="text-sm capitalize cursor-help underline decoration-dotted decoration-base-content/30 underline-offset-4">
                        {key}
                        {key === 'prediction' && <span className="opacity-50"> (optional)</span>}
                      </span>
                    </span>
                    <input
                      type="date" className="input input-bordered input-sm"
                      value={w?.from ?? ''}
                      onChange={(e) => setWindow(key, { from: e.target.value })}
                    />
                    <input
                      type="date" className="input input-bordered input-sm"
                      value={w?.to ?? ''}
                      onChange={(e) => setWindow(key, { to: e.target.value })}
                    />
                    <span
                      className={`text-xs tabular-nums whitespace-nowrap ${
                        bars === 0 ? 'text-error font-medium' : 'text-base-content/60'
                      }`}
                    >
                      {bars !== null
                        ? `${bars.toLocaleString()} bars${days ? ` / ${days.toLocaleString()} days` : ''}`
                        : '—'}
                      {bars === 0 && ' ← empty'}
                    </span>
                  </div>
                )
              })}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <button
                type="button" className="btn btn-xs"
                disabled={!fitted || alreadyFitted}
                onClick={() => fitted && setEditedWindows(fitted)}
              >
                Fit windows to this series
              </button>
              <span className="text-xs text-base-content/60">
                {!slice
                  ? 'Pick a series first.'
                  : !fitted
                    ? `${symbol}/${source}/${timeframe} holds fewer than three whole years, one each for train, validation and test.`
                    : alreadyFitted
                      ? 'The windows are fitted to this series.'
                      : `Splits ${slice.fromDate.slice(0, 10)} → ${slice.toDate.slice(0, 10)} into whole years, about 55% train, 25% validation and 20% test, with any part year left as prediction.`}
              </span>
            </div>
            {windowProblems.length > 0 && (
              <ul className="mt-3 text-sm text-error list-disc list-inside space-y-1">
                {windowProblems.map((p) => <li key={p}>{p}</li>)}
              </ul>
            )}
            {emptyWindows.length > 0 && (
              <div className="alert alert-error py-2 mt-3">
                <div className="text-xs">
                  <p className="font-medium">
                    {emptyWindows.join(', ')} hold no stored bars for{' '}
                    <span className="font-mono">{symbol}/{source}/{timeframe}</span>.
                  </p>
                  <p className="mt-1">
                    {slice && (
                      <>The series runs {slice.fromDate.slice(0, 10)} →{' '}
                        {slice.toDate.slice(0, 10)}. </>
                    )}
                    {emptyWindows.includes('train')
                      ? 'A model fitted on an empty train window learns nothing, and nothing '
                        + 'downstream would say so.'
                      : 'That window cannot be scored.'}
                    {' '}The dates are still saveable — this is a warning, not a block — but
                    they are almost certainly wrong for this series.
                    {fitted && ' "Fit windows to this series" proposes ones that are not.'}
                  </p>
                </div>
              </div>
            )}
          </Section>

          <Section title="5 · How to fit it">
            <div className="grid gap-4 md:grid-cols-3">
              <label className="form-control">
                <FieldLabel text="Model type" hint={hintFor('strategy.modelType')} />
                <select
                  className="select select-bordered" value={modelType}
                  onChange={(e) => setModelType(e.target.value)}
                >
                  {(saved?.modelTypes ?? ['mlp']).map((m) => (
                    <option key={m} value={m}>{m.toUpperCase()}</option>
                  ))}
                </select>
              </label>
              <label className="form-control">
                <FieldLabel text="Validation scheme" hint={hintFor('strategy.validationScheme')} />
                <select
                  className="select select-bordered" value={scheme}
                  onChange={(e) => setScheme(e.target.value)}
                >
                  {(saved?.validationSchemes ?? ['walkforward']).map((s) => (
                    <option key={s} value={s}>{s.replace('_', ' ')}</option>
                  ))}
                </select>
              </label>
              <label className="form-control">
                <FieldLabel text="Label horizon (bars)" hint={hintFor('strategy.labelHorizon')} />
                <input
                  type="number" min={1} max={100} className="input input-bordered"
                  value={horizon} onChange={(e) => setHorizon(e.target.value)}
                />
                {/* I8: bars are not days below D1, so say what the number means here. */}
                {horizonSpan(Number(horizon), timeframe) && (
                  <span className="label-text-alt mt-1 text-base-content/60">
                    {horizonSpan(Number(horizon), timeframe)}
                  </span>
                )}
              </label>
            </div>
          </Section>

          <Section
            title="6 · Hyperparameters"
            note="Leave a field blank to use the model's default. Bounds are the ones the API enforces, so nothing here can be saved that training would later reject."
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(paramSpec).map(([key, spec]: [string, ParamSpec]) => (
                <label key={key} className="form-control">
                  <FieldLabel
                    className="text-xs"
                    text={spec.label}
                    hint={paramHint(key, spec)}
                  />
                  <input
                    className="input input-bordered input-sm"
                    inputMode="decimal"
                    placeholder="default"
                    value={params[key] ?? ''}
                    onChange={(e) => setParams((p) => ({ ...p, [key]: e.target.value }))}
                  />
                </label>
              ))}
            </div>
          </Section>

          {objectiveSpec && objectiveForm && (
            <Section
              title="7 · Objective and costs"
              note="What the model is trained to want, the metric that decides whether a run beat holding, and what each trade costs. Every value is saved with the strategy, so a run cannot be charged something its record does not show."
            >
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                <label className="form-control">
                  <FieldLabel className="text-xs" text="Loss" hint={hintFor('objective.loss')} />
                  <select
                    className="select select-bordered select-sm" value={objectiveForm.loss}
                    onChange={(e) => setObjective({ loss: e.target.value as ObjectiveForm['loss'] })}
                  >
                    {objectiveSpec.losses.map((l) => <option key={l} value={l}>{l}</option>)}
                  </select>
                </label>
                {objectiveForm.loss === 'ce' ? (
                  <label className="form-control">
                    <FieldLabel
                      className="text-xs" text="Class weights" hint={hintFor('objective.classWeights')}
                    />
                    <input
                      type="checkbox" className="checkbox checkbox-sm mt-2"
                      checked={objectiveForm.classWeights}
                      onChange={(e) => setObjective({ classWeights: e.target.checked })}
                    />
                  </label>
                ) : (
                  <label className="form-control">
                    <FieldLabel
                      className="text-xs" text="Position threshold"
                      hint={hintFor('objective.positionThreshold')}
                    />
                    <input
                      className="input input-bordered input-sm" inputMode="decimal"
                      placeholder={String(objectiveSpec.defaultPositionThreshold)}
                      value={objectiveForm.positionThreshold}
                      onChange={(e) => setObjective({ positionThreshold: e.target.value })}
                    />
                  </label>
                )}
                <label className="form-control">
                  <FieldLabel className="text-xs" text="Verdict metric" hint={hintFor('objective.verdict')} />
                  <select
                    className="select select-bordered select-sm" value={objectiveForm.verdict}
                    onChange={(e) => setObjective({ verdict: e.target.value as ObjectiveForm['verdict'] })}
                  >
                    {objectiveSpec.verdicts.map((v) => <option key={v} value={v}>{v}</option>)}
                  </select>
                </label>
                <label className="form-control">
                  <FieldLabel className="text-xs" text="Cost (bps per change)" hint={hintFor('objective.costBps')} />
                  <input
                    className="input input-bordered input-sm" inputMode="decimal"
                    placeholder={String(objectiveSpec.defaults.costBps)}
                    value={objectiveForm.costBps}
                    onChange={(e) => setObjective({ costBps: e.target.value })}
                  />
                </label>
                <label className="form-control">
                  <FieldLabel className="text-xs" text="Park money in" hint={hintFor('objective.park')} />
                  <select
                    className="select select-bordered select-sm" value={objectiveForm.park}
                    onChange={(e) => setObjective({ park: e.target.value as ObjectiveForm['park'] })}
                  >
                    {objectiveSpec.parks.map((v) => <option key={v} value={v}>{v}</option>)}
                  </select>
                </label>
                <label className="form-control">
                  <FieldLabel
                    className="text-xs" text="Score after tax" hint={hintFor('objective.taxScore')}
                  />
                  <input
                    type="checkbox" className="toggle toggle-sm mt-2"
                    checked={objectiveForm.taxScore}
                    onChange={(e) => setObjective({ taxScore: e.target.checked })}
                  />
                </label>
                {objectiveForm.taxScore && (
                  <label className="form-control">
                    <FieldLabel
                      className="text-xs" text="Tax rate (%)" hint={hintFor('objective.taxRatePct')}
                    />
                    <input
                      className="input input-bordered input-sm" inputMode="decimal"
                      placeholder="e.g. 25"
                      value={objectiveForm.taxRatePct}
                      onChange={(e) => setObjective({ taxRatePct: e.target.value })}
                    />
                  </label>
                )}
                {objectiveForm.taxScore && objectiveForm.loss !== 'ce' && (
                  <label className="form-control">
                    <FieldLabel
                      className="text-xs" text="Train with tax" hint={hintFor('objective.taxTrain')}
                    />
                    <input
                      type="checkbox" className="toggle toggle-sm toggle-warning mt-2"
                      checked={objectiveForm.taxTrain}
                      onChange={(e) => setObjective({ taxTrain: e.target.checked })}
                    />
                    {objectiveForm.taxTrain && (
                      <span className="text-xs text-warning mt-1">
                        Changes the signals. Not for a strategy published as a signal.
                      </span>
                    )}
                  </label>
                )}
                <label className="form-control">
                  <FieldLabel className="text-xs" text="Execution" hint={hintFor('objective.execution')} />
                  <select
                    className="select select-bordered select-sm" value={objectiveForm.execution}
                    onChange={(e) => setObjective({ execution: e.target.value as ObjectiveForm['execution'] })}
                  >
                    <option value="signals">Strategy 1 · signals (long unless sell)</option>
                    <option value="margin">Strategy 2 · margin long and inverse</option>
                  </select>
                </label>
                {objectiveForm.execution === 'margin' && (
                  <>
                    {([
                      ['leveragePct', 'Leverage (% of core)', objectiveSpec.marginDefaults.leveragePct],
                      ['hedgePct', 'Hedge in PSQ (% of core)', objectiveSpec.marginDefaults.hedgePct],
                      ['marginSpreadPct', 'Margin rate (Fed funds +)', objectiveSpec.marginDefaults.marginSpreadPct],
                      ['maintenancePct', 'Margin call below (% equity)', objectiveSpec.marginDefaults.maintenancePct],
                    ] as const).map(([key, label, def]) => (
                      <label key={key} className="form-control">
                        <FieldLabel className="text-xs" text={label} hint={hintFor(`objective.${key}`)} />
                        <input
                          className="input input-bordered input-sm" inputMode="decimal"
                          placeholder={String(def)}
                          value={objectiveForm[key]}
                          onChange={(e) => setObjective({ [key]: e.target.value })}
                        />
                      </label>
                    ))}
                    {objectiveForm.loss !== 'ce' && !objectiveForm.taxTrain && (
                      <label className="form-control">
                        <FieldLabel
                          className="text-xs" text="Train with margin" hint={hintFor('objective.marginTrain')}
                        />
                        <input
                          type="checkbox" className="toggle toggle-sm toggle-warning mt-2"
                          checked={objectiveForm.marginTrain}
                          onChange={(e) => setObjective({ marginTrain: e.target.checked })}
                        />
                        {objectiveForm.marginTrain && (
                          <span className="text-xs text-warning mt-1">
                            Changes the signals, and training must start in 2007 or later.
                          </span>
                        )}
                      </label>
                    )}
                    {objectiveForm.loss !== 'ce' && (
                      <label className="form-control">
                        <FieldLabel
                          className="text-xs" text="Long threshold" hint={hintFor('objective.longThreshold')}
                        />
                        <input
                          className="input input-bordered input-sm" inputMode="decimal"
                          placeholder={String(objectiveSpec.defaultLongThreshold)}
                          value={objectiveForm.longThreshold}
                          onChange={(e) => setObjective({ longThreshold: e.target.value })}
                        />
                      </label>
                    )}
                  </>
                )}
              </div>
              {marginIssues.length > 0 && (
                <ul className="mt-3 space-y-1">
                  {marginIssues.map((m) => <li key={m} className="text-sm text-error">{m}</li>)}
                </ul>
              )}
              {objectiveForm.execution === 'margin' && marginIssues.length === 0 && (
                <p className="mt-3 text-sm text-base-content/70">
                  The QQQ core is never sold. Interest is Fed funds on each day plus the margin
                  rate, charged while money is borrowed; gains on the margin positions are taxed
                  when scoring after tax. Scored windows must start on or after PSQ&apos;s first
                  bar, 2006-06-21.
                </p>
              )}
            </Section>
          )}

          <div className="flex items-center gap-3">
            <button type="submit" className="btn btn-primary" disabled={!ready}>
              {busy ? <span className="loading loading-spinner loading-sm" /> : 'Save strategy'}
            </button>
            <span className="text-sm text-base-content/60">
              {selected.length === 0
                ? 'Pick at least one feature.'
                : `${selected.length} feature${selected.length === 1 ? '' : 's'} selected.`}
            </span>
          </div>

          {submitError && (
            <div className="alert alert-error">
              <span className="whitespace-pre-wrap">{submitError}</span>
            </div>
          )}
          {removed && (
            <div className="alert alert-info">
              <span>{removed}</span>
            </div>
          )}
          {result && (
            <div className="alert alert-success">
              <span>
                Saved <span className="font-mono">{result.name}</span> as version{' '}
                <span className="font-mono">v{result.version}</span> — {result.features.length}{' '}
                features, by {result.createdBy}. Train can run it now.
              </span>
            </div>
          )}
        </form>
      </div>
    </div>
  )
}

function Section(
  { title, note, children }: { title: string, note?: string, children: React.ReactNode },
) {
  return (
    <section className="bg-base-200 rounded-box p-4">
      <h2 className="font-medium mb-1">{title}</h2>
      {note && <p className="text-sm text-base-content/60 mb-3 max-w-3xl">{note}</p>}
      {children}
    </section>
  )
}

/**
 * The saved strategies, newest version of each. Clicking one loads it into the
 * form so a strategy can be cloned and one choice varied at a time.
 */
function SavedList(
  { saved, loading, error, onClone, onRemove }: {
    saved: SavedStrategiesResponse | null
    loading: boolean
    error: string | null
    onClone: (s: SavedStrategy) => void
    onRemove: (s: SavedStrategy) => void
  },
) {
  return (
    <aside className="bg-base-200 rounded-box p-4 h-fit">
      <h2 className="font-medium mb-2">Saved</h2>
      {loading && <span className="loading loading-spinner loading-sm" />}
      {error && <p className="text-sm text-error">{error}</p>}
      {saved && saved.items.length === 0 && (
        <p className="text-sm text-base-content/60">
          Nothing saved yet. Fill the form and save to create the first one.
        </p>
      )}
      <ul className="space-y-2">
        {(saved?.items ?? []).map((s) => (
          <li key={s.id} className="flex items-start gap-1 group">
            <button
              type="button"
              onClick={() => onClone(s)}
              className="text-left flex-1 rounded-btn px-3 py-2 hover:bg-base-300 transition-colors"
              title="Load this strategy into the form"
            >
              <span className="font-mono text-sm">{s.name}</span>
              <span className="badge badge-ghost badge-sm ml-2">v{s.version}</span>
              <span className="block text-xs text-base-content/60 mt-0.5">
                {s.instrument} · {s.timeframe} · {s.features.length} features
              </span>
            </button>
            <button
              type="button"
              onClick={() => onRemove(s)}
              className="btn btn-ghost btn-xs text-base-content/40 hover:text-error mt-1"
              aria-label={`Remove ${s.name}`}
              title={`Remove every version of ${s.name}`}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
    </aside>
  )
}

/**
 * Coverage marks on the picker, so a feature's state is visible before it is
 * chosen rather than after a confusing training result.
 */
const COVERAGE_MARK: Record<string, { glyph: string, cls: string, why: string }> = {
  full: {
    glyph: '●',
    cls: 'text-success',
    why: 'Has a value on every bar of the train window.',
  },
  partial: {
    glyph: '◐',
    cls: 'text-warning',
    why: 'Has gaps in the train window. Kept, not dropped — a short history is not a '
      + 'disqualification and nothing is padded — but the model sees fewer complete '
      + 'samples than you might assume.',
  },
  empty: {
    glyph: '○',
    cls: 'text-error',
    why: 'Not populated anywhere in the train window. A run would drop it: a feature '
      + 'with no values there cannot teach the model anything.',
  },
  unknown: {
    glyph: '·',
    cls: 'text-base-content/30',
    why: 'No built feature set for this series, so coverage is unknown. Build one and '
      + 'the marks fill in.',
  },
}

/** Checkbox list of the feature catalogue, with select-all, a count and coverage. */
function FeaturePicker(
  { columns, selected, onChange, coverage }: {
    columns: { name: string, method: string, note: string | null, width: number }[]
    selected: string[]
    onChange: (next: string[]) => void
    coverage: Record<string, FeatureCoverage>
  },
) {
  const all = columns.map((c) => c.name)
  const allSelected = all.length > 0 && all.every((n) => selected.includes(n))

  return (
    <div>
      <div className="flex items-center gap-3 mb-2">
        <button
          type="button" className="btn btn-xs"
          onClick={() => onChange(allSelected ? [] : all)}
        >
          {allSelected ? 'Clear all' : 'Select all'}
        </button>
        <span className="text-xs text-base-content/60">
          {selected.length} of {all.length}
        </span>
      </div>
      <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3 max-h-80 overflow-y-auto pr-1">
        {columns.map((c) => {
          const cov = coverage[c.name]
          const state = cov ? classifyCoverage(cov) : 'unknown'
          const mark = COVERAGE_MARK[state]
          const pct = cov && cov.share !== null
            ? ` ${(cov.share * 100).toFixed(0)}% of the train window.`
            : ''
          return (
            <label
              key={c.name}
              className={`${tipClass} flex items-start gap-2 text-sm py-1 cursor-pointer`}
              data-tip={`${mark.why}${pct} ${c.note ?? `Normalized with ${c.method}.`}`}
            >
              <input
                type="checkbox"
                className="checkbox checkbox-sm mt-0.5"
                checked={selected.includes(c.name)}
                onChange={(e) => onChange(
                  e.target.checked
                    ? [...selected, c.name]
                    : selected.filter((n) => n !== c.name),
                )}
              />
              <span className={`${mark.cls} leading-5`} aria-hidden>{mark.glyph}</span>
              <span className="font-mono text-xs leading-5">
                {c.name}
                {c.width > 1 && <span className="opacity-50"> ×{c.width}</span>}
              </span>
            </label>
          )
        })}
      </div>
    </div>
  )
}
