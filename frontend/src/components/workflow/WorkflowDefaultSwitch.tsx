/**
 * W14 AGT-08: "Use for new {base} runs" — a published fork as the project's
 * default workflow for its base, in the workflow editor's facts row.
 *
 * QA lead and above see a switch (ON = an UNPINNED default: each run starts
 * the fork's latest published version; OFF = the built-in again). Below that
 * role the fact is read-only. A built-in says what it is used for now. When the
 * default calls a model, the cost is said beside the switch: every new run —
 * the automatic one after each ingest included — pays for it.
 */
import { useState } from 'react'
import { useWorkflowDefaults } from '@/hooks/useAgentOps'
import { clearWorkflowDefault, setWorkflowDefault } from '@/services/workflowService'
import { pipelineActionError } from '@/services/pipelineActions'
import type { WorkflowDefault } from '@/types/agentOps'
import type { WorkflowItem } from '@/types/workflowDefinition'

/** The bases a project default exists for (mirrors WORKFLOW_DEFAULT_BASES). */
const DEFAULT_BASES = new Set(['offline', 'deep'])

export const DEFAULTS_LOAD_ERROR = 'Could not load the default workflows.'

function modelStagesWarning(stages: readonly string[]): string {
  const first = stages.slice(0, 3).join(', ')
  return `Every new run calls a model in ${stages.length} stage${stages.length === 1 ? '' : 's'}: ${first}${stages.length > 3 ? '…' : ''}`
}

export default function WorkflowDefaultSwitch({
  projectId,
  workflow,
  canManage,
}: {
  projectId: string
  /** The workflow selected in the editor. */
  workflow: WorkflowItem
  /** QA lead or above (the PUT / DELETE floor). */
  canManage: boolean
}) {
  const { data, error, mutate } = useWorkflowDefaults(projectId)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const base = workflow.base
  if (!DEFAULT_BASES.has(base)) return null
  if (error && !data) {
    return <span data-default-load-error="" className="text-[var(--color-text-muted)]">{DEFAULTS_LOAD_ERROR}</span>
  }
  const current: WorkflowDefault | undefined = data?.defaults.find((d) => d.base === base)
  if (!current) return null

  if (workflow.built_in) {
    return (
      <span data-default-fact="">
        Used for new {base} runs unless a fork is the project default (now: {current.resolved_ref ?? current.workflow_id})
      </span>
    )
  }

  const isDefault = current.source === 'project_default' && current.workflow_id === workflow.workflow_id
  if (!canManage) {
    return isDefault ? <span data-default-fact="">Used for new {base} runs</span> : null
  }
  // Only a published version executes: a draft has no switch.
  if (workflow.status !== 'published') return null

  async function toggle() {
    setBusy(true)
    setFailure(null)
    try {
      if (isDefault) {
        await clearWorkflowDefault(projectId, base)
      } else {
        await setWorkflowDefault(projectId, base, { workflow_id: workflow.workflow_id })
      }
      await mutate()
    } catch (err: unknown) {
      setFailure(pipelineActionError(err, 'The default could not be changed.'))
    } finally {
      setBusy(false)
    }
  }

  const label = `Use for new ${base} runs`
  return (
    <span data-default-switch="" className="inline-flex flex-wrap items-center gap-2">
      <span className="inline-flex items-center gap-2">
        <button
          type="button"
          role="switch"
          aria-checked={isDefault}
          aria-label={label}
          disabled={busy}
          onClick={() => void toggle()}
          className="relative inline-flex shrink-0 items-center rounded-full transition-colors disabled:opacity-50"
          style={{
            width: 34,
            height: 18,
            background: isDefault ? 'var(--color-accent)' : 'var(--color-bg-secondary)',
            border: '1px solid var(--color-border)',
          }}
        >
          <span
            aria-hidden
            className="absolute rounded-full border border-[var(--color-border)] bg-[var(--color-bg)]"
            style={{ width: 12, height: 12, top: 2, left: isDefault ? 18 : 2, transition: 'left 150ms ease-out' }}
          />
        </button>
        <span className="text-[var(--color-text-secondary)]">{label}</span>
      </span>
      {isDefault && current.model_stages.length > 0 && (
        <span data-model-stages="" className="max-w-[60ch] text-xs text-[var(--ui-warning-ink)]">
          {modelStagesWarning(current.model_stages)}
        </span>
      )}
      {failure && <span data-default-error="" className="text-xs text-[var(--ui-danger-ink)]">{failure}</span>}
    </span>
  )
}
