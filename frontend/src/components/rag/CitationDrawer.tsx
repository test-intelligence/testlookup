/**
 * A generated case's citations: the knowledge-source passages the case was
 * grounded on, with each source's link.
 *
 * It renders IN THE PAGE (W27b PAN-05), right after the case card whose
 * Citations button opened it: a read-only `InlineFormSection as="aside"` named
 * by its heading `Citations (<n>)`, X `Close`, Escape inside it closes it. It
 * was a fixed edge panel over the page. Every name and passage renders as
 * text; a source link is shown only for an http(s) URL.
 */
import { FileText } from 'lucide-react'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { Citation } from '@/types/rag-generation'

interface Props {
  citations: Citation[]
  caseIndex: number
  onClose: () => void
}

function safeSourceUrl(value: string | null | undefined): string | null {
  if (!value) return null
  try {
    const parsed = new URL(value)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null
  } catch {
    return null
  }
}

export default function CitationDrawer({ citations, caseIndex, onClose }: Props) {
  const caseCitations = citations.filter(c => c.case_index === caseIndex)

  return (
    <InlineFormSection
      as="aside"
      title={`Citations (${caseCitations.length})`}
      headingLevel={3}
      onClose={onClose}
    >
      <div className="space-y-3">
        {caseCitations.length === 0 ? (
          <p className="text-sm text-[var(--color-text-muted)]">No citations for this case.</p>
        ) : (
          caseCitations.map((cit, i) => {
            const sourceUrl = safeSourceUrl(cit.canonical_url)
            return (
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
              {sourceUrl && (
                <a
                  href={sourceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex text-xs text-[var(--color-link)] hover:underline"
                >
                  Open source
                </a>
              )}
            </div>
            )
          })
        )}
      </div>
    </InlineFormSection>
  )
}
