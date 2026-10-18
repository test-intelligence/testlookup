import type { ReactNode } from 'react'
import { clsx } from 'clsx'
import { InlineFormSection } from '@/components/ui/inPage/InlineFormSection'
import type { InlineSlot } from '@/components/ui/inPage/useInlineSlot'

/** A status / priority pill, coloured from one of the `format.ts` maps. */
export function StatusPill({ status, map }: { status: string; map: Record<string, string> }) {
  const cls = map[status] ?? 'bg-[var(--color-bg-hover)] text-[var(--color-text-muted)] border border-[var(--color-border-light)]'
  return (
    <span className={clsx('inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium', cls)}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}

/** The AI quality score, or a dash when none was computed. */
export function QualityScore({ score }: { score?: number }) {
  if (score == null) return <span className="text-[var(--color-text-faint)] text-xs">—</span>
  const color = score >= 80 ? 'text-[var(--status-passed)]' : score >= 60 ? 'text-[var(--status-broken)]' : 'text-[var(--status-failed)]'
  return <span className={clsx('text-sm font-semibold tabular-nums', color)}>{score}</span>
}

interface ModalWrapProps {
  onClose: () => void
  title: string
  children: ReactNode
  /** Caps the BODY's width (`max-w-2xl` / `max-w-3xl` / `max-w-4xl`); the section itself spans its column. */
  width?: string
  /** The host's form slot: one open form per section, and the discard question before another replaces it. */
  slot?: InlineSlot
}

/**
 * The page's form frame (create case / plan, AI generate, strategy). It
 * renders IN THE PAGE (W27b PAN-05), where its action lives (the Cases tab
 * between its alerts and the cases row; the Plans and Strategy tabs under
 * their toolbar): an `InlineFormSection` named by its title, the heading id
 * `tm-generic-modal-title` kept, its X labelled `Close`, and Escape while
 * focus is inside closes it. No overlay and no frame width: `width` caps the
 * body.
 */
export function ModalWrap({ onClose, title, children, width = 'max-w-2xl', slot }: ModalWrapProps) {
  return (
    <InlineFormSection title={title} titleId="tm-generic-modal-title" onClose={onClose} slot={slot}>
      <div className={clsx('w-full', width)}>{children}</div>
    </InlineFormSection>
  )
}
