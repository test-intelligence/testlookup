/**
 * AnalyticsWidget — shared shell that wraps every configurable chart widget.
 *
 * EV-1: Supports VisualizationInstance with user-configured titles and edit action.
 */
import { Pencil, Copy, X } from 'lucide-react'
import { SectionErrorBoundary } from '@/components/ui/SectionErrorBoundary'
import { getInstanceLabel } from '@/components/analytics/widgetRegistry'
import type { VisualizationInstance } from '@/components/analytics/widgetRegistry'

interface Props {
  /** Widget ID (legacy) or instance for display. */
  id: string
  label: string
  children: React.ReactNode
  instance?: VisualizationInstance
  onRemove?: (id: string) => void
  onEdit?: (instanceId: string) => void
  onDuplicate?: (instanceId: string) => void
  loading?: boolean
  empty?: boolean
  className?: string
}

export default function AnalyticsWidget({
  id, label, children, instance, onRemove, onEdit, onDuplicate, loading, empty, className,
}: Props) {
  const displayLabel = instance ? getInstanceLabel(instance) : label
  const removeId = instance?.instanceId ?? id

  return (
    <SectionErrorBoundary>
      <div className={`card p-0 overflow-hidden ${className ?? ''}`}>
        {/* Title bar */}
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--color-border)]/40">
          <h3 className="text-xs font-medium text-[var(--color-text-muted)] uppercase tracking-wider truncate">
            {displayLabel}
            {instance?.title && (
              <span className="ml-1 text-[var(--color-text-faint)] normal-case tracking-normal">(custom)</span>
            )}
          </h3>
          <div className="flex items-center gap-0.5 shrink-0">
            {onEdit && instance && (
              <button
                onClick={() => onEdit(instance.instanceId)}
                className="text-[var(--color-text-faint)] hover:text-blue-400 transition-colors p-1 rounded"
                title="Edit visualization"
                aria-label={`Edit ${displayLabel}`}
              >
                <Pencil className="h-3 w-3" />
              </button>
            )}
            {onDuplicate && instance && (
              <button
                onClick={() => onDuplicate(instance.instanceId)}
                className="text-[var(--color-text-faint)] hover:text-emerald-400 transition-colors p-1 rounded"
                title="Duplicate visualization"
                aria-label={`Duplicate ${displayLabel}`}
              >
                <Copy className="h-3 w-3" />
              </button>
            )}
            {onRemove && (
              <button
                onClick={() => onRemove(removeId)}
                className="text-[var(--color-text-faint)] hover:text-red-400 transition-colors p-1 rounded"
                title={`Remove ${displayLabel}`}
                aria-label={`Remove ${displayLabel} widget`}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        {/* Content */}
        <div className="px-4 py-3">
          {loading ? (
            <div className="flex items-center justify-center py-8">
              <div className="h-6 w-6 border-2 border-[var(--color-text-faint)] border-t-transparent rounded-full animate-spin" />
            </div>
          ) : empty ? (
            <div className="text-center py-8 text-xs text-[var(--color-text-muted)]">
              No data available for this widget
            </div>
          ) : (
            children
          )}
        </div>
      </div>
    </SectionErrorBoundary>
  )
}
