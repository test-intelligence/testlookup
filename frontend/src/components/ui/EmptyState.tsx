import { ReactNode } from 'react'

interface Props {
  icon?: ReactNode
  title: string
  description?: string
  action?: ReactNode
  /**
   * W29a UIX-11. `neutral` (default): a resting state ("no runs in this
   * window"). `warning`: the same layout with the icon tile on the warning
   * tint — a problem, not a resting state (DataUnavailable); the title stays
   * neutral text, never an orange headline.
   */
  tone?: 'neutral' | 'warning'
  /** One muted line holding the title, then `action` inline (a panel's small empty state). */
  compact?: boolean
  testId?: string
  role?: 'alert' | 'status'
}

/** The empty / unavailable look (one look for every page, W29a UIX-11). */
export default function EmptyState({ icon, title, description, action, tone = 'neutral', compact = false, testId, role }: Props) {
  if (compact) {
    return (
      <p role={role} data-testid={testId} className="py-3 text-sm text-[var(--color-text-muted)]">
        {title}
        {action ? <span className="ml-2 inline-flex items-center align-middle">{action}</span> : null}
      </p>
    )
  }
  return (
    <div role={role} data-testid={testId} className="flex flex-col items-center justify-center py-12 text-center">
      {icon && tone === 'warning' && (
        <div className="p-4 rounded-2xl mb-4" style={{ background: 'var(--ui-warning-bg)', color: 'var(--ui-warning)' }}>
          {icon}
        </div>
      )}
      {icon && tone !== 'warning' && (
        <div className="p-4 bg-[var(--color-bg-secondary)] rounded-2xl mb-4 text-[var(--color-text-muted)]">{icon}</div>
      )}
      <h3 className="text-lg font-semibold text-[var(--color-text-secondary)] mb-1">{title}</h3>
      {description && <p className="text-sm text-[var(--color-text-muted)] max-w-sm mb-4">{description}</p>}
      {action}
    </div>
  )
}
