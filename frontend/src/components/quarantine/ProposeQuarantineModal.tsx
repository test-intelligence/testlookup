/**
 * Propose-quarantine modal — a lifted, prefill-friendly variant of the
 * US-2.4 MuteTestModal on the Failure Analysis page, reused by the Ask-AI
 * copilot's action handoffs (AI-6).
 *
 * Submission goes through the SAME audited flow as /failures: a manual
 * PROPOSED row via POST /api/v1/quarantine (QA_LEAD+), which then awaits
 * approval on /quarantine. The required reason lands in the proposal's
 * rationale and the quarantine audit trail. The agent never submits —
 * a human reviews, types the reason, and clicks.
 */
import { useState } from 'react'
import toast from 'react-hot-toast'
import { flakyQuarantineService } from '@/services/flakyQuarantineService'

export interface QuarantinePrefill {
  project_id: string
  test_fingerprint: string
  test_name: string
  suite_name?: string | null
  fail_count?: number | null
}

export default function ProposeQuarantineModal({
  prefill, source, onClose,
}: {
  prefill: QuarantinePrefill
  /** Audit tag recorded in the proposal rationale (e.g. "ask-ai-copilot"). */
  source: string
  onClose: () => void
}) {
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const trimmedReason = reason.trim()

  async function handleSubmit() {
    if (!trimmedReason || submitting) return
    setSubmitting(true)
    try {
      const row = await flakyQuarantineService.propose({
        project_id: prefill.project_id,
        test_fingerprint: prefill.test_fingerprint,
        test_name: prefill.test_name,
        suite_name: prefill.suite_name ?? null,
        detection_method: 'manual',
        ...(prefill.fail_count ? { fail_count: prefill.fail_count } : {}),
        rationale: { reason: trimmedReason, source },
      })
      toast.success(
        row.status === 'PROPOSED'
          ? 'Quarantine proposal created — pending QA Lead approval on /quarantine.'
          : `Quarantine request updated — now ${row.status} (see /quarantine).`,
        { duration: 8000 },
      )
      onClose()
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to create the quarantine proposal'
      toast.error(detail)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Propose quarantine"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={() => !submitting && onClose()}
    >
      <div
        className="w-full max-w-md rounded-lg bg-[var(--color-bg-card)] border border-[var(--color-border)] p-5 shadow-xl"
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold text-[var(--color-text)] m-0">
          Propose quarantine
        </h2>
        <p className="mt-1 text-[12.5px] text-[var(--color-text-muted)]">
          Proposes <code className="font-mono text-[11.5px]">{prefill.test_name}</code> for
          quarantine. A QA Lead approves or rejects the proposal on /quarantine —
          nothing is muted until then.
        </p>

        <label className="block mt-3 text-[12px] font-medium text-[var(--color-text)]">
          Reason <span style={{ color: 'var(--status-failed)' }}>*</span>
          <textarea
            value={reason}
            onChange={e => setReason(e.target.value)}
            rows={3}
            placeholder="Why should this test stop gating runs? (goes to the quarantine audit trail)"
            className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2 text-[12.5px] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
          />
        </label>

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-[12px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-3 py-1.5 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting || !trimmedReason}
            title={!trimmedReason ? 'A reason is required' : undefined}
            className="text-[12.5px] font-medium rounded-md px-3 py-1.5 disabled:opacity-50"
            style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
          >
            {submitting ? 'Proposing…' : 'Propose quarantine'}
          </button>
        </div>
      </div>
    </div>
  )
}
