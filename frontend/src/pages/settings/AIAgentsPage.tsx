/**
 * Settings → AI Agents (agent governance).
 *
 * Per-agent policy cards (only "Investigator" ships in Wave B): enabled
 * toggle, trust-ladder mode selector (shadow → suggest → act), the four
 * per-run/per-day budget inputs, and the promotion status line ("N shadow runs
 * completed"). Saves via the canonical /agent-configs/{agent_id} resource with
 * success/error toasts.
 *
 * Act (W15 AGT-03, owner decision D16): while the card's mode is Act, a
 * fieldset ticks the action types Act may run — quarantine, reassign, an
 * internal defect — each opt-in; a person still approves every action in
 * Inbox › Approvals, every run is audited and can be undone. Save writes the
 * policy (`updatePolicy`) when it changed and the ticks
 * (`updateInvestigatorAct`, a fresh GET-then-PUT) when they changed.
 *
 * Route-level access is QA_LEAD+ (managementRoutes in App.tsx), same as every
 * other settings page.
 */
import { useId, useState } from 'react'
import { Bot, ShieldAlert } from 'lucide-react'
import toast from 'react-hot-toast'
import { useAgentPolicies, refreshAgentGovernance } from '@/hooks/useAgentGovernance'
import { useActiveProjectId } from '@/hooks/useProjectScopedSWR'
import { agentGovernanceService } from '@/services/agentGovernanceService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import type { AgentMode, AgentPolicy, AgentPolicyBudgets } from '@/types/investigator'
import { ACT_ACTION_LABELS, ACT_ACTION_TYPES } from '@/types/agentActions'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import FixerConfigCard from '@/components/fixer/FixerConfigCard'
import AgentConfigPanel from '@/components/agents/AgentConfigPanel'
import PageHeader from '@/components/ui/PageHeader'
import { helpTopicParam } from '@/components/help/helpTopics'

const HELP_TOPIC = helpTopicParam('/settings/ai-agents')

const AGENT_LABEL: Record<string, { title: string; desc: string }> = {
  investigator: {
    title: 'Investigator',
    desc: 'Hypothesis-loop root-cause agent — tests infrastructure / commit / environment / known-flaky / regression hypotheses against queryable evidence for a run.',
  },
}

/**
 * Trust-ladder copy per mode — mirrors the plan's shadow→suggest→act doctrine.
 * Act (W15 AGT-03, owner decision D16) runs only the action types ticked
 * below, each one approved by a person, audited and undoable: no autonomy.
 */
export const MODE_COPY: Record<AgentMode, { label: string; copy: string }> = {
  shadow: {
    label: 'Shadow',
    copy: 'Logs what it would do. Takes no action, proposes nothing — pure observation while trust is established.',
  },
  suggest: {
    label: 'Suggest',
    copy: 'Proposes typed actions to Inbox › Approvals. Nothing executes: a person approves or does it themselves.',
  },
  act: {
    label: 'Act',
    copy: 'Approved proposals run: a person approves each one, every run is audited and can be undone. Only the action types ticked below.',
  },
}

/** What each action type Act may run does, and how its Undo restores it (one muted line each). */
export const ACT_TYPE_HINTS: Record<string, string> = {
  'quarantine.apply':
    'Applies the quarantine an approved proposal names; Undo releases it. Needs quarantine to be on for this project.',
  'failure.reassign': 'Assigns the failure to its suite owner; Undo restores the previous assignee.',
  'defect.promote': 'Files an internal defect for the cluster (never Jira); Undo closes it as Invalid.',
}
export const ACT_FIELDSET_LABEL = 'Action types Act may run'
export const ACT_ACK_NOTE = 'Acknowledging a release item needs no Act: it only records the decision.'
export const ACT_NO_TICK_NOTE = 'Act runs nothing until at least one action type is ticked.'

const sortedTicks = (ticks: readonly string[]) => [...new Set(ticks)].sort()
const sameTicks = (a: readonly string[], b: readonly string[]) =>
  sortedTicks(a).join('\n') === sortedTicks(b).join('\n')

/** Act's per-type opt-in (D16): three checkboxes, each with what it does and how Undo restores it. */
function ActTypesFieldset({ ticks, onChange }: { ticks: string[]; onChange: (ticks: string[]) => void }) {
  const hintId = useId()
  const toggle = (type: string, on: boolean) =>
    onChange(sortedTicks(on ? [...ticks, type] : ticks.filter((t) => t !== type)))
  return (
    <fieldset
      aria-label={ACT_FIELDSET_LABEL}
      data-testid="agent-act-types"
      className="m-0 mt-3 min-w-0 space-y-2.5 rounded-lg border border-[var(--color-border)] p-3"
    >
      <div className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">Act — action types</div>
      {ACT_ACTION_TYPES.map((type) => (
        <div key={type}>
          <label className="flex cursor-pointer items-center gap-2 text-sm text-[var(--color-text)]">
            <input
              type="checkbox"
              checked={ticks.includes(type)}
              aria-describedby={`${hintId}-${type}`}
              onChange={(e) => toggle(type, e.target.checked)}
            />
            {ACT_ACTION_LABELS[type]}
          </label>
          <p id={`${hintId}-${type}`} className="m-0 ml-6 max-w-[60ch] text-xs text-[var(--color-text-muted)]">
            {ACT_TYPE_HINTS[type]}
          </p>
        </div>
      ))}
      <p className="m-0 text-xs text-[var(--color-text-muted)]">{ACT_ACK_NOTE}</p>
      {ticks.length === 0 && (
        <p className="m-0 text-xs text-[var(--ui-warning-ink)]" data-testid="agent-act-no-tick">{ACT_NO_TICK_NOTE}</p>
      )}
    </fieldset>
  )
}

const BUDGET_FIELDS: { key: keyof AgentPolicyBudgets; label: string; hint: string }[] = [
  { key: 'max_runs_per_day',      label: 'Max runs / day',        hint: 'agent launches per project per day' },
  { key: 'max_llm_calls_per_run', label: 'Max LLM calls / run',   hint: 'hard stop per investigation' },
  { key: 'max_tokens_per_run',    label: 'Max tokens / run',      hint: 'input + output tokens' },
  { key: 'max_seconds_per_run',   label: 'Max seconds / run',     hint: 'wall-clock timeout' },
]

function PolicyCard({ policy, projectId, onSaved }: { policy: AgentPolicy; projectId: string; onSaved: () => void }) {
  const [enabled, setEnabled] = useState(policy.enabled)
  const [mode, setMode] = useState<AgentMode>(policy.mode)
  const [budgets, setBudgets] = useState<AgentPolicyBudgets>({ ...policy.budgets })
  // The Act allowlist survives a mode change (re-entering Act restores the ticks); it is inert outside Act.
  const savedTicks = policy.act?.allowed_action_types ?? []
  const [ticks, setTicks] = useState<string[]>(() => sortedTicks(savedTicks))
  const [saving, setSaving] = useState(false)

  const meta = AGENT_LABEL[policy.agent_id] ?? { title: policy.agent_id, desc: '' }
  const budgetErrors = BUDGET_FIELDS.filter(
    (f) => !Number.isFinite(budgets[f.key]) || budgets[f.key] < 1 || !Number.isInteger(budgets[f.key]),
  )
  const policyDirty =
    enabled !== policy.enabled ||
    mode !== policy.mode ||
    BUDGET_FIELDS.some((f) => budgets[f.key] !== policy.budgets[f.key])
  const ticksDirty = !sameTicks(ticks, savedTicks)
  const dirty = policyDirty || ticksDirty

  const onSave = async () => {
    if (budgetErrors.length > 0 || saving) return
    setSaving(true)
    let wrote = false
    let reseeded = false
    try {
      if (policyDirty) {
        await agentGovernanceService.updatePolicy(projectId, policy.agent_id, { enabled, mode, budgets })
        wrote = true
      }
      // The second write re-reads the document, so it carries the config_version the first one produced.
      if (ticksDirty) {
        await agentGovernanceService.updateInvestigatorAct(projectId, sortedTicks(ticks))
        wrote = true
      }
      toast.success(`${meta.title} policy saved`)
      reseeded = true
      onSaved()
    } catch {
      // shared axios interceptor surfaces the server's error message; a failed second write leaves the first one
      // saved, so the card re-seeds from the server (once).
      if (wrote && !reseeded) onSaved()
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="card space-y-4" data-testid={`agent-policy-${policy.agent_id}`}>
      <div className="flex items-start gap-3">
        <div className="p-2 bg-[var(--color-bg-secondary)] rounded-lg">
          <Bot className="h-4 w-4 text-[var(--color-text-muted)]" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-[var(--color-text)] m-0">{meta.title}</h3>
          <p className="text-sm text-[var(--color-text-muted)] m-0 mt-0.5">{meta.desc}</p>
        </div>
        {/* Enabled toggle */}
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <span className="text-xs text-[var(--color-text-muted)]">{enabled ? 'Enabled' : 'Disabled'}</span>
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-label={`${meta.title} enabled`}
            onClick={() => setEnabled((v) => !v)}
            className="relative inline-flex items-center rounded-full transition-colors"
            style={{
              width: 34,
              height: 18,
              background: enabled ? 'var(--color-accent)' : 'var(--color-bg-secondary)',
              border: '1px solid var(--color-border)',
            }}
          >
            <span
              aria-hidden
              // eslint-disable-next-line no-restricted-syntax -- a toggle knob: white on both track colours in every theme (the track carries the state)
              className="absolute rounded-full bg-white"
              style={{ width: 12, height: 12, top: 2, left: enabled ? 18 : 2, transition: 'left 150ms ease-out' }}
            />
          </button>
        </label>
      </div>

      {/* Trust-ladder mode selector */}
      <div>
        <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
          Autonomy mode — trust ladder
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2" role="radiogroup" aria-label="Agent autonomy mode">
          {(Object.keys(MODE_COPY) as AgentMode[]).map((m) => {
            const info = MODE_COPY[m]
            const selected = mode === m
            return (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={selected}
                aria-label={info.label}
                onClick={() => setMode(m)}
                className="rounded-lg border p-3 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
                style={{
                  background: selected ? 'var(--color-bg-secondary)' : 'var(--color-bg)',
                  borderColor: selected ? 'var(--color-accent)' : 'var(--color-border)',
                }}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-[var(--color-text)]">{info.label}</span>
                </div>
                <p className="text-xs text-[var(--color-text-muted)] m-0 mt-1 leading-relaxed">{info.copy}</p>
              </button>
            )
          })}
        </div>
        {/* W15 AGT-03: the action types Act may run — only while the card's mode is Act (nothing renders otherwise). */}
        {mode === 'act' && <ActTypesFieldset ticks={ticks} onChange={setTicks} />}
      </div>

      {/* Budgets */}
      <div>
        <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Budgets</div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {BUDGET_FIELDS.map((f) => {
            const invalid = budgetErrors.some((e) => e.key === f.key)
            return (
              <label key={f.key} className="block">
                <span className="text-xs text-[var(--color-text-secondary)]">{f.label}</span>
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={Number.isFinite(budgets[f.key]) ? budgets[f.key] : ''}
                  aria-label={f.label}
                  aria-invalid={invalid}
                  onChange={(e) =>
                    setBudgets((b) => ({ ...b, [f.key]: e.target.value === '' ? Number.NaN : Number(e.target.value) }))
                  }
                  className="mt-1 w-full rounded-md px-2 py-1.5 text-sm tabular-nums bg-[var(--color-bg)] text-[var(--color-text)]"
                  style={{ border: `1px solid ${invalid ? 'var(--gate-no-go)' : 'var(--color-border)'}` }}
                />
                <span className="text-[10px] text-[var(--color-text-faint)]">{f.hint}</span>
              </label>
            )
          })}
        </div>
        {budgetErrors.length > 0 && (
          <p className="text-xs m-0 mt-1.5" style={{ color: 'var(--gate-no-go)' }}>
            Budgets must be positive whole numbers.
          </p>
        )}
      </div>

      {/* Promotion status + save */}
      <div className="flex items-center justify-between gap-3 flex-wrap pt-1" style={{ borderTop: '1px solid var(--color-border)' }}>
        <p className="text-xs text-[var(--color-text-muted)] m-0 mt-3">
          <span className="font-semibold text-[var(--color-text-secondary)] tabular-nums">
            {policy.promotion.shadow_runs_completed}
          </span>{' '}
          shadow run{policy.promotion.shadow_runs_completed === 1 ? '' : 's'} completed
          {policy.promotion.note && <> — {policy.promotion.note}</>}
        </p>
        <button
          type="button"
          onClick={onSave}
          disabled={!dirty || budgetErrors.length > 0 || saving}
          className="btn-primary mt-3"
        >
          {saving ? 'Saving…' : 'Save policy'}
        </button>
      </div>
    </div>
  )
}

export default function AIAgentsPage() {
  const projectId = useActiveProjectId()
  const scopedProjectId = projectId === ALL_PROJECTS_ID ? null : projectId
  const { policies, isLoading, error, mutate } = useAgentPolicies(scopedProjectId)

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        title="AI Agents"
        subtitle="Per-agent governance: trust-ladder autonomy mode, budgets, and promotion status. Every agent launches in shadow mode and earns promotion through clean shadow runs."
        helpTopic={HELP_TOPIC}
      />

      {!scopedProjectId ? (
        <div
          className="card flex items-center gap-3 py-3 px-4"
          style={{ borderColor: 'var(--gate-conditional-border)', background: 'var(--gate-conditional-bg)' }}
        >
          <ShieldAlert className="h-4 w-4 flex-shrink-0" style={{ color: 'var(--gate-conditional)' }} />
          <p className="text-sm m-0" style={{ color: 'var(--gate-conditional)' }}>
            Agent policies are per-project — select a specific project from the top bar to manage them.
          </p>
        </div>
      ) : (
        <>
          {isLoading ? (
            <div className="flex justify-center py-10"><LoadingSpinner size="lg" /></div>
          ) : error ? (
            <p className="text-sm text-[var(--color-text-muted)]">Could not load agent policies.</p>
          ) : policies.length === 0 ? (
            <p className="text-sm text-[var(--color-text-muted)]">No governable agents are registered for this project yet.</p>
          ) : (
            policies.map((p) => (
              <PolicyCard
                // Key on the server state so a successful save (or an external
                // change surfaced by revalidation) re-seeds the form — no
                // set-state-in-effect syncing.
                key={`${p.agent_id}:${p.enabled}:${p.mode}:${p.budgets.max_runs_per_day}:${p.budgets.max_llm_calls_per_run}:${p.budgets.max_tokens_per_run}:${p.budgets.max_seconds_per_run}:${(p.act?.allowed_action_types ?? []).join(',')}`}
                policy={p}
                projectId={scopedProjectId}
                // Both keys: the config panel below renders the same document
                // under a different key and holds the If-Match the next save sends.
                onSaved={() => void refreshAgentGovernance(projectId)}
              />
            ))
          )}
          {/* Fixer keeps its pinned card shape while AgentConfig owns storage. */}
          <FixerConfigCard projectId={scopedProjectId} />
          {/* E4.3: per-agent configuration (agent-configs), one tab per agent. */}
          <AgentConfigPanel projectId={scopedProjectId} />
        </>
      )}
    </div>
  )
}
