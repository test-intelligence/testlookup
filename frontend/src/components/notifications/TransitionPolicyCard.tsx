/**
 * NTF-02: the top bar project's notification policy, as the third card of
 * Email (SMTP) & channels (QA lead and admin).
 *
 * What it edits is what the dispatcher reads (W09 contract §1.3):
 * "Notify on every run" gates Run failed, Run passed and High failure rate;
 * "Notify on state changes" and the seven checkboxes gate the test.* state
 * changes; the threshold is the failed runs in a row before "Test newly
 * failing". Save sends the WHOLE policy (the PUT replaces every field).
 *
 * The SMTP card's rule holds here: a failed or malformed read shows a
 * sentence and Retry, never the form at placeholder defaults (Save would
 * write them as the project's policy). In All Projects it asks for nothing.
 *
 * In the page (owner rule 2026-10-16): no modal, no confirm. It renders no
 * `ProjectRequiredEmptyState`: the page is not single-project, and the prompt
 * would make the route-scope ratchet demand it be declared so.
 */
import { useState } from 'react'
import toast from 'react-hot-toast'
import { AlertTriangle, CheckCircle, SlidersHorizontal } from 'lucide-react'
import Field from '@/components/ui/Field'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useTransitionPolicy } from '@/hooks/useTransitionPolicy'
import { updateTransitionPolicy } from '@/services/notificationPolicyService'
import { extractErrorMessage } from '@/services/apiErrors'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { parseTransitionPolicy, type TransitionPolicy, type TransitionPolicyBody } from '@/types/notifications'
import { EVENT_LABELS, TRANSITION_EVENTS } from './notificationEventsModel'

export const POLICY_ALL_PROJECTS_TEXT =
  'Each project has its own notification policy. Pick a project in the top bar to see and change it.'
export const POLICY_UNREADABLE_TEXT = "The project's notification policy could not be read."
export const POLICY_DEFAULTS_TEXT = 'Using defaults: state changes ON, every run OFF.'

const THRESHOLD_MIN = 1
const THRESHOLD_MAX = 20

const FIELD_LABEL = 'block text-xs font-medium text-[var(--color-text-muted)] mb-1.5'

type RequestError = { message?: string; response?: { data?: { detail?: unknown } } }

function Switch({
  label,
  description,
  checked,
  onChange,
}: {
  label: string
  description: string
  checked: boolean
  onChange: (next: boolean) => void
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <p className="text-sm text-[var(--color-text)]">{label}</p>
        <p className="text-xs text-[var(--color-text-muted)] mt-0.5">{description}</p>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={`relative w-11 h-6 shrink-0 rounded-full transition-colors ${
          checked ? 'bg-[var(--color-btn-primary-bg)]' : 'bg-[var(--color-bg-hover)]'
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-5' : ''
          }`}
        />
      </button>
    </div>
  )
}

/** The form, seeded once from a parsed policy; the card re-keys it when the stored policy changes. */
function PolicyForm({
  projectId,
  policy,
  onSaved,
}: {
  projectId: string
  policy: TransitionPolicy
  onSaved: () => Promise<unknown>
}) {
  const [perRun, setPerRun] = useState(policy.per_run_events_enabled)
  const [transitions, setTransitions] = useState(policy.transitions_enabled)
  const [events, setEvents] = useState<string[]>(() =>
    policy.enabled_events.filter(e => (TRANSITION_EVENTS as readonly string[]).includes(e)),
  )
  const [threshold, setThreshold] = useState(String(policy.consecutive_failure_threshold))
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const thresholdNumber = Number(threshold)
  const thresholdValid =
    threshold.trim() !== '' && Number.isInteger(thresholdNumber) && thresholdNumber >= THRESHOLD_MIN && thresholdNumber <= THRESHOLD_MAX

  const toggleEvent = (event: string) =>
    setEvents(prev => (prev.includes(event) ? prev.filter(e => e !== event) : [...prev, event]))

  const handleSave = async () => {
    if (!thresholdValid) return
    const body: TransitionPolicyBody = {
      transitions_enabled: transitions,
      per_run_events_enabled: perRun,
      // The server's order, whatever order they were ticked in.
      enabled_events: TRANSITION_EVENTS.filter(e => events.includes(e)),
      consecutive_failure_threshold: thresholdNumber,
    }
    setSaving(true)
    setSaveError(null)
    try {
      await updateTransitionPolicy(projectId, body)
      toast.success('Notification policy saved')
      await onSaved()
    } catch (err: unknown) {
      const e = err as RequestError
      setSaveError(extractErrorMessage(e?.response?.data?.detail, 'The policy could not be saved.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-5">
      {policy.is_default && (
        <p className="text-sm text-[var(--color-text-secondary)]" data-policy-defaults="">
          {POLICY_DEFAULTS_TEXT}
        </p>
      )}

      <Switch
        label="Notify on every run"
        description="Run failed, Run passed and High failure rate, once per run."
        checked={perRun}
        onChange={setPerRun}
      />
      <Switch
        label="Notify on state changes"
        description="Only when a test changes state: newly failing, recovered, newly flaky, quarantined and the quarantine lifecycle."
        checked={transitions}
        onChange={setTransitions}
      />

      <div>
        <p className="block text-xs font-medium text-[var(--color-text-muted)] mb-2">State changes sent</p>
        <div className="grid grid-cols-2 gap-2" data-policy-events="">
          {TRANSITION_EVENTS.map(event => (
            <label
              key={event}
              className={`flex items-center gap-2 ${transitions ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'}`}
            >
              <input
                type="checkbox"
                className="w-4 h-4 rounded border-[var(--color-border-light)] bg-[var(--color-bg-secondary)]"
                checked={events.includes(event)}
                disabled={!transitions}
                onChange={() => toggleEvent(event)}
              />
              <span className="text-sm text-[var(--color-text-secondary)]">{EVENT_LABELS[event]}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="max-w-xs">
        <Field label={'Failed runs in a row before "Test newly failing"'} labelClassName={FIELD_LABEL}>
          {id => (
            <input
              id={id}
              type="number"
              min={THRESHOLD_MIN}
              max={THRESHOLD_MAX}
              step={1}
              value={threshold}
              onChange={e => setThreshold(e.target.value)}
              className="input w-24 text-sm"
            />
          )}
        </Field>
        {!thresholdValid && (
          <p className="text-xs text-[var(--status-failed)] mt-1" data-policy-threshold-error="">
            {`Between ${THRESHOLD_MIN} and ${THRESHOLD_MAX}.`}
          </p>
        )}
      </div>

      <div className="flex items-center gap-2 pt-1">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !thresholdValid}
          className="btn-primary flex items-center gap-2 text-sm px-4 py-2 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? <LoadingSpinner size="sm" /> : <CheckCircle className="w-4 h-4" />}
          Save
        </button>
      </div>
      {saveError && (
        <p role="alert" className="text-sm text-[var(--status-failed)]" data-policy-save-error="">
          {saveError}
        </p>
      )}
    </div>
  )
}

/** A key that changes whenever the stored policy does, so the form re-seeds from it. */
function policyKey(p: TransitionPolicy): string {
  return [
    p.project_id,
    p.is_default ? 'default' : 'row',
    p.per_run_events_enabled,
    p.transitions_enabled,
    p.consecutive_failure_threshold,
    p.enabled_events.join(','),
  ].join('|')
}

export default function TransitionPolicyCard() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const activeProject = useProjectStore(s => s.activeProject)
  const projectId = activeProjectId && activeProjectId !== ALL_PROJECTS_ID ? activeProjectId : null
  const { policy: read, error, isLoading, mutate } = useTransitionPolicy(projectId)
  // Parsed again here: the form is never seeded from a shape it did not check.
  const policy = parseTransitionPolicy(read)

  let body: React.ReactNode
  if (!projectId) {
    body = <p className="text-sm text-[var(--color-text-muted)]">{POLICY_ALL_PROJECTS_TEXT}</p>
  } else if (isLoading) {
    body = (
      <div className="flex justify-center py-4">
        <LoadingSpinner size="sm" />
      </div>
    )
  } else if (error || !policy) {
    body = (
      <div
        role="alert"
        data-policy-unavailable=""
        className="flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm border-[var(--status-broken-bd)] bg-[var(--status-broken-bg)] text-[var(--status-broken)]"
      >
        <AlertTriangle className="mt-0.5 h-4 w-4 flex-shrink-0" aria-hidden />
        <span className="flex-1">{POLICY_UNREADABLE_TEXT}</span>
        <button
          type="button"
          onClick={() => void mutate()}
          className="shrink-0 text-xs font-medium underline text-[var(--color-text-secondary)] hover:text-[var(--color-text)]"
        >
          Retry
        </button>
      </div>
    )
  } else {
    body = <PolicyForm key={policyKey(policy)} projectId={projectId} policy={policy} onSaved={mutate} />
  }

  return (
    <section
      id="notification-policy"
      data-notification-policy=""
      aria-label="Project notification policy"
      className="card border border-[var(--color-border)] rounded-lg overflow-hidden"
    >
      <div className="flex items-center gap-3 p-4 border-b border-[var(--color-border)]">
        <div className="p-2 rounded-lg bg-[var(--color-bg-secondary)] text-[var(--color-text)]">
          <SlidersHorizontal className="w-4 h-4" />
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="font-semibold text-[var(--color-text)]">Project notification policy</h2>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            {projectId && activeProject?.name
              ? <>Which notifications <span className="text-[var(--color-text-secondary)]">{activeProject.name}</span> sends: every run, and state changes</>
              : 'Which notifications a project sends: every run, and state changes'}
          </p>
        </div>
      </div>
      <div className="p-4">{body}</div>
    </section>
  )
}
