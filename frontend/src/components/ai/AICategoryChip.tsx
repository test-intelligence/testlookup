/**
 * W03 AIH-05: the AI category chip and confidence used by the test History AI
 * strip's popover and the canonical page's "AI category" column. The category is
 * shown as the API names it ("uncategorized" for null); a missing confidence
 * reads "confidence not reported", never 0. Rendered as React text only.
 */
import { categoryColor, confidenceColor } from '@/utils/formatters'
import { categoryText, confidenceText } from './aiTestHistory.model'

export function AICategoryChip({ category }: { category: string | null }) {
  return (
    <span
      data-ai-category={category ?? ''}
      className={`inline-flex items-center rounded border border-[var(--color-border)] bg-[var(--color-bg-secondary)] px-1.5 py-0.5 font-mono text-[10px] ${categoryColor(category ?? '')}`}
    >
      {categoryText(category)}
    </span>
  )
}

export function AIConfidence({ confidence }: { confidence: number | null }) {
  return confidence == null ? (
    <span data-ai-confidence="" className="text-[var(--color-text-muted)]">
      {confidenceText(confidence)}
    </span>
  ) : (
    <span data-ai-confidence={Math.round(confidence)} className={confidenceColor(confidence)}>
      {confidenceText(confidence)}
    </span>
  )
}
