import { api, type TrainedModel } from '../../lib/api'
import { useApiData } from '../../lib/useApiData'
import { useTimeframe } from '../../lib/useTimeframe'
import StageShell, { NotWired, type StageState } from './StageShell'

type Loaded = { items: TrainedModel[], modelsDir: string }

/**
 * Stage 5 — training.
 *
 * The design wants this tab followed in real time: current fold, epoch, the
 * loss curve updating, validation-fold results against buy-and-hold as they
 * land. None of that is recorded anywhere yet — the walk-forward runner prints
 * to a terminal and nothing persists per-epoch progress — so this shows the
 * models that exist and says plainly that the live view is missing, rather
 * than implying a run is being watched when it is not.
 */
export default function TrainingStage() {
  const [tf] = useTimeframe()
  const { data, error } = useApiData<Loaded>((signal) => api.models(tf, signal), [tf])

  if (error) return <div className="alert alert-error">{error}</div>
  if (!data) return <span className="loading loading-spinner" />

  const models = data.items
  const state: StageState = models.length === 0 ? 'empty' : 'unknown'

  return (
    <StageShell
      state={state}
      summary={
        models.length === 0
          ? `No trained models for ${tf}`
          : `${models.length} trained model${models.length === 1 ? '' : 's'} on disk`
      }
      detail={
        models.length === 0
          ? 'Nothing has been trained at this timeframe yet.'
          : 'Whether a model beat buy-and-hold on its validation folds is not recorded with the model, so this stage cannot report a pass or fail. Models with no recorded timeframe are listed at every timeframe.'
      }
    >
      <NotWired
        what="Live training progress"
        how={'The runner prints fold, epoch and loss to its terminal and persists none of it. '
          + 'A progress table written during the run would be what this tab reads.'}
      />

      <div className="overflow-x-auto bg-base-200 rounded-box mt-6">
        <table className="table table-sm">
          <thead>
            <tr>
              <th>Model</th>
              <th>Strategy</th>
              <th>Instrument</th>
              <th>Timeframe</th>
              <th className="text-right">Bars</th>
              <th className="text-right">Val. acc</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.versionId ?? m.cacheKey}>
                <td className="font-mono text-xs">{(m.versionId ?? m.cacheKey ?? '').slice(0, 16)}</td>
                <td className="text-xs">{m.strategyName ?? '—'}</td>
                <td className="font-mono text-xs">{m.instrumentSymbol ?? '—'}</td>
                <td className="text-xs">
                  {m.timeframe
                    ? <span className="font-mono">{m.timeframe}</span>
                    : <span className="opacity-60" title="The loader does not record it">not recorded</span>}
                </td>
                <td className="text-right">{m.trainingBarCount?.toLocaleString() ?? '—'}</td>
                <td className="text-right">
                  {m.validationAccuracyPct != null ? `${m.validationAccuracyPct.toFixed(1)}%` : '—'}
                </td>
                <td className="text-xs">
                  {m.createdAt ? new Date(m.createdAt).toLocaleString() : '—'}
                </td>
              </tr>
            ))}
            {models.length === 0 && (
              <tr>
                <td colSpan={7} className="text-center text-base-content/60">
                  Nothing trained at {tf}.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="text-sm text-base-content/60 mt-3 font-mono">{data.modelsDir}</p>
    </StageShell>
  )
}
