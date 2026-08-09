/**
 * ModeTabs — Live · Debug · Audit · Compare segmented control.
 *
 * The handoff is explicit that only Debug is implemented for this iteration;
 * the other three tabs render but show holding-pattern placeholders when
 * selected. We expose ``onChange`` so the parent can swap content out.
 */
import { clsx } from 'clsx'
import { Activity, BookOpen, Bug, GitCompare } from 'lucide-react'

export type WorkflowMode = 'live' | 'debug' | 'audit' | 'compare'

const TABS: { id: WorkflowMode; label: string; icon: typeof Activity }[] = [
  { id: 'live',    label: 'Live',    icon: Activity },
  { id: 'debug',   label: 'Debug',   icon: Bug },
  { id: 'audit',   label: 'Audit',   icon: BookOpen },
  { id: 'compare', label: 'Compare', icon: GitCompare },
]

interface ModeTabsProps {
  mode: WorkflowMode
  onChange: (m: WorkflowMode) => void
  /** When true and the Live tab is selected, the Live icon gets a pulse. */
  liveActive?: boolean
}

export default function ModeTabs({ mode, onChange, liveActive = false }: ModeTabsProps) {
  return (
    <div
      className="flex items-center gap-4 px-3.5 border-b shrink-0"
      style={{ borderColor: 'var(--color-border)', height: 41 }}
      role="tablist"
    >
      {TABS.map(t => {
        const active = mode === t.id
        const Icon = t.icon
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={active}
            type="button"
            onClick={() => onChange(t.id)}
            className={clsx(
              'h-full inline-flex items-center gap-1.5 text-[13px] transition-colors relative px-0.5',
              active ? 'text-[var(--color-text)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]',
            )}
          >
            <Icon
              className={clsx(
                'h-3.5 w-3.5',
                t.id === 'live' && liveActive && 'motion-safe:animate-pulse text-[var(--status-passed)]',
              )}
            />
            <span>{t.label}</span>
            {t.id === 'live' && !active && liveActive && (
              <span className="ml-1 text-[9px] uppercase tracking-[0.08em] px-1 rounded-sm" style={{ background: 'color-mix(in srgb, var(--status-passed) 14%, transparent)', color: 'var(--status-passed)' }}>
                live
              </span>
            )}
            {active && (
              <span
                className="absolute left-0 right-0 bottom-0 h-0.5"
                style={{ background: 'var(--color-accent)' }}
              />
            )}
          </button>
        )
      })}
    </div>
  )
}
