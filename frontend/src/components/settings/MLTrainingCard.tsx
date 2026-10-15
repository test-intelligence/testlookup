import { useState } from 'react'
import { Cpu, Loader2 } from 'lucide-react'
import { appSettingsService } from '@/services/appSettingsService'
import { useMLStatus } from '@/hooks/useAIConfig'
import { shortAgo } from '@/utils/formatters'
import { NIGHTLY_COPY, NIGHTLY_OFF_COPY, modelLine, refusalMessage, samplesLine } from './mlTrainingCopy'

/**
 * The ML classifier on Settings › AI (W04 AIC-05): how many labelled samples
 * exist against the floor, the model in service, and — for an ADMIN — "Train
 * now", with why it cannot run and what the last attempt did.
 *
 * Replaces the old banner that hard-coded "/ 200" and implied a model would
 * train by itself; none ever did (the trainer had no caller). Data comes from
 * `GET /settings/ai/ml/status`, which this hook polls every 3 s while a job is
 * queued or running, so "Training…" turns into the model line without a reload.
 */

const FAIL_TONE = 'text-[var(--status-failed)]'
const MUTED = 'text-[var(--color-text-muted)]'

export default function MLTrainingCard({ isAdmin }: { isAdmin: boolean }) {
  const { data: status, error, mutate } = useMLStatus()
  const [pending, setPending] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  if (!status) {
    return (
      <div data-ml-training-card="" className="rounded-lg border border-[var(--color-border)] px-3 py-2 text-xs">
        <span className={MUTED}>
          {error ? 'Could not read the ML training status from the API.' : 'Reading ML training status…'}
        </span>
      </div>
    )
  }

  const trainingState = status.training?.state ?? 'idle'
  const busy = pending || trainingState !== 'idle'
  const lastRun = status.last_run
  const showLastRun = isAdmin && !busy && lastRun != null && lastRun.status !== 'trained'
  const cannotTrain = refusal ?? (!status.can_train ? status.cannot_train_message : null)

  async function trainNow() {
    setPending(true)
    setRefusal(null)
    try {
      await appSettingsService.retrainML()
    } catch (err) {
      setRefusal(refusalMessage(err))
    } finally {
      try {
        await mutate()
      } finally {
        setPending(false)
      }
    }
  }

  return (
    <div
      data-ml-training-card=""
      data-ml-training-state={trainingState}
      className="rounded-lg border border-[var(--color-border)] px-3 py-3 space-y-2"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h4 className="text-xs font-semibold text-[var(--color-text-secondary)] flex items-center gap-1.5">
            <Cpu className="h-3.5 w-3.5" aria-hidden="true" /> ML classifier
          </h4>
          <p data-ml-samples="" className="text-xs text-[var(--color-text)] break-words">
            {samplesLine(status)}
          </p>
          {status.model_available && (
            <p data-ml-model-line="" className={`text-xs ${MUTED} break-words`} title={status.trained_at ?? undefined}>
              {modelLine(status)}
            </p>
          )}
        </div>
        {isAdmin && (
          <button
            type="button"
            data-ml-train-now=""
            onClick={() => { void trainNow() }}
            disabled={busy || !status.can_train}
            title={!status.can_train ? status.cannot_train_message ?? undefined : undefined}
            className="btn-secondary text-xs flex items-center gap-1.5 flex-shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
            {busy ? 'Training…' : 'Train now'}
          </button>
        )}
      </div>

      {isAdmin && cannotTrain && (
        <p data-ml-cannot-train="" className={`text-xs ${refusal ? FAIL_TONE : MUTED}`}>
          {cannotTrain}
        </p>
      )}

      {showLastRun && lastRun && (
        <p
          data-ml-last-run=""
          data-ml-last-run-status={lastRun.status}
          className={`text-xs ${lastRun.status === 'error' ? FAIL_TONE : MUTED} break-words`}
          title={lastRun.finished_at}
        >
          Last attempt {shortAgo(lastRun.finished_at)}: {lastRun.message ?? lastRun.status}
        </p>
      )}

      {isAdmin && (
        <p className={`text-xs ${MUTED}`} data-ml-schedule="">
          {status.retrain_enabled ? NIGHTLY_COPY : NIGHTLY_OFF_COPY}
        </p>
      )}
    </div>
  )
}
