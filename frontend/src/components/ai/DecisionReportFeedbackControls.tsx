import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import type { DecisionClaim, DecisionReportVersion, DecisionReportCorrectionType, DecisionReportUtilityRating } from '@/services/runIntelligenceService'
import { submitDecisionReportFeedback } from '@/services/runIntelligenceService'
import { useModalFocus } from '@/hooks/useModalFocus'

type Props = {
  runId: string
  reportVersion: DecisionReportVersion | null | undefined
  claims: DecisionClaim[]
}

const utilityChoices: Array<[DecisionReportUtilityRating, string]> = [
  ['useful', 'Useful'],
  ['partially_useful', 'Partly useful'],
  ['not_useful', 'Not useful'],
]

const correctionChoices: Array<[DecisionReportCorrectionType, string]> = [
  ['category', 'Category'],
  ['cause', 'Root cause'],
  ['flaky', 'Flaky assessment'],
  ['release', 'Release recommendation'],
]

function refId(ref: Record<string, unknown>): string {
  return String(ref.id ?? ref.evidence_id ?? '')
}

export default function DecisionReportFeedbackControls({ runId, reportVersion, claims }: Props) {
  const [utilityStatus, setUtilityStatus] = useState<string | null>(null)
  const [selectedClaim, setSelectedClaim] = useState<DecisionClaim | null>(null)
  const [correctionType, setCorrectionType] = useState<DecisionReportCorrectionType>('category')
  const [correctedValue, setCorrectedValue] = useState('')
  const [reason, setReason] = useState('')
  const [evidenceId, setEvidenceId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const correctionDialogRef = useModalFocus<HTMLFormElement>({
    onClose: () => setSelectedClaim(null),
    canClose: !submitting,
    open: selectedClaim !== null,
  })

  const evidenceOptions = useMemo(() => {
    if (!selectedClaim) return []
    return [...selectedClaim.evidence, ...selectedClaim.counter_evidence]
      .map(refId)
      .filter(Boolean)
      .filter((id, index, all) => all.indexOf(id) === index)
      .slice(0, 5)
  }, [selectedClaim])

  if (!reportVersion) return null
  const report = reportVersion

  async function submitUtility(rating: DecisionReportUtilityRating) {
    setError(null)
    try {
      await submitDecisionReportFeedback(runId, report.report_id, {
        report_version: report.report_version,
        feedback_kind: 'utility',
        utility_rating: rating,
      })
      setUtilityStatus('Thanks — utility feedback recorded.')
    } catch {
      setError('Could not record report feedback.')
    }
  }

  async function submitCorrection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedClaim || !reason.trim() || !correctedValue.trim() || !evidenceId) return
    setSubmitting(true)
    setError(null)
    const value = correctionType === 'flaky'
      ? correctedValue.trim().toLowerCase() === 'true'
      : correctedValue.trim()
    try {
      await submitDecisionReportFeedback(runId, report.report_id, {
        report_version: report.report_version,
        feedback_kind: 'claim_correction',
        claim_id: selectedClaim.claim_id,
        correction_type: correctionType,
        corrected_value: value,
        reason: reason.trim(),
        evidence_ids: [evidenceId],
      })
      setUtilityStatus('Correction recorded for this report version.')
      setSelectedClaim(null)
      setCorrectedValue('')
      setReason('')
      setEvidenceId('')
    } catch {
      setError('Could not record the correction.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mt-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-bg-secondary)] p-3" data-testid="decision-report-feedback">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[12px] font-semibold">Was this report useful?</span>
        {utilityChoices.map(([rating, label]) => (
          <button key={rating} type="button" className="rounded border border-[var(--color-border)] px-2 py-1 text-[11px] hover:border-[var(--color-accent)]" onClick={() => void submitUtility(rating)}>
            {label}
          </button>
        ))}
        {utilityStatus && <span role="status" className="text-[11px] text-[var(--color-text-secondary)]">{utilityStatus}</span>}
      </div>
      {claims.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-[var(--color-text-muted)]">Correct a claim:</span>
          {claims.slice(0, 8).map(claim => (
            <button key={claim.claim_id} type="button" className="text-[11px] font-semibold text-[var(--color-accent)] hover:underline" onClick={() => {
              setSelectedClaim(claim)
              setEvidenceId(refId(claim.evidence[0] ?? claim.counter_evidence[0] ?? {}))
              setError(null)
            }}>
              {claim.claim_id}
            </button>
          ))}
        </div>
      )}
      {error && <p role="alert" className="mb-0 mt-2 text-[11px] text-[var(--status-failed)]">{error}</p>}
      {selectedClaim && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="presentation" onClick={() => setSelectedClaim(null)}>
          <form ref={correctionDialogRef} role="dialog" aria-modal="true" aria-labelledby="claim-correction-heading" className="w-full max-w-lg rounded-xl border border-[var(--color-border)] bg-[var(--color-bg-card)] p-4 shadow-xl" onClick={event => event.stopPropagation()} onSubmit={submitCorrection}>
            <h3 id="claim-correction-heading" className="m-0 text-[16px] font-semibold">Correct claim</h3>
            <p className="mt-2 text-[12px] text-[var(--color-text-secondary)]">{selectedClaim.text}</p>
            <label className="mt-3 block text-[12px] font-semibold">Correction type
              <select className="mt-1 block w-full rounded border border-[var(--color-border)] bg-transparent p-2 text-[12px]" value={correctionType} onChange={event => setCorrectionType(event.target.value as DecisionReportCorrectionType)}>
                {correctionChoices.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
            </label>
            <label className="mt-3 block text-[12px] font-semibold">Correct value
              <input required value={correctedValue} onChange={event => setCorrectedValue(event.target.value)} className="mt-1 block w-full rounded border border-[var(--color-border)] bg-transparent p-2 text-[12px]" placeholder={correctionType === 'flaky' ? 'true or false' : 'Your correction'} />
            </label>
            <label className="mt-3 block text-[12px] font-semibold">Reason
              <textarea required value={reason} onChange={event => setReason(event.target.value)} className="mt-1 block min-h-20 w-full rounded border border-[var(--color-border)] bg-transparent p-2 text-[12px]" placeholder="What should change and why?" />
            </label>
            <label className="mt-3 block text-[12px] font-semibold">Supporting evidence
              <select required value={evidenceId} onChange={event => setEvidenceId(event.target.value)} className="mt-1 block w-full rounded border border-[var(--color-border)] bg-transparent p-2 text-[12px]">
                <option value="">Select bound evidence</option>
                {evidenceOptions.map(id => <option key={id} value={id}>{id}</option>)}
              </select>
            </label>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className="rounded border border-[var(--color-border)] px-3 py-1.5 text-[12px]" onClick={() => setSelectedClaim(null)}>Cancel</button>
              <button type="submit" disabled={submitting || evidenceOptions.length === 0} className="rounded bg-[var(--color-accent)] px-3 py-1.5 text-[12px] font-semibold text-white disabled:opacity-50">{submitting ? 'Recording…' : 'Record correction'}</button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
