import { useState } from 'react'
import { Check, FileText, Loader2, X } from 'lucide-react'
import { clsx } from 'clsx'
import toast from 'react-hot-toast'
import type { RagGenerateResponse } from '@/types/rag-generation'
import CitationDrawer from './CitationDrawer'
import { ragService } from '@/services/ragGenerationService'

interface Props {
  result: RagGenerateResponse
  onCaseAccepted?: () => void
}

export default function GenerationReviewPanel({ result, onCaseAccepted }: Props) {
  const [citationDrawerIdx, setCitationDrawerIdx] = useState<number | null>(null)
  const [acceptedIndices, setAcceptedIndices] = useState<Set<number>>(new Set())
  const [rejectedIndices, setRejectedIndices] = useState<Set<number>>(new Set())
  const [processing, setProcessing] = useState<number | null>(null)

  const handleAccept = async (idx: number) => {
    if (!result.created_ids[idx]) return
    setProcessing(idx)
    try {
      await ragService.acceptCase(result.batch_id, result.created_ids[idx])
      setAcceptedIndices(prev => new Set(prev).add(idx))
      toast.success('Case accepted')
      onCaseAccepted?.()
    } catch {
      toast.error('Failed to accept case')
    } finally {
      setProcessing(null)
    }
  }

  const handleReject = async (idx: number) => {
    if (!result.created_ids[idx]) return
    setProcessing(idx)
    try {
      await ragService.rejectCase(result.batch_id, result.created_ids[idx])
      setRejectedIndices(prev => new Set(prev).add(idx))
      toast.success('Case rejected')
    } catch {
      toast.error('Failed to reject case')
    } finally {
      setProcessing(null)
    }
  }

  return (
    <div className="space-y-4">
      {/* Batch header */}
      <div className="flex items-center gap-3 text-sm">
        <span className={clsx(
          'px-2 py-0.5 rounded text-xs font-medium',
          result.generation_mode === 'grounded'
            ? 'bg-[var(--status-passed-bg)]/20 text-[var(--status-passed)]'
            : 'bg-[var(--status-broken-bg)]/20 text-[var(--status-broken)]',
        )}>
          {result.generation_mode}
        </span>
        <span className="text-[var(--color-text-muted)]">
          {result.test_cases.length} cases generated
        </span>
        {result.coverage_summary && (
          <span className="text-[var(--color-text-muted)]">
            Coverage: {result.coverage_summary}
          </span>
        )}
      </div>

      {/* Gaps */}
      {result.gaps_noted.length > 0 && (
        <div className="rounded-lg border border-[var(--status-broken-bd)]/30 bg-[var(--status-broken-bg)]/10 px-4 py-2">
          <p className="text-xs font-medium text-[var(--status-broken)] mb-1">Uncovered Requirements</p>
          <ul className="text-xs text-[var(--status-broken)]/80 space-y-0.5">
            {result.gaps_noted.map((gap, i) => (
              <li key={i}>- {gap}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Generated cases */}
      <div className="space-y-3">
        {result.test_cases.map((tc, idx) => {
          const isAccepted = acceptedIndices.has(idx)
          const isRejected = rejectedIndices.has(idx)
          const isProcessing = processing === idx
          const hasCitations = result.citations.some(c => c.case_index === idx)

          return (
            <div
              key={idx}
              className={clsx(
                'rounded-lg border p-4 space-y-2',
                isAccepted && 'border-[var(--status-passed-bd)]/30 bg-[var(--status-passed-bg)]/5',
                isRejected && 'border-[var(--status-failed-bd)]/30 bg-[var(--status-failed-bg)]/5 opacity-60',
                !isAccepted && !isRejected && 'border-[var(--color-border)]',
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <h4 className="text-sm font-medium text-[var(--color-text)]">{tc.title}</h4>
                  {tc.description && (
                    <p className="text-xs text-[var(--color-text-muted)] mt-1 line-clamp-2">{tc.description}</p>
                  )}
                </div>
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]">
                    {tc.priority}
                  </span>
                  <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--color-bg-secondary)] text-[var(--color-text-muted)]">
                    {tc.test_type}
                  </span>
                </div>
              </div>

              {/* Steps preview */}
              {tc.steps && tc.steps.length > 0 && (
                <div className="text-xs text-[var(--color-text-faint)]">
                  {tc.steps.length} steps defined
                </div>
              )}

              {/* Actions */}
              <div className="flex items-center gap-2 pt-1">
                {!isAccepted && !isRejected && (
                  <>
                    <button
                      className="flex items-center gap-1 text-xs px-2.5 py-1 rounded bg-[var(--status-passed-bg)]/20 text-[var(--status-passed)] hover:bg-[var(--status-passed-bg)]/30 transition-colors"
                      onClick={() => handleAccept(idx)}
                      disabled={isProcessing}
                    >
                      {isProcessing ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
                      Accept
                    </button>
                    <button
                      className="flex items-center gap-1 text-xs px-2.5 py-1 rounded bg-[var(--status-failed-bg)]/20 text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]/30 transition-colors"
                      onClick={() => handleReject(idx)}
                      disabled={isProcessing}
                    >
                      <X className="h-3 w-3" />
                      Reject
                    </button>
                  </>
                )}
                {isAccepted && <span className="text-xs text-[var(--status-passed)]">Accepted</span>}
                {isRejected && <span className="text-xs text-[var(--status-failed)]">Rejected</span>}

                {hasCitations && (
                  <button
                    className="flex items-center gap-1 text-xs px-2.5 py-1 rounded text-[var(--color-text-muted)] hover:bg-[var(--color-bg-secondary)] transition-colors ml-auto"
                    onClick={() => setCitationDrawerIdx(idx)}
                  >
                    <FileText className="h-3 w-3" />
                    Citations
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {/* Citation drawer */}
      {citationDrawerIdx !== null && (
        <CitationDrawer
          citations={result.citations}
          caseIndex={citationDrawerIdx}
          onClose={() => setCitationDrawerIdx(null)}
        />
      )}
    </div>
  )
}
