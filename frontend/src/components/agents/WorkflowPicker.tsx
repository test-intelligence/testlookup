/**
 * W14 AGT-08: choose the workflow a pipeline runs — opened in the page (an
 * `InlineFormSection`, never a dialog) by `/agents`' **Choose workflow**
 * chevron and by the Run page's ⋯ **Trigger pipeline**.
 *
 * Its reads (the project's workflows and defaults) and the deep gate are asked
 * only while it is open. **Queue pipeline** calls `triggerPipeline(runId)` for
 * the project default — the same body as the one-click **Trigger pipeline** —
 * or `(runId, workflowId[, version])` for a named one; the server's refusal
 * (404 / 409 / 422) is shown here, as text.
 */
import { useId, useState } from 'react'
import toast from 'react-hot-toast'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import { deepInvestigationAvailability, useAIConfig } from '@/hooks/useAIConfig'
import { useWorkflowChoices } from '@/hooks/useAgentOps'
import agentService from '@/services/agentService'
import { PIPELINE_QUEUED, pipelineActionError } from '@/services/pipelineActions'
import { PICKER_TITLE, WORKFLOWS_LOAD_ERROR, pickerOptions } from './pipelineViewsModel'

export default function WorkflowPicker({
  projectId,
  runId,
  onClose,
  onQueued,
  queuedMessage = PIPELINE_QUEUED,
}: {
  /** The run's project: the workflows and defaults are that project's. */
  projectId: string | null
  runId: string
  onClose: () => void
  /** After a successful queue (the page revalidates its list). */
  onQueued?: () => void
  queuedMessage?: string
}) {
  const selectId = useId()
  const { data: aiConfig } = useAIConfig()
  const { data: choices, error, mutate } = useWorkflowChoices(projectId, true)
  const options = pickerOptions(choices, { deepAvailable: deepInvestigationAvailability(aiConfig).available })
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const chosen = options.find(o => o.value === value) ?? options[0]

  async function queue() {
    setBusy(true)
    setFailure(null)
    try {
      if (chosen.workflowId == null) await agentService.triggerPipeline(runId)
      else if (chosen.workflowVersion == null) await agentService.triggerPipeline(runId, chosen.workflowId)
      else await agentService.triggerPipeline(runId, chosen.workflowId, chosen.workflowVersion)
      toast.success(queuedMessage)
      onQueued?.()
      onClose()
    } catch (err: unknown) {
      setFailure(pipelineActionError(err, 'The pipeline could not be queued.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <InlineFormSection
      title={PICKER_TITLE}
      headingLevel={2}
      onClose={onClose}
      canClose={!busy}
      closeLabel="Close the workflow picker"
      busy={busy}
      dataAttributes={{ 'data-workflow-picker': '' }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={selectId} className="text-xs text-[var(--color-text-muted)]">Workflow</label>
        <select
          id={selectId}
          aria-label="Workflow"
          value={chosen.value}
          onChange={e => { setValue(e.target.value); setFailure(null) }}
          disabled={busy}
          className="min-w-[16rem] max-w-[24rem] truncate rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-2 py-1 text-xs"
        >
          {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        {!choices && !error && <LoadingSpinner size="sm" />}
        <button type="button" className="btn-primary btn-sm" onClick={() => void queue()} disabled={busy}>
          {busy ? 'Queuing…' : 'Queue pipeline'}
        </button>
        <button type="button" className="btn-secondary btn-sm" onClick={onClose} disabled={busy}>
          Cancel
        </button>
      </div>
      {error && !choices && (
        <p data-picker-load-error="" className="m-0 mt-2 text-xs text-[var(--color-text-muted)]">
          {WORKFLOWS_LOAD_ERROR}{' '}
          <button type="button" className="underline" onClick={() => void mutate()}>Retry</button>
        </p>
      )}
      {failure && (
        <p data-picker-error="" className="m-0 mt-2 text-xs text-[var(--ui-danger-ink)]">{failure}</p>
      )}
    </InlineFormSection>
  )
}
