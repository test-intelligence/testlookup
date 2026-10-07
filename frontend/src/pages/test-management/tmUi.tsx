import type { ReactNode } from 'react'
import { clsx } from 'clsx'

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

interface ModalWrapProps { onClose: () => void; title: string; children: ReactNode; width?: string }

/** The page's modal shell (create case / plan, AI generate, strategy). */
export function ModalWrap({ onClose, title, children, width = 'max-w-2xl' }: ModalWrapProps) {
  return (
    <div role="dialog" aria-modal="true" aria-labelledby="tm-generic-modal-title" className="fixed inset-0 bg-[var(--color-bg)]/60 z-50 flex items-center justify-center p-4">
      <div className={clsx('bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl w-full shadow-2xl flex flex-col max-h-[90vh]', width)}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--color-border)] flex-shrink-0">
          <h2 id="tm-generic-modal-title" className="text-base font-semibold text-[var(--color-text)]">{title}</h2>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors text-xl leading-none">&times;</button>
        </div>
        <div className="overflow-y-auto flex-1 px-6 py-4">
          {children}
        </div>
      </div>
    </div>
  )
}
