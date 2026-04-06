/**
 * AnalyticsGrid — renders visualization instances from the registry.
 *
 * EV-1: Accepts VisualizationInstance[] with fallback to legacy string[] widgetIds.
 * Separates metric widgets (responsive row) from chart widgets (2-col grid).
 */

import AnalyticsWidget from './AnalyticsWidget'
import { getWidgetDef, getInstanceChartType } from './widgetRegistry'
import type { VisualizationInstance } from './widgetRegistry'

interface Props {
  /** New: instance-based rendering. */
  instances?: VisualizationInstance[]
  /** Legacy: widget ID list (auto-wrapped as instances). */
  widgetIds?: string[]
  renderWidget: (instance: VisualizationInstance) => React.ReactNode | null
  onRemove?: (instanceId: string) => void
  onEdit?: (instanceId: string) => void
  onDuplicate?: (instanceId: string) => void
  loading?: boolean
}

export default function AnalyticsGrid({ instances, widgetIds, renderWidget, onRemove, onEdit, onDuplicate, loading }: Props) {
  // Normalize: use instances if provided, else wrap widgetIds
  const items: VisualizationInstance[] = instances ?? (widgetIds ?? []).map(id => ({
    instanceId: id,
    templateId: id,
  }))

  const metricItems = items.filter(i => getInstanceChartType(i) === 'metric')
  const chartItems = items.filter(i => {
    const ct = getInstanceChartType(i)
    return ct && ct !== 'metric'
  })

  return (
    <div className="space-y-4">
      {metricItems.length > 0 && (
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
          {metricItems.map(instance => {
            const def = getWidgetDef(instance.templateId)
            if (!def) return null
            const content = renderWidget(instance)
            return (
              <AnalyticsWidget
                key={instance.instanceId}
                id={instance.templateId}
                label={def.label}
                instance={instance}
                onRemove={onRemove}
                onEdit={onEdit}
                onDuplicate={onDuplicate}
                loading={loading}
                empty={!content && !loading}
              >
                {content}
              </AnalyticsWidget>
            )
          })}
        </div>
      )}

      {chartItems.length > 0 && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          {chartItems.map(instance => {
            const def = getWidgetDef(instance.templateId)
            if (!def) return null
            const content = renderWidget(instance)
            return (
              <AnalyticsWidget
                key={instance.instanceId}
                id={instance.templateId}
                label={def.label}
                instance={instance}
                onRemove={onRemove}
                onEdit={onEdit}
                onDuplicate={onDuplicate}
                loading={loading}
                empty={!content && !loading}
              >
                {content}
              </AnalyticsWidget>
            )
          })}
        </div>
      )}
    </div>
  )
}
