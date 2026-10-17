/**
 * W09 FLK-02: the project's quarantine lifecycle policy, the card of Flaky
 * tests › Policy (`/flaky?tab=policy`).
 *
 * What it edits is what the server reads: the SLA an approved quarantine gets
 * (`stale_at = approved_at + SLA`), the internal defect filed on approval, the
 * pass streak that releases (or only flags "Ready to promote"), the detection
 * floor the flaky sentinel proposes from, and the cap past which the
 * Quarantined tab warns. Save sends the WHOLE policy (the PUT replaces every
 * field, `max_active_quarantined` included).
 *
 * A QA lead or admin edits; everyone else sees the same card with disabled
 * inputs and no Save. A failed or malformed read shows a sentence and Retry,
 * never the form at placeholder values (Save would store them).
 *
 * In the page (owner rule 2026-10-16): a card in a tab, no modal, no confirm.
 */
import { useState, type ReactNode } from 'react'
import toast from 'react-hot-toast'
import { AlertTriangle, CheckCircle, ShieldAlert } from 'lucide-react'
import Field from '@/components/ui/Field'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { useQuarantinePolicy } from '@/hooks/useQuarantinePolicy'
import { extractErrorMessage } from '@/services/apiErrors'
import {
  parseQuarantinePolicy,
  quarantinePolicyService,
  type QuarantinePolicy,
  type QuarantinePolicyBody,
} from '@/services/quarantinePolicyService'
import { activeNowText, percentToRate, policyDefaultsText, rateToPercent } from './flakyModel'

export const POLICY_UNREADABLE_TEXT = 'The quarantine policy could not be read.'
export const POLICY_READ_ONLY_TEXT = 'Only a QA lead can change the quarantine policy.'

const LABEL = 'block text-xs font-medium text-[var(--color-text-muted)] mb-1.5'
const HINT = 'mt-1 text-xs text-[var(--color-text-muted)]'
/** The range beside a field whose value is outside it. */
const RANGE = 'text-xs text-[var(--status-failed)]'

type RequestError = { response?: { data?: { detail?: unknown } } }

/** A whole number in [min, max], or null. */
function intIn(raw: string, min: number, max: number): number | null {
  if (raw.trim() === '') return null
  const n = Number(raw)
  return Number.isInteger(n) && n >= min && n <= max ? n : null
}

/** A number in [min, max] (decimals allowed), or null. */
function numberIn(raw: string, min: number, max: number): number | null {
  if (raw.trim() === '') return null
  const n = Number(raw)
  return Number.isFinite(n) && n >= min && n <= max ? n : null
}

function NumberField({
  label,
  value,
  onChange,
  disabled,
  min,
  max,
  step = 1,
  error,
  hint,
  inputClassName = 'w-28',
}: {
  label: ReactNode
  value: string
  onChange: (next: string) => void
  disabled: boolean
  min: number
  max: number
  step?: number | 'any'
  /** The range, shown beside the field while the value is outside it. */
  error: string | null
  hint?: ReactNode
  inputClassName?: string
}) {
  return (
    <div>
      <Field label={label} labelClassName={LABEL}>
        {(id) => (
          <div className="flex items-center gap-2">
            <input
              id={id}
              type="number"
              min={min}
              max={max}
              step={step}
              value={value}
              disabled={disabled}
              onChange={(e) => onChange(e.target.value)}
              aria-invalid={error ? true : undefined}
              className={`input text-sm ${inputClassName} disabled:opacity-60`}
            />
            {error && <span className={RANGE} data-policy-range="">{error}</span>}
          </div>
        )}
      </Field>
      {hint && <p className={HINT}>{hint}</p>}
    </div>
  )
}

/** The form, seeded once from a parsed policy; the card re-keys it when the stored policy changes. */
function PolicyForm({
  projectId,
  policy,
  canEdit,
  activeCount,
  onSaved,
}: {
  projectId: string
  policy: QuarantinePolicy
  canEdit: boolean
  activeCount: number | undefined
  onSaved: (saved: QuarantinePolicy | null) => Promise<unknown>
}) {
  const [sla, setSla] = useState(String(policy.sla_days))
  const [autoDefect, setAutoDefect] = useState(policy.auto_create_defect)
  const [autoPromote, setAutoPromote] = useState(policy.auto_promote)
  const [passes, setPasses] = useState(String(policy.promote_after_passes))
  const [threshold, setThreshold] = useState(String(rateToPercent(policy.detection_flip_rate_threshold)))
  const [minRuns, setMinRuns] = useState(String(policy.detection_min_runs))
  const [cap, setCap] = useState(String(policy.max_active_quarantined))
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const values = {
    sla: intIn(sla, 1, 365),
    passes: intIn(passes, 1, 1000),
    threshold: numberIn(threshold, 0, 100),
    minRuns: intIn(minRuns, 1, 1000),
    cap: intIn(cap, 0, 1000),
  }
  const valid = Object.values(values).every((v) => v !== null)
  const disabled = !canEdit || saving
  const activeNow = activeNowText(activeCount, values.cap ?? policy.max_active_quarantined)

  const handleSave = async () => {
    if (!canEdit || !valid) return
    const body: QuarantinePolicyBody = {
      sla_days: values.sla as number,
      auto_create_defect: autoDefect,
      auto_promote: autoPromote,
      promote_after_passes: values.passes as number,
      detection_flip_rate_threshold: percentToRate(values.threshold as number),
      detection_min_runs: values.minRuns as number,
      max_active_quarantined: values.cap as number,
    }
    setSaving(true)
    setSaveError(null)
    try {
      const saved = await quarantinePolicyService.update(projectId, body)
      toast.success('Quarantine policy saved')
      await onSaved(parseQuarantinePolicy(saved))
    } catch (err: unknown) {
      const e = err as RequestError
      setSaveError(extractErrorMessage(e?.response?.data?.detail, 'The quarantine policy could not be saved.'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-5">
      {policy.is_default && (
        <p className="text-sm text-[var(--color-text-secondary)]" data-policy-defaults="">
          {policyDefaultsText(policy)}
        </p>
      )}
      {!canEdit && (
        <p className="text-sm text-[var(--color-text-muted)]" data-policy-read-only="">
          {POLICY_READ_ONLY_TEXT}
        </p>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <NumberField
          label="SLA (days a test may stay quarantined)"
          value={sla}
          onChange={setSla}
          disabled={disabled}
          min={1}
          max={365}
          error={values.sla === null ? '1-365 days' : null}
          hint="A quarantine still in place after this many days is flagged stale."
        />

        <div>
          <label className="flex items-start gap-2 text-sm text-[var(--color-text)]">
            <input
              type="checkbox"
              className="mt-0.5 h-4 w-4 rounded border-[var(--color-border-light)] bg-[var(--color-bg-secondary)] disabled:opacity-60"
              checked={autoDefect}
              disabled={disabled}
              onChange={(e) => setAutoDefect(e.target.checked)}
            />
            <span>
              File an internal defect when a quarantine is approved
              <span className="block text-xs text-[var(--color-text-muted)]">
                An internal TestLookup defect. No Jira issue is created.
              </span>
            </span>
          </label>
        </div>

        <div>
          <div className="flex flex-wrap items-center gap-2 text-sm text-[var(--color-text)]">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                className="h-4 w-4 rounded border-[var(--color-border-light)] bg-[var(--color-bg-secondary)] disabled:opacity-60"
                checked={autoPromote}
                disabled={disabled}
                onChange={(e) => setAutoPromote(e.target.checked)}
                aria-label="Release automatically after the consecutive passes below"
              />
              <span aria-hidden>Release automatically after</span>
            </label>
            <input
              type="number"
              min={1}
              max={1000}
              step={1}
              value={passes}
              disabled={disabled}
              onChange={(e) => setPasses(e.target.value)}
              aria-label="Release after this many consecutive passes"
              aria-invalid={values.passes === null ? true : undefined}
              className="input w-24 text-sm disabled:opacity-60"
            />
            <span>consecutive passes</span>
            {values.passes === null && <span className={RANGE} data-policy-range="">1-1000 passes</span>}
          </div>
          <p className={HINT}>Off: the test is only flagged “Ready to promote” for a person to release.</p>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <NumberField
            label="Detection threshold (flip rate, %)"
            value={threshold}
            onChange={setThreshold}
            disabled={disabled}
            min={0}
            max={100}
            step="any"
            error={values.threshold === null ? '0-100 %' : null}
            inputClassName="w-24"
          />
          <NumberField
            label="Minimum runs"
            value={minRuns}
            onChange={setMinRuns}
            disabled={disabled}
            min={1}
            max={1000}
            error={values.minRuns === null ? '1-1000 runs' : null}
            inputClassName="w-24"
          />
        </div>

        <NumberField
          label="Warn when more than this many tests are quarantined"
          value={cap}
          onChange={setCap}
          disabled={disabled}
          min={0}
          max={1000}
          error={values.cap === null ? '0-1000 tests' : null}
          hint={
            <>
              0 = no limit. The Quarantined tab warns past it; nothing is blocked.
              {activeNow && <span data-policy-active=""> {activeNow}</span>}
            </>
          }
        />
      </div>

      {canEdit && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving || !valid}
            className="btn-primary flex items-center gap-2 text-sm px-4 py-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? <LoadingSpinner size="sm" /> : <CheckCircle className="w-4 h-4" />}
            Save
          </button>
        </div>
      )}
      {saveError && (
        <p role="alert" className="text-sm text-[var(--status-failed)]" data-policy-save-error="">
          {saveError}
        </p>
      )}
    </div>
  )
}

/** A key that changes whenever the stored policy does, so the form re-seeds from it. */
function policyKey(p: QuarantinePolicy): string {
  return [
    p.project_id,
    p.is_default ? 'default' : 'row',
    p.sla_days,
    p.auto_create_defect,
    p.auto_promote,
    p.promote_after_passes,
    p.detection_flip_rate_threshold,
    p.detection_min_runs,
    p.max_active_quarantined,
  ].join('|')
}

export default function QuarantinePolicyCard({
  projectId,
  canEdit,
  activeCount,
}: {
  projectId: string
  /** QA lead and above (the PUT's role). */
  canEdit: boolean
  /** Active quarantines now (the page's stats), for "3 of 8 active now."; undefined until they answer. */
  activeCount: number | undefined
}) {
  const { policy: read, error, isLoading, mutate } = useQuarantinePolicy(projectId, true)
  // Parsed again here: the form is never seeded from a shape it did not check.
  const policy = parseQuarantinePolicy(read)

  let body: ReactNode
  if (isLoading) {
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
    body = (
      <PolicyForm
        key={policyKey(policy)}
        projectId={projectId}
        policy={policy}
        canEdit={canEdit}
        activeCount={activeCount}
        onSaved={(saved) => (saved ? mutate(saved) : mutate())}
      />
    )
  }

  return (
    <section
      data-primary=""
      data-quarantine-policy=""
      aria-label="Quarantine policy"
      className="card border border-[var(--color-border)] rounded-lg overflow-hidden"
    >
      <div className="flex items-center gap-3 p-4 border-b border-[var(--color-border)]">
        <div className="p-2 rounded-lg bg-[var(--color-bg-secondary)] text-[var(--color-text)]">
          <ShieldAlert className="w-4 h-4" aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="font-semibold text-[var(--color-text)]">Quarantine policy</h2>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            How long a quarantine lasts, what approving one files, when a test is released, and what the detector proposes.
          </p>
        </div>
      </div>
      <div className="p-4">{body}</div>
    </section>
  )
}
