import { FileText, X } from 'lucide-react'
import type { Citation } from '@/types/rag-generation'

interface Props {
  citations: Citation[]
  caseIndex: number
  onClose: () => void
}

export default function CitationDrawer({ citations, caseIndex, onClose }: Props) {
  const caseCitations = citations.filter(c => c.case_index === caseIndex)

  return (
    <div className="fixed inset-y-0 right-0 w-96 bg-[var(--color-bg-card)] border-l border-[var(--color-border)] shadow-2xl z-50 flex flex-col">
      <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--color-border)]">
        <h3 className="text-sm font-semibold text-[var(--color-text)]">
          Citations ({caseCitations.length})
        </h3>
        <button onClick={onClose} className="p-1 rounded hover:bg-[var(--color-bg-secondary)]">
          <X className="h-4 w-4 text-[var(--color-text-muted)]" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {caseCitations.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">No citations for this case.</p>
        ) : (
          caseCitations.map((cit, i) => (
            <div key={i} className="rounded-lg border border-[var(--color-border)] p-3 space-y-1.5">
              <div className="flex items-center gap-2">
                <FileText className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                <span className="text-xs font-medium text-[var(--color-text)]">{cit.source_title}</span>
                {cit.relevance_score != null && (
                  <span className="text-xs text-[var(--color-text-faint)] ml-auto">
                    {(cit.relevance_score * 100).toFixed(0)}% match
                  </span>
                )}
              </div>
              {cit.section_heading && (
                <p className="text-xs text-[var(--color-text-muted)]">{cit.section_heading}</p>
              )}
              {cit.chunk_text_preview && (
                <p className="text-xs text-[var(--color-text-faint)] line-clamp-4">{cit.chunk_text_preview}</p>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
