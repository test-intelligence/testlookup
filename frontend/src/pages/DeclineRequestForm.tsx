/**
 * Decline an erasure request (W38, W31 F-1, contract §4), opened under the queue row: one of the four fixed reasons
 * (D-2: a code, never free text) — the person reads that reason's sentence on their Profile, shown here before
 * sending. "Decline request" is the only sender and stays disabled until a reason is chosen. A refusal
 * (`request_not_pending`, `own_request`, `request_not_found`, `decline_code_invalid`) renders inline as its sentence.
 */
import { useId, useState } from 'react'

import InlineFormSection from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'
import LoadingSpinner from '@/components/ui/LoadingSpinner'
import { subjectRightsErrorMessage, subjectRightsService } from '@/services/subjectRightsService'
import type { DeclineCode, ErasureRequestRead } from '@/types/subjectRights'
import {
  DECLINE_FAILED,
  DECLINE_OPTIONS,
  DECLINE_REASON_LABEL,
  DECLINE_REASON_PLACEHOLDER,
  DECLINE_REQUEST,
  declineLabel,
  declinePreview,
} from './erasureRequestsModel'

interface Props {
  row: ErasureRequestRead
  slot?: InlineSlot
  onClose: () => void
  /** The decline was saved: close the form and re-read the queue. */
  onDeclined: (row: ErasureRequestRead) => void
}

export function DeclineRequestForm({ row, slot, onClose, onDeclined }: Props) {
  const selectId = useId()
  const [code, setCode] = useState<DeclineCode | ''>('')
  const [busy, setBusy] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  async function decline() {
    if (!code || busy) return
    setBusy(true)
    setRefusal(null)
    try {
      const decided = await subjectRightsService.declineErasureRequest(row.id, code)
      onDeclined(decided)
    } catch (err: unknown) {
      setRefusal(await subjectRightsErrorMessage(err, DECLINE_FAILED))
    } finally {
      setBusy(false)
    }
  }

  return (
    <InlineFormSection
      title={declineLabel(row)}
      headingLevel={3}
      onClose={onClose}
      canClose={!busy}
      slot={slot}
      busy={busy}
      dataAttributes={{ 'data-decline-request': row.id }}
      footer={
        <div className="flex items-center justify-end gap-2">
          <button type="button" className="btn-secondary text-xs" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-danger inline-flex items-center gap-1.5 text-xs"
            onClick={() => void decline()}
            disabled={!code || busy}
          >
            {busy && <LoadingSpinner size="sm" />}
            {DECLINE_REQUEST}
          </button>
        </div>
      }
    >
      <div className="flex flex-col gap-2">
        <label htmlFor={selectId} className="text-xs text-[var(--color-text-secondary)]">
          {DECLINE_REASON_LABEL}
        </label>
        <select
          id={selectId}
          className="input w-72"
          value={code}
          disabled={busy}
          onChange={(event) => setCode(event.target.value as DeclineCode | '')}
        >
          <option value="">{DECLINE_REASON_PLACEHOLDER}</option>
          {DECLINE_OPTIONS.map((option) => (
            <option key={option.code} value={option.code}>
              {option.label}
            </option>
          ))}
        </select>
        {code && (
          <p className="m-0 text-xs text-[var(--color-text-muted)]" data-decline-preview="">
            {declinePreview(code)}
          </p>
        )}
        {refusal && (
          <p role="alert" className="m-0 text-xs text-[var(--ui-danger-ink)]" data-decline-refusal="">
            {refusal}
          </p>
        )}
      </div>
    </InlineFormSection>
  )
}
