/**
 * Fixer settings card (Agentic plan AI-2 — Wave D), rendered on
 * Settings → AI Agents next to the Investigator governance card.
 *
 * Enabled toggle, trust-ladder mode limited to shadow/suggest ("suggest opens
 * DRAFT pull requests — merging is always human"; act is rendered disabled —
 * it does not exist for this agent), schedule, validation-runner section with
 * per-type conditional fields, test-glob allowlist editor, the four Fixer
 * budgets, Save (PUT — QA_LEAD+), and a Run-now button (POST) that toasts the
 * server detail per 202 / 403 disabled / 409 running / 422 runner-required.
 */
import { useState } from 'react'
import { isAxiosError } from 'axios'
import { Plus, Wrench, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import { useFixerConfig } from '@/hooks/useFixer'
import { fixerService } from '@/services/fixerService'
import { extractErrorMessage } from '@/services/apiErrors'
import type {
  FixerBudgets,
  FixerConfig,
  FixerMode,
  FixerRunnerType,
  FixerSchedule,
} from '@/types/fixer'
import LoadingSpinner from '@/components/ui/LoadingSpinner'

export const RUNNER_REQUIRED_COPY =
  'a validation runner is required before the Fixer may open PRs'

/** Trust-ladder copy. The Fixer tops out at suggest — merging is always human. */
const MODE_COPY: Record<FixerMode | 'act', { label: string; copy: string; disabled?: boolean; tooltip?: string }> = {
  shadow: {
    label: 'Shadow',
    copy: 'Selects failing tests, generates and validates candidate patches, and records every attempt in the ledger — opens nothing.',
  },
  suggest: {
    label: 'Suggest',
    copy: 'Suggest opens DRAFT pull requests — merging is always human. Requires a validation runner.',
  },
  act: {
    label: 'Act',
    copy: 'Does not exist for the Fixer. Merging is always human.',
    disabled: true,
    tooltip: 'The Fixer never acts autonomously — merging is always human',
  },
}

const RUNNER_TYPE_LABEL: Record<FixerRunnerType, string> = {
  none: 'None (shadow-only)',
  docker: 'Docker',
  workflow_dispatch: 'Workflow dispatch',
}

const BUDGET_FIELDS: { key: keyof FixerBudgets; label: string; hint: string }[] = [
  { key: 'max_tests_per_run',       label: 'Max tests / run',     hint: 'failing tests targeted per Fixer run' },
  { key: 'max_attempts_per_test',   label: 'Max attempts / test', hint: 'patch candidates per test' },
  { key: 'validation_reruns',       label: 'Validation reruns',   hint: 'green reruns required to validate' },
  { key: 'max_concurrent_open_prs', label: 'Max open PRs',        hint: 'concurrent draft PRs (suggest mode)' },
]

const inputClass =
  'mt-1 w-full rounded-md px-2 py-1.5 text-sm bg-[var(--color-bg)] text-[var(--color-text)]'
const inputBorder = { border: '1px solid var(--color-border)' }

function FixerForm({ config, projectId, onSaved }: { config: FixerConfig; projectId: string; onSaved: () => void }) {
  const [enabled, setEnabled] = useState(config.enabled)
  const [mode, setMode] = useState<FixerMode>(config.mode)
  const [schedule, setSchedule] = useState<FixerSchedule>(config.schedule)
  const [runnerType, setRunnerType] = useState<FixerRunnerType>(config.runner.type)
  const [runnerImage, setRunnerImage] = useState(config.runner.runner_image ?? '')
  const [commandTemplate, setCommandTemplate] = useState(config.runner.command_template ?? '')
  const [workflowRef, setWorkflowRef] = useState(config.runner.workflow_ref ?? '')
  const [globs, setGlobs] = useState<string[]>(config.test_globs)
  const [newGlob, setNewGlob] = useState('')
  const [budgets, setBudgets] = useState<FixerBudgets>({ ...config.budgets })
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)

  const budgetErrors = BUDGET_FIELDS.filter(
    (f) => !Number.isFinite(budgets[f.key]) || budgets[f.key] < 1 || !Number.isInteger(budgets[f.key]),
  )
  const runnerMissingForSuggest = mode === 'suggest' && runnerType === 'none'

  /** Contract payload — irrelevant runner fields are sent as null, empty inputs too. */
  const buildPayload = (): FixerConfig => ({
    enabled,
    mode,
    runner: {
      type: runnerType,
      runner_image: runnerType === 'docker' && runnerImage.trim() ? runnerImage.trim() : null,
      command_template: runnerType === 'docker' && commandTemplate.trim() ? commandTemplate.trim() : null,
      workflow_ref: runnerType === 'workflow_dispatch' && workflowRef.trim() ? workflowRef.trim() : null,
    },
    test_globs: globs,
    budgets: { ...budgets },
    schedule,
  })

  const addGlob = () => {
    const g = newGlob.trim()
    if (!g || globs.includes(g)) return
    setGlobs((gs) => [...gs, g])
    setNewGlob('')
  }

  const onSave = async () => {
    if (budgetErrors.length > 0 || saving) return
    setSaving(true)
    try {
      await fixerService.updateConfig(projectId, buildPayload())
      toast.success('Fixer configuration saved')
      onSaved()
    } catch {
      // shared axios interceptor surfaces the server's error message
    } finally {
      setSaving(false)
    }
  }

  const onRunNow = async () => {
    if (running) return
    setRunning(true)
    try {
      const { fixer_run_id } = await fixerService.startRun(projectId)
      toast.success(`Fixer run queued (${fixer_run_id.slice(0, 8)})`)
    } catch (err) {
      if (isAxiosError(err)) {
        const status = err.response?.status
        const detail = extractErrorMessage(
          (err.response?.data as { detail?: unknown } | undefined)?.detail,
          '',
        )
        if (status === 403) {
          toast.error(detail || 'The Fixer is disabled for this project — enable it above and save first.')
        } else if (status === 409) {
          toast(detail || 'A Fixer run is already in progress for this project.', { icon: 'ℹ️' })
        } else if (status === 422) {
          toast.error(detail || `Cannot run: ${RUNNER_REQUIRED_COPY}.`)
        }
        // Other statuses: the shared axios interceptor surfaced the message.
      }
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="card space-y-4" data-testid="fixer-config-card">
      <div className="flex items-start gap-3">
        <div className="p-2 bg-[var(--color-bg-secondary)] rounded-lg">
          <Wrench className="h-4 w-4 text-[var(--color-text-muted)]" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-[var(--color-text)] m-0">Fixer</h3>
          <p className="text-sm text-[var(--color-text-muted)] m-0 mt-0.5">
            Test-fix agent — picks stubborn failing tests, generates candidate patches inside the allowed test globs,
            and validates them with reruns before anything is proposed.
          </p>
        </div>
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <span className="text-xs text-[var(--color-text-muted)]">{enabled ? 'Enabled' : 'Disabled'}</span>
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            aria-label="Fixer enabled"
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

      {/* Trust-ladder mode selector — shadow / suggest only */}
      <div>
        <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
          Autonomy mode — trust ladder
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2" role="radiogroup" aria-label="Fixer autonomy mode">
          {(Object.keys(MODE_COPY) as (FixerMode | 'act')[]).map((m) => {
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
                onClick={() => !info.disabled && setMode(m as FixerMode)}
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
                      never
                    </span>
                  )}
                </div>
                <p className="text-xs text-[var(--color-text-muted)] m-0 mt-1 leading-relaxed">{info.copy}</p>
              </button>
            )
          })}
        </div>
      </div>

      {/* Schedule */}
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          Schedule
          <select
            value={schedule}
            onChange={(e) => setSchedule(e.target.value as FixerSchedule)}
            aria-label="Fixer schedule"
            className="rounded-md px-2 py-1.5 text-sm bg-[var(--color-bg)] text-[var(--color-text)]"
            style={inputBorder}
          >
            <option value="off">Off (manual only)</option>
            <option value="daily">Daily</option>
            <option value="weekly">Weekly</option>
          </select>
        </label>
      </div>

      {/* Validation runner */}
      <div>
        <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
          Validation runner
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <label className="block">
            <span className="text-xs text-[var(--color-text-secondary)]">Runner type</span>
            <select
              value={runnerType}
              onChange={(e) => setRunnerType(e.target.value as FixerRunnerType)}
              aria-label="Runner type"
              className={inputClass}
              style={inputBorder}
            >
              {(Object.keys(RUNNER_TYPE_LABEL) as FixerRunnerType[]).map((t) => (
                <option key={t} value={t}>{RUNNER_TYPE_LABEL[t]}</option>
              ))}
            </select>
          </label>
          {runnerType === 'docker' && (
            <>
              <label className="block">
                <span className="text-xs text-[var(--color-text-secondary)]">Runner image</span>
                <input
                  type="text"
                  value={runnerImage}
                  aria-label="Runner image"
                  placeholder="e.g. python:3.11-slim"
                  onChange={(e) => setRunnerImage(e.target.value)}
                  className={clsx(inputClass, 'font-mono')}
                  style={inputBorder}
                />
              </label>
              <label className="block">
                <span className="text-xs text-[var(--color-text-secondary)]">Command template</span>
                <input
                  type="text"
                  value={commandTemplate}
                  aria-label="Command template"
                  placeholder="e.g. pytest {test} -x"
                  onChange={(e) => setCommandTemplate(e.target.value)}
                  className={clsx(inputClass, 'font-mono')}
                  style={inputBorder}
                />
              </label>
            </>
          )}
          {runnerType === 'workflow_dispatch' && (
            <label className="block md:col-span-2">
              <span className="text-xs text-[var(--color-text-secondary)]">Workflow ref</span>
              <input
                type="text"
                value={workflowRef}
                aria-label="Workflow ref"
                placeholder="e.g. .github/workflows/fixer-validate.yml@main"
                onChange={(e) => setWorkflowRef(e.target.value)}
                className={clsx(inputClass, 'font-mono')}
                style={inputBorder}
              />
            </label>
          )}
        </div>
        {runnerMissingForSuggest && (
          <div
            className="flex items-start gap-2 rounded-md px-3 py-2 mt-2"
            role="alert"
            style={{ background: 'var(--gate-conditional-bg)', border: '1px solid var(--gate-conditional-border)' }}
          >
            <p className="text-xs m-0" style={{ color: 'var(--gate-conditional)' }}>
              Suggest mode needs somewhere to prove a patch: {RUNNER_REQUIRED_COPY}. Saving is allowed, but Run now
              will be rejected (422) until a runner is configured.
            </p>
          </div>
        )}
      </div>

      {/* Test globs */}
      <div>
        <div className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
          Allowed test globs
        </div>
        <p className="text-xs text-[var(--color-text-muted)] m-0 mb-2">
          The Fixer may only touch files matching these globs — patches reaching outside are rejected.
        </p>
        <ul className="m-0 p-0 space-y-1.5" aria-label="Test globs">
          {globs.map((g) => (
            <li key={g} className="flex items-center gap-2">
              <code
                className="font-mono text-xs px-2 py-1 rounded flex-1 min-w-0 truncate"
                style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}
              >
                {g}
              </code>
              <button
                type="button"
                aria-label={`Remove glob ${g}`}
                onClick={() => setGlobs((gs) => gs.filter((x) => x !== g))}
                className="p-1 rounded hover:bg-[var(--color-bg-hover)] text-[var(--color-text-muted)]"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          ))}
          {globs.length === 0 && (
            <li className="text-xs" style={{ color: 'var(--gate-no-go)' }}>
              No globs — the Fixer cannot touch any file. Add at least one.
            </li>
          )}
        </ul>
        <div className="flex items-center gap-2 mt-2">
          <input
            type="text"
            value={newGlob}
            aria-label="New test glob"
            placeholder="e.g. tests/**"
            onChange={(e) => setNewGlob(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addGlob() } }}
            className="rounded-md px-2 py-1.5 text-sm font-mono flex-1 bg-[var(--color-bg)] text-[var(--color-text)]"
            style={inputBorder}
          />
          <button
            type="button"
            onClick={addGlob}
            disabled={!newGlob.trim()}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 text-sm rounded-md border hover:bg-[var(--color-bg-hover)] disabled:opacity-50 text-[var(--color-text-secondary)]"
            style={{ borderColor: 'var(--color-border)' }}
          >
            <Plus className="h-3.5 w-3.5" /> Add
          </button>
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
                  className={clsx(inputClass, 'tabular-nums')}
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

      {/* Run now + save */}
      <div className="flex items-center justify-end gap-2 pt-1 mt-3" style={{ borderTop: '1px solid var(--color-border)' }}>
        <button
          type="button"
          onClick={onRunNow}
          disabled={running}
          className="mt-3 px-4 py-1.5 text-sm font-medium rounded-md border transition-colors hover:bg-[var(--color-bg-hover)] disabled:opacity-50 text-[var(--color-text-secondary)]"
          style={{ borderColor: 'var(--color-border)' }}
        >
          {running ? 'Queuing…' : 'Run now'}
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={budgetErrors.length > 0 || saving}
          className="mt-3 px-4 py-1.5 text-sm font-medium rounded-md transition-colors disabled:opacity-50"
          style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
        >
          {saving ? 'Saving…' : 'Save configuration'}
        </button>
      </div>
    </div>
  )
}

export default function FixerConfigCard({ projectId }: { projectId: string }) {
  const { data, isLoading, error, mutate } = useFixerConfig(projectId)

  if (isLoading && !data) {
    return (
      <div className="card flex justify-center py-8" data-testid="fixer-config-loading">
        <LoadingSpinner size="md" />
      </div>
    )
  }
  if (error || !data) {
    return (
      <div className="card" data-testid="fixer-config-error">
        <p className="text-sm text-[var(--color-text-muted)] m-0">Could not load the Fixer configuration.</p>
      </div>
    )
  }
  return (
    <FixerForm
      // Key on the server state so a successful save (or revalidation) re-seeds
      // the form — no set-state-in-effect syncing (same pattern as PolicyCard).
      key={JSON.stringify(data)}
      config={data}
      projectId={projectId}
      onSaved={() => void mutate()}
    />
  )
}
