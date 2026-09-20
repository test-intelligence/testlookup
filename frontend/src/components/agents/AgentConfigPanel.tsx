/**
 * Settings → AI Agents → Agent configuration (architecture E4.3).
 *
 * One tab per configurable agent. Each tab edits that agent's configuration
 * document for the active project and saves it whole with
 * PUT /api/v1/projects/{id}/agent-configs/{agent_id} (QA_LEAD+). The server is
 * the validator: environment ceilings, the attempts x timeout deadline, and the
 * mode checks live there, so a refused save lists the server's reasons inline
 * instead of relying on a toast alone.
 *
 * A stored configuration that no longer validates (an environment ceiling was
 * lowered after it was saved) is shown as stored, with its errors.
 */
import { useState } from 'react'
import { AlertTriangle, SlidersHorizontal } from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import { useAgentConfigs, refreshAgentGovernance } from '@/hooks/useAgentGovernance'
import { agentGovernanceService } from '@/services/agentGovernanceService'
import { agentLabel, saveErrorLines } from '@/utils/agentConfig'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import type { AgentMode } from '@/types/investigator'
import type {
  AgentConfigDocument,
  AgentConfigView,
  AgentTier,
  ReviewPolicy,
  ToolPermission,
} from '@/types/agentConfig'

const MODES: { value: AgentMode; label: string; copy: string }[] = [
  { value: 'shadow', label: 'Shadow', copy: 'Read-only tools; records what it would do.' },
  { value: 'suggest', label: 'Suggest', copy: 'Adds proposals that a person approves.' },
  { value: 'act', label: 'Act', copy: 'Adds mutating tools and capabilities.' },
]

const TIERS: { value: AgentTier; label: string }[] = [
  { value: 'auto', label: 'Auto (start at the default, escalate by rules)' },
  { value: 'deterministic', label: 'Deterministic only' },
  { value: 'slm', label: 'Small language model' },
  { value: 'llm', label: 'Large language model' },
]

const REVIEW_POLICIES: { value: ReviewPolicy; label: string }[] = [
  { value: 'human_required', label: 'Human review required' },
  { value: 'human_required_plus_auto_reviewer', label: 'Human review plus an automatic reviewer' },
]

interface NumberField {
  id: string
  label: string
  integer: boolean
  get: (d: AgentConfigDocument) => number
  set: (d: AgentConfigDocument, v: number) => void
}

const RETRY_FIELDS: NumberField[] = [
  {
    id: 'retry.max_attempts', label: 'Max attempts', integer: true,
    get: (d) => d.retry.max_attempts, set: (d, v) => { d.retry.max_attempts = v },
  },
  {
    id: 'timeout_seconds', label: 'Timeout (seconds)', integer: true,
    get: (d) => d.timeout_seconds, set: (d, v) => { d.timeout_seconds = v },
  },
]

const BUDGET_FIELDS: NumberField[] = [
  {
    id: 'budget.max_runs_per_day', label: 'Max runs / day', integer: true,
    get: (d) => d.budget.max_runs_per_day, set: (d, v) => { d.budget.max_runs_per_day = v },
  },
  {
    id: 'budget.max_llm_calls_per_run', label: 'Max LLM calls / run', integer: true,
    get: (d) => d.budget.max_llm_calls_per_run, set: (d, v) => { d.budget.max_llm_calls_per_run = v },
  },
  {
    id: 'budget.max_tokens_per_run', label: 'Max tokens / run', integer: true,
    get: (d) => d.budget.max_tokens_per_run, set: (d, v) => { d.budget.max_tokens_per_run = v },
  },
  {
    id: 'budget.max_cost_usd_per_run', label: 'Max cost (USD) / run', integer: false,
    get: (d) => d.budget.max_cost_usd_per_run, set: (d, v) => { d.budget.max_cost_usd_per_run = v },
  },
]

function clone(doc: AgentConfigDocument): AgentConfigDocument {
  return JSON.parse(JSON.stringify(doc)) as AgentConfigDocument
}

function fieldInvalid(field: NumberField, doc: AgentConfigDocument): boolean {
  const value = field.get(doc)
  const minimum = field.id === 'retry.max_attempts' || field.id === 'timeout_seconds' ? 1 : 0
  return !Number.isFinite(value) || value < minimum || (field.integer && !Number.isInteger(value))
}

function Toggle({ checked, label, disabled, onChange }: { checked: boolean; label: string; disabled?: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className={clsx('flex items-center gap-2 text-sm', disabled ? 'opacity-60' : 'cursor-pointer')}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        aria-label={label}
      />
      <span className="text-[var(--color-text-secondary)]">{label}</span>
    </label>
  )
}

function NumberInput({ field, doc, onChange }: { field: NumberField; doc: AgentConfigDocument; onChange: (v: number) => void }) {
  const value = field.get(doc)
  const invalid = fieldInvalid(field, doc)
  return (
    <label className="block">
      <span className="text-xs text-[var(--color-text-secondary)]">{field.label}</span>
      <input
        type="number"
        min={0}
        step={field.integer ? 1 : 0.01}
        value={Number.isFinite(value) ? value : ''}
        aria-label={field.label}
        aria-invalid={invalid}
        onChange={(e) => onChange(e.target.value === '' ? Number.NaN : Number(e.target.value))}
        className="mt-1 w-full rounded-md px-2 py-1.5 text-sm tabular-nums bg-[var(--color-bg)] text-[var(--color-text)]"
        style={{ border: `1px solid ${invalid ? 'var(--gate-no-go)' : 'var(--color-border)'}` }}
      />
    </label>
  )
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">{children}</div>
}

function AgentConfigForm({
  view,
  tools,
  projectId,
  onSaved,
}: {
  view: AgentConfigView
  tools: Record<string, ToolPermission>
  projectId: string
  onSaved: () => void
}) {
  const [doc, setDoc] = useState<AgentConfigDocument>(() => clone(view.config))
  const [saving, setSaving] = useState(false)
  const [serverErrors, setServerErrors] = useState<string[]>([])

  const update = (mutate: (d: AgentConfigDocument) => void) =>
    setDoc((prev) => {
      const next = clone(prev)
      mutate(next)
      return next
    })

  const invalidFields = [...RETRY_FIELDS, ...BUDGET_FIELDS].filter((f) => fieldInvalid(f, doc))
  const dirty = JSON.stringify(doc) !== JSON.stringify(view.config)
  const worstCase = doc.retry.max_attempts * doc.timeout_seconds
  const title = agentLabel(view.agent_id)
  const toolNames = Object.keys(tools).sort()

  const onSave = async () => {
    if (!dirty || invalidFields.length > 0 || saving) return
    setSaving(true)
    try {
      await agentGovernanceService.updateAgentConfig(projectId, view.agent_id, doc, view.config_version)
      setServerErrors([])
      toast.success(`${title} configuration saved`)
      onSaved()
    } catch (err) {
      setServerErrors(saveErrorLines(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-5" role="tabpanel" aria-label={`${title} configuration`} data-testid={`agent-config-${view.agent_id}`}>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-xs text-[var(--color-text-muted)] m-0">
          {view.source === 'default' ? (
            <>Using defaults; this project has not saved a configuration for this agent.</>
          ) : (
            <>
              Version <span className="font-semibold tabular-nums">{view.config_version}</span>
              {view.updated_at && <> · saved {new Date(view.updated_at).toLocaleString()}</>}
            </>
          )}
        </p>
        <Toggle checked={doc.enabled} label="Enabled" onChange={(v) => update((d) => { d.enabled = v })} />
      </div>

      {!view.valid && (
        <div
          role="alert"
          className="rounded-lg p-3 text-sm"
          style={{ background: 'var(--gate-conditional-bg)', border: '1px solid var(--gate-conditional-border)', color: 'var(--gate-conditional)' }}
        >
          <div className="flex items-center gap-2 font-medium">
            <AlertTriangle className="h-4 w-4" /> The stored configuration no longer validates. Invocations are refused until it is fixed.
          </div>
          <ul className="m-0 mt-1 pl-5 list-disc">
            {view.errors.map((e) => <li key={e}>{e}</li>)}
          </ul>
        </div>
      )}

      <div>
        <SectionTitle>Mode</SectionTitle>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2" role="radiogroup" aria-label="Agent mode">
          {MODES.map((m) => {
            const selected = doc.mode === m.value
            return (
              <button
                key={m.value}
                type="button"
                role="radio"
                aria-checked={selected}
                aria-label={m.label}
                onClick={() => update((d) => { d.mode = m.value })}
                className="rounded-lg border p-2.5 text-left transition-colors hover:bg-[var(--color-bg-hover)]"
                style={{
                  background: selected ? 'var(--color-bg-secondary)' : 'var(--color-bg)',
                  borderColor: selected ? 'var(--color-accent)' : 'var(--color-border)',
                }}
              >
                <div className="text-sm font-semibold text-[var(--color-text)]">{m.label}</div>
                <p className="text-xs text-[var(--color-text-muted)] m-0 mt-0.5">{m.copy}</p>
              </button>
            )
          })}
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label className="block">
          <SectionTitle>Model tier</SectionTitle>
          <select
            aria-label="Model tier"
            value={doc.model.tier}
            onChange={(e) => update((d) => { d.model.tier = e.target.value as AgentTier })}
            className="w-full rounded-md px-2 py-1.5 text-sm bg-[var(--color-bg)] text-[var(--color-text)] border border-[var(--color-border)]"
          >
            {TIERS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </label>
        <div>
          <SectionTitle>Retries</SectionTitle>
          <div className="grid grid-cols-2 gap-3">
            {RETRY_FIELDS.map((f) => (
              <NumberInput key={f.id} field={f} doc={doc} onChange={(v) => update((d) => f.set(d, v))} />
            ))}
          </div>
          <p className="text-[11px] text-[var(--color-text-faint)] m-0 mt-1" data-testid="worst-case">
            Worst case {Number.isFinite(worstCase) ? `${doc.retry.max_attempts} × ${doc.timeout_seconds} s = ${worstCase} s` : '—'};
            it must fit the pipeline deadline.
          </p>
        </div>
      </div>

      <div>
        <SectionTitle>Budget</SectionTitle>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {BUDGET_FIELDS.map((f) => (
            <NumberInput key={f.id} field={f} doc={doc} onChange={(v) => update((d) => f.set(d, v))} />
          ))}
        </div>
      </div>

      {toolNames.length > 0 && (
        <fieldset className="border-0 p-0 m-0">
          <legend className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">Tool allowlist</legend>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
            {toolNames.map((name) => {
              const permission = tools[name]
              return (
                <label key={name} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    aria-label={name}
                    checked={doc.tools.allowlist.includes(name)}
                    onChange={(e) =>
                      update((d) => {
                        const set = new Set(d.tools.allowlist)
                        if (e.target.checked) set.add(name)
                        else set.delete(name)
                        d.tools.allowlist = [...set].sort()
                      })
                    }
                  />
                  <span className="font-mono text-xs text-[var(--color-text-secondary)]">{name}</span>
                  {permission !== 'read_only' && (
                    <span className="text-[10px] px-1.5 py-px rounded-full bg-[var(--color-bg-hover)] text-[var(--color-text-muted)]">
                      {permission}
                    </span>
                  )}
                </label>
              )
            })}
          </div>
        </fieldset>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <SectionTitle>Review</SectionTitle>
          <select
            aria-label="Review policy"
            value={doc.review.policy}
            onChange={(e) =>
              update((d) => {
                d.review.policy = e.target.value as ReviewPolicy
                if (d.review.policy === 'human_required_plus_auto_reviewer') d.review.auto_reviewer = true
              })
            }
            className="w-full rounded-md px-2 py-1.5 text-sm bg-[var(--color-bg)] text-[var(--color-text)] border border-[var(--color-border)]"
          >
            {REVIEW_POLICIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
          <div className="mt-2 space-y-1">
            <Toggle
              checked={doc.review.auto_reviewer}
              label="Automatic reviewer"
              disabled={doc.review.policy === 'human_required_plus_auto_reviewer'}
              onChange={(v) => update((d) => { d.review.auto_reviewer = v })}
            />
            <Toggle
              checked={doc.review.second_model_check}
              label="Second-model check"
              onChange={(v) => update((d) => { d.review.second_model_check = v })}
            />
          </div>
        </div>
        <div>
          <SectionTitle>Request overrides may</SectionTitle>
          <div className="space-y-1">
            <Toggle
              checked={doc.override_policy.allow_tier_downgrade}
              label="Lower the model tier"
              onChange={(v) => update((d) => { d.override_policy.allow_tier_downgrade = v })}
            />
            <Toggle
              checked={doc.override_policy.allow_retry_decrease}
              label="Lower max attempts"
              onChange={(v) => update((d) => { d.override_policy.allow_retry_decrease = v })}
            />
            <Toggle
              checked={doc.override_policy.allow_tool_narrowing}
              label="Narrow the tool allowlist"
              onChange={(v) => update((d) => { d.override_policy.allow_tool_narrowing = v })}
            />
          </div>
        </div>
      </div>

      {serverErrors.length > 0 && (
        <div role="alert" className="rounded-lg p-3 text-sm" style={{ border: '1px solid var(--gate-no-go)', color: 'var(--gate-no-go)' }}>
          <div className="font-medium">The server refused this configuration:</div>
          <ul className="m-0 mt-1 pl-5 list-disc">
            {serverErrors.map((e) => <li key={e}>{e}</li>)}
          </ul>
        </div>
      )}

      <div className="flex items-center justify-end gap-3 pt-3" style={{ borderTop: '1px solid var(--color-border)' }}>
        {invalidFields.length > 0 && (
          <p className="text-xs m-0 mr-auto" style={{ color: 'var(--gate-no-go)' }}>
            Attempts and timeout must be at least 1; budgets must be zero or more (whole numbers except cost).
          </p>
        )}
        <button
          type="button"
          onClick={onSave}
          disabled={!dirty || invalidFields.length > 0 || saving}
          className="px-4 py-1.5 text-sm font-medium rounded-md transition-colors disabled:opacity-50"
          style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
        >
          {saving ? 'Saving…' : 'Save configuration'}
        </button>
      </div>
    </div>
  )
}

export default function AgentConfigPanel({ projectId }: { projectId: string }) {
  const { configs, tools, isLoading, error, mutate } = useAgentConfigs(projectId)
  const [selected, setSelected] = useState<string | null>(null)
  const active = configs.find((c) => c.agent_id === selected) ?? configs[0] ?? null

  return (
    <section className="card space-y-4" data-testid="agent-config-panel">
      <div className="flex items-start gap-3">
        <div className="p-2 bg-[var(--color-bg-secondary)] rounded-lg">
          <SlidersHorizontal className="h-4 w-4 text-[var(--color-text-muted)]" />
        </div>
        <div>
          <h3 className="font-semibold text-[var(--color-text)] m-0">Agent configuration</h3>
          <p className="text-sm text-[var(--color-text-muted)] m-0 mt-0.5">
            Mode, model tier, retries, tools, budget and review for each agent in this project. A request can only tighten
            these values, and the deployment&apos;s limits always apply.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="flex justify-center py-6"><LoadingSpinner size="lg" /></div>
      ) : error ? (
        <p className="text-sm text-[var(--color-text-muted)]">Could not load agent configurations.</p>
      ) : !active ? (
        <p className="text-sm text-[var(--color-text-muted)]">No configurable agents are registered.</p>
      ) : (
        <>
          <div role="tablist" aria-label="Configurable agents" className="flex flex-wrap gap-1">
            {configs.map((c) => {
              const isActive = c.agent_id === active.agent_id
              return (
                <button
                  key={c.agent_id}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => setSelected(c.agent_id)}
                  className={clsx(
                    'px-2.5 py-1 rounded-md text-xs border transition-colors',
                    isActive ? 'font-semibold' : 'hover:bg-[var(--color-bg-hover)]',
                  )}
                  style={{
                    background: isActive ? 'var(--color-bg-secondary)' : 'var(--color-bg)',
                    borderColor: isActive ? 'var(--color-accent)' : 'var(--color-border)',
                    color: c.valid ? 'var(--color-text)' : 'var(--gate-no-go)',
                  }}
                >
                  {agentLabel(c.agent_id)}
                  {c.source === 'project' && <span className="ml-1 text-[var(--color-text-faint)]">v{c.config_version}</span>}
                </button>
              )
            })}
          </div>
          <AgentConfigForm
            // Re-seed the form when the agent or the saved version changes.
            key={`${active.agent_id}:${active.config_version}`}
            view={active}
            tools={tools}
            projectId={projectId}
            // Both keys: the policy cards above render the same document under
            // a different key, and a stale card poisons this panel's If-Match.
            onSaved={() => void refreshAgentGovernance(projectId)}
          />
        </>
      )}
    </section>
  )
}
