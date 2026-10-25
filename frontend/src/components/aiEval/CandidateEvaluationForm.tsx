/**
 * Evaluate candidate (W16 AGT-09 b): an in-page form (InlineFormSection, in the dataset row's slot) that queues a
 * REPLAY of the dataset through the real engine of one tier — rules, ML, or the configured language model under the
 * offline ceiling — and scores the stored outputs (`POST /ai-eval/runs/evaluate/{dataset_id}` with a body, ADMIN).
 *
 * The tiers come from `GET /ai-eval/candidate-options`: only the task type's tiers are offered, an unavailable tier
 * is disabled with the server's sentence (`No ML model is trained yet…`, `Offline mode is on and the configured
 * model is not local (…)`). The candidate's `model` / `prompt_version` are ASSERTIONS of what this form showed
 * (sent for the llm tier only): a deployment whose configuration changed in between is refused with the server's
 * sentence, inline. The replay uses the pinned project's LLM budget and release policy: All Projects → Start is
 * disabled with the sentence that says so.
 */
import { useId, useMemo, useState } from 'react'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import { useCandidateOptions, useEvaluateCandidate } from '@/hooks/useAiEval'
import type { AIEvalDataset } from '@/services/aiEvalService'
import type { EvalCandidate } from '@/types/aiEval'
import { COPY, candidateLabel, refusalText } from './aiEvalModel'

export interface CandidateEvaluationFormProps {
  dataset: AIEvalDataset
  /** The pinned project (null under All Projects: nothing can start). */
  projectId: string | null
  slot?: InlineSlot
  onClose: () => void
  /** The queued run's id (the row then shows its runs list, polling). */
  onQueued: (runId: string) => void
}

export const OFFLINE_NOTE = 'Offline mode is on: only rules, ML or a local model can be evaluated.'

export default function CandidateEvaluationForm({ dataset, projectId, slot, onClose, onQueued }: CandidateEvaluationFormProps) {
  const id = useId()
  const { data: options, error, isLoading, mutate } = useCandidateOptions(true)
  const evaluateCandidate = useEvaluateCandidate()
  const supported = options?.supported_tiers_by_task?.[dataset.task_type]
  const tiers = useMemo(() => {
    const all = Array.isArray(options?.tiers) ? options.tiers : []
    return Array.isArray(supported) ? all.filter((option) => supported.includes(option.tier)) : all
  }, [options, supported])
  const maxItems = Math.max(1, Math.min(options?.max_items ?? 200, dataset.item_count || 200))
  const [picked, setPicked] = useState<string | null>(null)
  const [items, setItems] = useState<string>(() => String(Math.min(dataset.item_count || 1, 200)))
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const firstAvailable = tiers.find((option) => option.available)?.tier ?? null
  const tier = picked ?? firstAvailable
  const option = tiers.find((o) => o.tier === tier)
  const limit = Number(items)
  const limitValid = Number.isInteger(limit) && limit >= 1 && limit <= maxItems
  const canStart = !!projectId && !!option?.available && limitValid && !busy

  const start = async () => {
    if (!projectId || !option) return
    setBusy(true)
    setRefusal(null)
    const candidate: EvalCandidate =
      option.tier === 'llm'
        ? {
            tier: option.tier,
            ...(option.model ? { model: option.model } : {}),
            ...(options?.prompt_version ? { prompt_version: options.prompt_version } : {}),
          }
        : { tier: option.tier }
    try {
      const accepted = await evaluateCandidate(dataset.id, { candidate, project_id: projectId, limit })
      onQueued(accepted.run_id)
    } catch (err) {
      setRefusal(refusalText(err, 'The evaluation was not queued.'))
    } finally {
      setBusy(false)
    }
  }

  let body
  if (isLoading) {
    body = <LoadingSpinner size="sm" />
  } else if (error || !options) {
    body = (
      <p className="m-0 text-xs text-[var(--color-text-muted)]">
        {COPY.loadError.options}{' '}
        <button type="button" className="btn-secondary btn-sm" onClick={() => { void mutate() }}>Retry</button>
      </p>
    )
  } else {
    body = (
      <div className="space-y-3">
        <fieldset className="m-0 space-y-1.5 border-0 p-0">
          <legend className="mb-1 text-xs font-medium text-[var(--color-text-secondary)]">Tier</legend>
          {tiers.length === 0 && <p className="m-0 text-xs text-[var(--color-text-muted)]">No tier can replay this task type.</p>}
          {tiers.map((o) => (
            <label key={o.tier} data-candidate-tier={o.tier} className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name={`${id}-tier`}
                value={o.tier}
                checked={tier === o.tier}
                disabled={!o.available}
                onChange={() => setPicked(o.tier)}
                className="mt-1"
              />
              <span className="min-w-0">
                <span className={o.available ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)]'}>{candidateLabel(o)}</span>
                {!o.available && o.reason && (
                  <span className="block text-xs text-[var(--color-text-muted)]">{o.reason}</span>
                )}
              </span>
            </label>
          ))}
        </fieldset>
        {options.offline_mode && <p className="m-0 text-xs text-[var(--color-text-muted)]">{OFFLINE_NOTE}</p>}
        <label className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
          Items
          <input
            type="number"
            className="input w-24 text-sm"
            min={1}
            max={maxItems}
            value={items}
            onChange={(event) => setItems(event.target.value)}
          />
          <span className="text-[var(--color-text-muted)]">of {dataset.item_count} (at most {maxItems})</span>
        </label>
        {!projectId && (
          <p data-testid="candidate-project-note" className="m-0 max-w-[60ch] text-xs text-[var(--ui-warning-ink)]">
            {COPY.allProjectsReplay}
          </p>
        )}
        {refusal && <p role="alert" className="m-0 text-xs text-[var(--ui-danger-ink)]">{refusal}</p>}
      </div>
    )
  }

  return (
    <InlineFormSection
      title="Evaluate candidate"
      headingLevel={3}
      description={`Replay ${dataset.name} through one tier's real engine and score its outputs.`}
      onClose={onClose}
      canClose={!busy}
      closeLabel="Close Evaluate candidate"
      slot={slot}
      busy={busy}
      testId="candidate-evaluation-form"
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>Cancel</button>
          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1.5"
            disabled={!canStart}
            onClick={() => { void start() }}
          >
            {busy && <LoadingSpinner size="sm" />}
            Start
          </button>
        </>
      }
    >
      {body}
    </InlineFormSection>
  )
}
