/**
 * Propose-quarantine form — a lifted, prefill-friendly variant of the
 * US-2.4 MuteTestModal on the Failure Analysis page, reused by Flaky tests ›
 * Detected and the Ask-AI copilot's action handoffs (AI-6).
 *
 * It renders IN THE PAGE where its action lives (W27 PAN-04): under the
 * Detected row it was opened from, or inside the chat message; an
 * `InlineFormSection` named "Propose quarantine", as the dialog was.
 *
 * Submission goes through the SAME audited flow as /failures: a manual
 * PROPOSED row via POST /api/v1/quarantine (QA_LEAD+), which then awaits
 * approval under Flaky tests › Proposed (or the Inbox's Approvals). The required reason lands in the proposal's
 * rationale and the quarantine audit trail. The agent never submits —
 * a human reviews, types the reason, and clicks.
 */
import { useState } from 'react'
import toast from 'react-hot-toast'
import { flakyQuarantineService } from '@/services/flakyQuarantineService'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'

export interface QuarantinePrefill {
  project_id: string
  test_fingerprint: string
  test_name: string
  suite_name?: string | null
  fail_count?: number | null
}

export default function ProposeQuarantineModal({
  prefill, source, onClose, slot,
}: {
  prefill: QuarantinePrefill
  /** Audit tag recorded in the proposal rationale (e.g. "ask-ai-copilot"). */
  source: string
  onClose: () => void
  /** The host section's slot: one form open there; a typed reason asks before it is replaced. */
  slot?: InlineSlot
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
          ? 'Quarantine proposal created — pending QA Lead approval under Flaky tests › Proposed.'
          : `Quarantine request updated — now ${row.status} (see Flaky tests).`,
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
    <InlineFormSection
      title="Propose quarantine"
      description={
        <span className="text-[12.5px]">
          Proposes <code className="font-mono text-[11.5px] [overflow-wrap:anywhere]">{prefill.test_name}</code> for
          quarantine. A QA Lead approves or rejects it under Flaky tests › Proposed —
          nothing is muted until then.
        </span>
      }
      onClose={onClose}
      canClose={!submitting}
      showClose={false}
      busy={submitting}
      slot={slot}
      footer={
        <>
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
            className="btn-primary btn-sm"
          >
            {submitting ? 'Proposing…' : 'Propose quarantine'}
          </button>
        </>
      }
    >
      <label className="block max-w-2xl text-[12px] font-medium text-[var(--color-text)]">
        Reason <span style={{ color: 'var(--status-failed)' }}>*</span>
        <textarea
          value={reason}
          onChange={e => setReason(e.target.value)}
          rows={3}
          placeholder="Why should this test stop gating runs? (goes to the quarantine audit trail)"
          className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2 text-[12.5px] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
        />
      </label>
    </InlineFormSection>
  )
}
