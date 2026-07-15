/**
 * Settings → AI Agents (Agentic plan AI-3 — governance).
 *
 * Per-agent policy cards (only "Investigator" ships in Wave B): enabled
 * toggle, trust-ladder mode selector (shadow → suggest → act; act is disabled
 * with a "coming in a later wave" tooltip), the four per-run/per-day budget
 * inputs, and the promotion status line ("N shadow runs completed"). Saves
 * via PUT /projects/{id}/agent-policies/{agent_id} with success/error toasts.
 *
 * Route-level access is QA_LEAD+ (managementRoutes in App.tsx), same as every
 * other settings page.
 */
import { useState } from 'react'
import { Bot, ShieldAlert } from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import { useAgentPolicies } from '@/hooks/useAgentGovernance'
import { useActiveProjectId } from '@/hooks/useProjectScopedSWR'
import { agentGovernanceService } from '@/services/agentGovernanceService'
import { ALL_PROJECTS_ID } from '@/store/projectStore'
import type { AgentMode, AgentPolicy, AgentPolicyBudgets } from '@/types/investigator'
import LoadingSpinner from '@/components/ui/LoadingSpinner'

const AGENT_LABEL: Record<string, { title: string; desc: string }> = {
  investigator: {
    title: 'Investigator',
    desc: 'Hypothesis-loop root-cause agent — tests infrastructure / commit / environment / known-flaky / regression hypotheses against queryable evidence for a run.',
  },
}

/** Trust-ladder copy per mode — mirrors the plan's shadow→suggest→act doctrine. */
const MODE_COPY: Record<AgentMode, { label: string; copy: string; disabled?: boolean; tooltip?: string }> = {
  shadow: {
    label: 'Shadow',
    copy: 'Logs what it would do. Takes no action, proposes nothing — pure observation while trust is established.',
  },
  suggest: {
    label: 'Suggest',
    copy: 'Proposes actions for explicit human approval. Nothing executes without a person clicking approve.',
  },
  act: {
    label: 'Act',
    copy: 'Executes pre-approved action types autonomously within budgets, with a full audit trail.',
    disabled: true,
    tooltip: 'Gated-act mode is coming in a later wave',
  },
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
  const [saving, setSaving] = useState(false)

  const meta = AGENT_LABEL[policy.agent_id] ?? { title: policy.agent_id, desc: '' }
  const budgetErrors = BUDGET_FIELDS.filter(
    (f) => !Number.isFinite(budgets[f.key]) || budgets[f.key] < 1 || !Number.isInteger(budgets[f.key]),
  )
  const dirty =
    enabled !== policy.enabled ||
    mode !== policy.mode ||
    BUDGET_FIELDS.some((f) => budgets[f.key] !== policy.budgets[f.key])

  const onSave = async () => {
    if (budgetErrors.length > 0 || saving) return
    setSaving(true)
    try {
      await agentGovernanceService.updatePolicy(projectId, policy.agent_id, { enabled, mode, budgets })
      toast.success(`${meta.title} policy saved`)
      onSaved()
    } catch {
      // shared axios interceptor surfaces the server's error message
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
                disabled={info.disabled}
                title={info.tooltip}
                onClick={() => !info.disabled && setMode(m)}
                className={clsx(
                  'rounded-lg border p-3 text-left transition-colors',
                  info.disabled ? 'opacity-50 cursor-not-allowed' : 'hover:bg-[var(--color-bg-hover)]',
                )}
                style={{
                  background: selected ? 'var(--color-bg-secondary)' : 'var(--color-bg)',
                  borderColor: selected ? 'var(--color-accent)' : 'var(--color-border)',
                }}
              >
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-[var(--color-text)]">{info.label}</span>
                  {info.disabled && (
                    <span className="text-[10px] px-1.5 py-px rounded-full bg-[var(--color-bg-hover)] text-[var(--color-text-muted)]">
                      later wave
                    </span>
                  )}
                </div>
                <p className="text-xs text-[var(--color-text-muted)] m-0 mt-1 leading-relaxed">{info.copy}</p>
              </button>
            )
          })}
        </div>
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
          className="mt-3 px-4 py-1.5 text-sm font-medium rounded-md transition-colors disabled:opacity-50"
          style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
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
      <div>
        <h1 className="text-2xl font-bold text-[var(--color-text)]">AI Agents</h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">
          Per-agent governance: trust-ladder autonomy mode, budgets, and promotion status. Every agent launches in
          shadow mode and earns promotion through clean shadow runs.
        </p>
      </div>

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
      ) : isLoading ? (
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
            key={`${p.agent_id}:${p.enabled}:${p.mode}:${p.budgets.max_runs_per_day}:${p.budgets.max_llm_calls_per_run}:${p.budgets.max_tokens_per_run}:${p.budgets.max_seconds_per_run}`}
            policy={p}
            projectId={scopedProjectId}
            onSaved={() => void mutate()}
          />
        ))
      )}
    </div>
  )
}
