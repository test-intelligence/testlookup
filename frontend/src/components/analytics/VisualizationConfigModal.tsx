/**
 * VisualizationConfigModal — edit a single visualization instance.
 *
 * EV-5: Allows users to set custom title, override chart type,
 * and configure instance-level filters.
 */
import { useState } from 'react'
import { Settings2, X, Check } from 'lucide-react'
import { getWidgetDef } from './widgetRegistry'
import type { VisualizationInstance, ChartType } from './widgetRegistry'

interface Props {
  instance: VisualizationInstance
  onSave: (instanceId: string, patch: Partial<Omit<VisualizationInstance, 'instanceId' | 'templateId'>>) => void
  onClose: () => void
}

const CHART_TYPE_LABELS: Record<string, string> = {
  line: 'Line Chart',
  bar: 'Bar Chart',
  area: 'Area Chart',
  pie: 'Pie Chart',
  gauge: 'Gauge',
  metric: 'KPI Metric',
  table: 'Table',
  stacked_bar: 'Stacked Bar',
  donut: 'Donut Chart',
}

export default function VisualizationConfigModal({ instance, onSave, onClose }: Props) {
  const template = getWidgetDef(instance.templateId)
  const allowedTypes = template?.allowedChartTypes ?? (template ? [template.chartType] : [])

  const [title, setTitle] = useState(instance.title ?? '')
  const [chartType, setChartType] = useState<ChartType | ''>(instance.chartType ?? '')

  const handleSave = () => {
    onSave(instance.instanceId, {
      title: title.trim() || undefined,
      chartType: chartType || undefined,
    })
    onClose()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/70" />
      <div
        className="relative bg-[var(--color-bg-card)] border border-[var(--color-border)] rounded-xl shadow-2xl w-full max-w-md overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[var(--color-border)] bg-[var(--color-bg)]">
          <div className="flex items-center gap-2">
            <Settings2 className="h-4 w-4 text-[var(--color-text-secondary)]" />
            <h2 className="text-sm font-semibold text-[var(--color-text)]">Configure Visualization</h2>
          </div>
          <button onClick={onClose} className="text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)] p-1 rounded hover:bg-[var(--color-bg-card)]/50">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Form */}
        <div className="px-5 py-4 space-y-4">
          {/* Template info */}
          <div className="text-xs text-[var(--color-text-muted)]">
            Template: <span className="text-[var(--color-text-secondary)]">{template?.label ?? instance.templateId}</span>
          </div>

          {/* Custom title */}
          <div>
            <label className="text-xs font-medium text-[var(--color-text-secondary)] block mb-1">Custom Title</label>
            <input
              type="text"
              value={title}
              onChange={e => setTitle(e.target.value)}
              placeholder={template?.label ?? 'Untitled'}
              className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] rounded-lg px-3 py-2 text-sm placeholder:text-[var(--color-text-muted)] focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)]"
            />
            <p className="text-[10px] text-[var(--color-text-muted)] mt-1">Leave blank to use the default template name</p>
          </div>

          {/* Chart type override */}
          {allowedTypes.length > 1 && (
            <div>
              <label className="text-xs font-medium text-[var(--color-text-secondary)] block mb-1">Chart Type</label>
              <select
                value={chartType}
                onChange={e => setChartType(e.target.value as ChartType)}
                className="w-full bg-[var(--color-bg-card)] border border-[var(--color-border)] text-[var(--color-text)] rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-[var(--color-accent)]"
              >
                <option value="">Default ({CHART_TYPE_LABELS[template?.chartType ?? ''] ?? 'auto'})</option>
                {allowedTypes.map(ct => (
                  <option key={ct} value={ct}>{CHART_TYPE_LABELS[ct] ?? ct}</option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-3 border-t border-[var(--color-border)] bg-[var(--color-bg)]">
          <button onClick={onClose} className="px-3 py-1.5 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text)]">
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium bg-[var(--color-accent-muted)] text-white rounded-lg hover:bg-[var(--color-accent-muted)] transition-colors"
          >
            <Check className="h-3 w-3" />
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
