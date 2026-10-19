/**
 * The QA lead's override of a run's release decision (GATE-01).
 *
 * One control, two hosts: the release gate's verdict card
 * (`/release-gate/:runId`) and the run page's Analysis verdict banner
 * (`/runs/:runId?tab=analysis`). Both write through the one endpoint,
 * `POST /api/v1/release-readiness/{run}/override` (QA lead + run access, a
 * required reason, an immutable audit entry), so there is one write path and
 * one audit trail.
 *
 * The API refuses an override when no decision is stored for the run (404);
 * a host should not offer the form then. A refusal is shown in the form: the
 * shared client already toasts a 403/422 with the server's own words, and a
 * 404 it leaves quiet.
 */
import { useState } from 'react'
import { Shield } from 'lucide-react'
import toast from 'react-hot-toast'
import { clsx } from 'clsx'
import { releaseCouncilService, type ReleaseCouncilDecision } from '@/services/releaseCouncilService'
import { extractErrorMessage } from '@/services/apiErrors'
import { DECISION_LABEL, DECISION_TOKEN } from '@/components/ui/statusVocabulary'

export type OverrideRecommendation = 'GO' | 'CONDITIONAL_GO' | 'NO_GO'

/**
 * The three values the API accepts, in the order the form offers them, in the
 * one decision vocabulary (W29a UIX-03): its words (Go / Conditional / No-Go)
 * and its legible ink for the chosen one.
 */
const OVERRIDE_OPTIONS: ReadonlyArray<{ value: OverrideRecommendation; label: string; colour: string }> = [
  { value: 'GO', label: DECISION_LABEL.go, colour: DECISION_TOKEN.go.ink },
  { value: 'CONDITIONAL_GO', label: DECISION_LABEL.conditional, colour: DECISION_TOKEN.conditional.ink },
  { value: 'NO_GO', label: DECISION_LABEL.no_go, colour: DECISION_TOKEN.no_go.ink },
]

/** What the API said when it refused, in its own words where it gave some. */
function refusalText(error: unknown): string {
  const response = (error as { response?: { status?: number; data?: { detail?: unknown } } })?.response
  if (response?.status === 404) {
    return 'No stored release decision for this run: run deep investigation first.'
  }
  return extractErrorMessage(response?.data?.detail, 'Override failed: QA Lead role required.')
}

export default function OverrideDecisionForm({
  runId,
  initialRecommendation = 'GO',
  onApplied,
  onCancel,
  className,
}: {
  runId: string
  /** The value the form opens on (the run page's Hold opens on NO_GO). */
  initialRecommendation?: OverrideRecommendation
  /** Called with the decision as stored after the override; revalidate here. */
  onApplied: (updated: ReleaseCouncilDecision) => void | Promise<void>
  onCancel: () => void
  className?: string
}) {
  const [recommendation, setRecommendation] = useState<OverrideRecommendation>(initialRecommendation)
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const submit = async () => {
    const trimmed = reason.trim()
    if (!trimmed) {
      toast.error('Please provide a reason for the override')
      return
    }
    setSubmitting(true)
    setRefusal(null)
    try {
      const updated = await releaseCouncilService.override(runId, recommendation, trimmed)
      toast.success('Release decision overridden')
      setReason('')
      await onApplied(updated)
    } catch (error) {
      setRefusal(refusalText(error))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      data-override-form=""
      className={clsx('space-y-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-card)] p-3', className)}
    >
      <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--color-text)]">
        <Shield className="w-4 h-4 text-[var(--color-text-muted)]" />
        QA Lead Override
      </h3>
      <div className="flex gap-2" role="group" aria-label="Override to">
        {OVERRIDE_OPTIONS.map(option => (
          <button
            key={option.value}
            type="button"
            aria-pressed={recommendation === option.value}
            onClick={() => setRecommendation(option.value)}
            className={clsx(
              'px-3 py-1.5 rounded text-xs font-medium transition-colors',
              recommendation === option.value
                ? 'bg-[var(--color-bg-hover)]'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
            )}
            style={recommendation === option.value ? { color: option.colour } : undefined}
          >
            {option.label}
          </button>
        ))}
      </div>
      <textarea
        value={reason}
        onChange={e => setReason(e.target.value)}
        placeholder="Reason for override (required)…"
        aria-label="Reason for override"
        rows={3}
        className="w-full bg-[var(--color-bg-secondary)] border border-[var(--color-border)] rounded-lg px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-faint)] focus:outline-none focus:border-[var(--color-border)] resize-none"
      />
      {refusal && (
        <p role="alert" className="text-xs text-[var(--status-failed)]">{refusal}</p>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => void submit()}
          disabled={submitting}
          className="btn-primary"
        >
          {submitting ? 'Saving…' : 'Apply Override'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)] text-sm"
        >
          Cancel
        </button>
      </div>
    </div>
  )
}
