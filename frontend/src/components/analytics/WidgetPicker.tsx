/**
 * WidgetPicker — modal for selecting which widgets to display on a page.
 *
 * Shows all available widgets for the current page with checkboxes.
 * Users can enable/disable widgets and confirm changes.
 */
import { useState } from 'react'
import { LayoutGrid, X, Check, RotateCcw } from 'lucide-react'
import { getPageWidgets, getDefaultWidgetIds } from './widgetRegistry'
import type { WidgetDef } from './widgetRegistry'

interface Props {
  page: string
  enabledIds: string[]
  onSave: (ids: string[]) => void
  onClose: () => void
}

export default function WidgetPicker({ page, enabledIds, onSave, onClose }: Props) {
  const available = getPageWidgets(page)
  const [selected, setSelected] = useState<Set<string>>(new Set(enabledIds))

  const toggle = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleSave = () => {
    // Preserve order: keep existing order for enabled, append newly added at end
    const ordered = [
      ...enabledIds.filter(id => selected.has(id)),
      ...Array.from(selected).filter(id => !enabledIds.includes(id)),
    ]
    onSave(ordered)
    onClose()
  }

  const handleReset = () => {
    const defaults = getDefaultWidgetIds(page)
    onSave(defaults)
    onClose()
  }

  // Group by chart type for better organization
  const groups = groupByType(available)

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="widget-picker-title" className="fixed inset-0 z-50 flex items-center justify-center" onClick={onClose}>
      {/* Backdrop — solid dark overlay */}
      <div className="absolute inset-0 bg-black/70" />
      {/* Modal panel — solid opaque background */}
      <div
        className="relative bg-[#1e293b] border border-[#334155] rounded-xl shadow-2xl w-full max-w-lg max-h-[80vh] overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#334155] bg-[#0f172a]">
          <div className="flex items-center gap-2">
            <LayoutGrid className="h-4 w-4 text-[var(--color-text-secondary)]" />
            <h2 id="widget-picker-title" className="text-sm font-semibold text-[var(--color-text)]">Customize Widgets</h2>
          </div>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] transition-colors p-1 rounded hover:bg-[var(--color-bg-card)]/50">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Widget list */}
        <div className="px-5 py-3 overflow-y-auto max-h-[55vh] space-y-4">
          {Object.entries(groups).map(([type, widgets]) => (
            <div key={type}>
              <p className="text-[10px] font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-2">
                {type === 'metric' ? 'KPI Metrics' : type === 'line' || type === 'area' ? 'Trend Charts' : type === 'pie' || type === 'donut' ? 'Distribution Charts' : type === 'stacked_bar' || type === 'bar' ? 'Bar Charts' : 'Charts'}
              </p>
              <div className="space-y-1">
                {widgets.map(w => (
                  <label
                    key={w.id}
                    className="flex items-start gap-3 px-3 py-2.5 rounded-lg hover:bg-[var(--color-bg-card)]/50 cursor-pointer transition-colors"
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(w.id)}
                      onChange={() => toggle(w.id)}
                      className="mt-0.5 h-4 w-4 rounded border-[var(--color-border)] bg-[var(--color-bg-card)] text-[var(--color-accent)] focus:ring-[var(--color-accent)] focus:ring-offset-0 accent-blue-500"
                    />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-[var(--color-text)]">{w.label}</p>
                      <p className="text-xs text-[var(--color-text-secondary)]">{w.description}</p>
                    </div>
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-[#334155] bg-[#0f172a]">
          <button
            onClick={handleReset}
            className="flex items-center gap-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text)] transition-colors"
          >
            <RotateCcw className="h-3 w-3" />
            Reset to Defaults
          </button>
          <div className="flex items-center gap-2">
            <button onClick={onClose} className="px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text)] transition-colors">
              Cancel
            </button>
            <button
              onClick={handleSave}
              className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium bg-[var(--color-accent-muted)] text-white rounded-lg hover:bg-[var(--color-accent-muted)] transition-colors"
            >
              <Check className="h-3 w-3" />
              Apply ({selected.size} widgets)
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function groupByType(widgets: WidgetDef[]): Record<string, WidgetDef[]> {
  const groups: Record<string, WidgetDef[]> = {}
  for (const w of widgets) {
    const key = w.chartType
    ;(groups[key] ??= []).push(w)
  }
  return groups
}
