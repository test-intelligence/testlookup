/**
 * Correct-classification dialog — US-2.4, lifted out of FailureAnalysisPage
 * for US-15.1 so every surface that renders AI trust chrome can wire its
 * "Correct" button to the SAME correction path instead of inventing a second
 * one.
 *
 * Resolves the test's latest AI analysis by fingerprint (or takes an
 * `analysisId` directly when the caller already has one), then submits
 * rating=incorrect + corrected_category feedback: the backend overwrites the
 * analysis category and persists an AIFeedback row for the training export.
 */
import { useState } from 'react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import { aiFeedbackService } from '@/services/aiFeedbackService'
import { useAnalysisLookup } from '@/hooks/useAnalysisLookup'
import { useModalFocus } from '@/hooks/useModalFocus'

export const CATEGORY_CHOICES = [
  { id: 'FLAKY',             label: 'Flaky',              desc: 'Intermittent — passes on retry; race or fixture issue.' },
  { id: 'PRODUCT_BUG',       label: 'Product Bug',        desc: 'Regression in the product under test.' },
  { id: 'INFRASTRUCTURE',    label: 'Infrastructure',     desc: 'Environment / network / platform failure.' },
  { id: 'TEST_DATA',         label: 'Test Data',          desc: 'Bad fixture, missing seed, stale snapshot.' },
  { id: 'AUTOMATION_DEFECT', label: 'Automation Defect',  desc: 'Test code is broken, not the product.' },
] as const

export type CategoryChoiceId = (typeof CATEGORY_CHOICES)[number]['id']

export default function CorrectClassificationModal({
  projectId, fingerprint, testName, analysisId: analysisIdProp, currentCategory: currentCategoryProp, onClose,
}: {
  /** Omit together with `fingerprint` when `analysisId` is supplied. */
  projectId?: string | null
  fingerprint?: string | null
  testName: string
  /** Pre-resolved analysis id — skips the fingerprint lookup entirely. */
  analysisId?: string | null
  currentCategory?: string | null
  onClose: () => void
}) {
  const skipLookup = Boolean(analysisIdProp)
  const { lookup, isLoading, isError } = useAnalysisLookup(
    skipLookup ? null : (projectId ?? null),
    skipLookup ? null : (fingerprint ?? null),
  )
  const [selected, setSelected] = useState<CategoryChoiceId | ''>('')
  const [comment, setComment] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const dialogRef = useModalFocus({ onClose, canClose: !submitting })

  const analysisId = analysisIdProp ?? lookup?.analysis_id ?? null
  const currentCategory = currentCategoryProp ?? lookup?.failure_category ?? 'UNKNOWN'

  async function handleSubmit() {
    if (!analysisId || !selected || submitting) return
    setSubmitting(true)
    try {
      await aiFeedbackService.submitFeedback(analysisId, {
        rating: 'incorrect',
        corrected_category: selected,
        ...(comment.trim() ? { comment: comment.trim() } : {}),
      })
      toast.success('Correction recorded — feeds the next training export.', { duration: 6000 })
      onClose()
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err as Error)?.message ??
        'Failed to record the correction'
      toast.error(detail)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label="Correct classification"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={() => !submitting && onClose()}
    >
      <div
        className="w-full max-w-md rounded-lg bg-[var(--color-bg-card)] border border-[var(--color-border)] p-5 shadow-xl"
        onClick={e => e.stopPropagation()}
      >
        <h2 className="text-base font-semibold text-[var(--color-text)] m-0">
          Correct classification
        </h2>
        <p className="mt-1 text-[12.5px] text-[var(--color-text-muted)]">
          <code className="font-mono text-[11.5px]">{testName}</code>
        </p>

        {isLoading && !skipLookup ? (
          <p className="mt-4 text-[12.5px] text-[var(--color-text-muted)]">
            Looking up the AI analysis…
          </p>
        ) : isError && !skipLookup ? (
          <p className="mt-4 text-[12.5px]" style={{ color: 'var(--gate-no-go)' }}>
            Could not look up the AI analysis — try again in a moment.
          </p>
        ) : !analysisId ? (
          <p className="mt-4 text-[12.5px] text-[var(--color-text-secondary)]">
            No AI analysis recorded for this test yet. Corrections overwrite an
            existing AI suggestion — once the analyzer has triaged a failure of
            this test, you can correct it here.
          </p>
        ) : (
          <>
            <p className="mt-3 text-[12.5px] text-[var(--color-text-secondary)]">
              Current category:{' '}
              <span className="font-mono text-[11.5px] bg-[var(--color-bg-secondary)] border border-[var(--color-border)] px-1.5 py-px rounded-sm">
                {currentCategory}
              </span>
            </p>

            <div className="mt-3 space-y-2" role="radiogroup" aria-label="Corrected category">
              {CATEGORY_CHOICES.map(c => (
                <button
                  key={c.id}
                  type="button"
                  role="radio"
                  aria-checked={selected === c.id}
                  disabled={submitting}
                  onClick={() => setSelected(c.id)}
                  className={clsx(
                    'w-full text-left px-3 py-2.5 rounded-md border transition-colors disabled:opacity-50',
                    selected === c.id
                      ? 'border-[var(--color-accent)] bg-[var(--color-bg-hover)]/40'
                      : 'border-[var(--color-border)] hover:border-[var(--color-accent)] hover:bg-[var(--color-bg-hover)]/40',
                  )}
                >
                  <div className="text-[13px] font-medium text-[var(--color-text)]">{c.label}</div>
                  <div className="text-[11.5px] text-[var(--color-text-muted)] mt-0.5">{c.desc}</div>
                </button>
              ))}
            </div>

            <label className="block mt-3 text-[12px] font-medium text-[var(--color-text)]">
              Comment <span className="text-[var(--color-text-faint)] font-normal">(optional)</span>
              <textarea
                value={comment}
                onChange={e => setComment(e.target.value)}
                rows={2}
                placeholder="What gave the misclassification away?"
                className="mt-1 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2.5 py-2 text-[12.5px] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
              />
            </label>
          </>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            disabled={submitting}
            className="text-[12px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] px-3 py-1.5 disabled:opacity-50"
          >
            {analysisId ? 'Cancel' : 'Close'}
          </button>
          {analysisId && (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={submitting || !selected}
              title={!selected ? 'Pick the corrected category first' : undefined}
              className="text-[12.5px] font-medium rounded-md px-3 py-1.5 disabled:opacity-50"
              style={{ background: 'var(--color-btn-primary-bg)', color: 'white' }}
            >
              {submitting ? 'Recording…' : 'Record correction'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
