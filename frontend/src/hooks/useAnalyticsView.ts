/**
 * useAnalyticsView — manages visualization instances and persistence for analytics pages.
 *
 * EV-1/EV-2: Supports both legacy widget-ID arrays and new VisualizationInstance arrays.
 * Loads from: server saved view → localStorage (Trends compat) → defaults.
 */
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import useSWR from 'swr'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import {
  getDefaultInstances,
  migrateWidgetIds,
  createInstance,
  MAX_INSTANCES_PER_PAGE,
} from '@/components/analytics/widgetRegistry'
import type { VisualizationInstance } from '@/components/analytics/widgetRegistry'

const TRENDS_STORAGE_KEY = 'testlookup_trend_charts'

interface SavedViewData {
  id: string
  filters: {
    instances?: VisualizationInstance[]
    widgets?: string[]
    page?: string
    version?: number
    [key: string]: unknown
  }
}

export interface AnalyticsViewResult {
  /** Full instance list — use this for rendering. */
  instances: VisualizationInstance[]
  /** Derived widget IDs — backward compat for pages that use ID-based conditionals. */
  widgetIds: string[]
  loading: boolean
  /** Add a new instance from a template. */
  addInstance: (templateId: string, config?: Partial<Omit<VisualizationInstance, 'instanceId' | 'templateId'>>) => void
  /** Update an existing instance's config. */
  updateInstance: (instanceId: string, patch: Partial<Omit<VisualizationInstance, 'instanceId' | 'templateId'>>) => void
  /** Remove an instance by its instanceId. */
  removeInstance: (instanceId: string) => void
  /** Duplicate an instance with a new UUID. */
  duplicateInstance: (instanceId: string) => void
  /** Replace all instances (used by WidgetPicker). */
  setWidgets: (ids: string[]) => void
  /** Reset to page defaults. */
  resetToDefaults: () => void
  /** Persist current state to server. */
  save: () => Promise<void>
  dirty: boolean
  // Legacy aliases
  addWidget: (id: string) => void
  removeWidget: (id: string) => void
  reorderWidgets: (ids: string[]) => void
}

export function useAnalyticsView(page: string): AnalyticsViewResult {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId

  const defaults = getDefaultInstances(page)
  const [instances, setInstances] = useState<VisualizationInstance[]>(defaults)
  const [savedViewId, setSavedViewId] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const initializedRef = useRef(false)

  // Derived: widget IDs for backward compat
  const widgetIds = useMemo(() => instances.map(i => i.templateId), [instances])

  // Fetch saved views
  const { data: savedViews, isLoading } = useSWR<SavedViewData[]>(
    projectId !== undefined ? ['saved-views', projectId, page] : null,
    async () => {
      const { getData } = await import('@/services/http')
      return getData<SavedViewData[]>('/api/v1/saved-views', {
        params: { ...(projectId ? { project_id: projectId } : {}) },
      })
    },
    { revalidateOnFocus: false },
  )

  // Initialize from saved view or fallback
  useEffect(() => {
    if (isLoading || initializedRef.current) return

    const pageView = savedViews?.find(v => v.filters?.page === page)
    if (pageView) {
      setSavedViewId(pageView.id)
      // v2: instance-based
      if (pageView.filters.instances && Array.isArray(pageView.filters.instances)) {
        setInstances(pageView.filters.instances)
        initializedRef.current = true
        return
      }
      // v1: widget-ID-based → migrate
      if (pageView.filters.widgets && Array.isArray(pageView.filters.widgets)) {
        setInstances(migrateWidgetIds(pageView.filters.widgets))
        initializedRef.current = true
        return
      }
    }

    // Trends localStorage fallback
    if (page === 'trends') {
      try {
        const stored = localStorage.getItem(TRENDS_STORAGE_KEY)
        if (stored) {
          const ids = JSON.parse(stored) as string[]
          if (Array.isArray(ids) && ids.length > 0) {
            setInstances(migrateWidgetIds(ids))
            initializedRef.current = true
            return
          }
        }
      } catch { /* ignore */ }
    }

    setInstances(defaults)
    initializedRef.current = true
  }, [savedViews, isLoading, page, defaults])

  const addInstance = useCallback((
    templateId: string,
    config?: Partial<Omit<VisualizationInstance, 'instanceId' | 'templateId'>>,
  ) => {
    setInstances(prev => {
      if (prev.length >= MAX_INSTANCES_PER_PAGE) return prev
      return [...prev, createInstance(templateId, config)]
    })
    setDirty(true)
  }, [])

  const updateInstance = useCallback((instanceId: string, patch: Partial<Omit<VisualizationInstance, 'instanceId' | 'templateId'>>) => {
    setInstances(prev => prev.map(i =>
      i.instanceId === instanceId ? { ...i, ...patch } : i,
    ))
    setDirty(true)
  }, [])

  const removeInstance = useCallback((instanceId: string) => {
    setInstances(prev => prev.filter(i => i.instanceId !== instanceId))
    setDirty(true)
  }, [])

  const duplicateInstance = useCallback((instanceId: string) => {
    setInstances(prev => {
      if (prev.length >= MAX_INSTANCES_PER_PAGE) return prev
      const source = prev.find(i => i.instanceId === instanceId)
      if (!source) return prev
      const clone = createInstance(source.templateId, {
        title: source.title ? `${source.title} (copy)` : undefined,
        chartType: source.chartType,
        metricVariant: source.metricVariant,
        filters: source.filters ? { ...source.filters } : undefined,
      })
      const idx = prev.indexOf(source)
      const next = [...prev]
      next.splice(idx + 1, 0, clone)
      return next
    })
    setDirty(true)
  }, [])

  // Legacy: set from widget IDs (used by WidgetPicker)
  const setWidgets = useCallback((ids: string[]) => {
    setInstances(migrateWidgetIds(ids))
    setDirty(true)
  }, [])

  const resetToDefaults = useCallback(() => {
    setInstances(getDefaultInstances(page))
    setDirty(true)
  }, [page])

  const save = useCallback(async () => {
    const { postData, patchData } = await import('@/services/http')
    const payload = {
      name: `${page} view`,
      page,
      filters: {
        page,
        instances,
        widgets: instances.map(i => i.templateId), // backward compat
        version: 2,
      },
      ...(projectId ? { project_id: projectId } : {}),
    }
    if (savedViewId) {
      await patchData(`/api/v1/saved-views/${savedViewId}`, payload)
    } else {
      const created = await postData<SavedViewData>('/api/v1/saved-views', payload)
      setSavedViewId(created.id)
    }
    setDirty(false)
    if (page === 'trends') {
      try { localStorage.removeItem(TRENDS_STORAGE_KEY) } catch { /* ignore */ }
    }
  }, [page, instances, savedViewId, projectId])

  // Legacy aliases
  const addWidget = useCallback((id: string) => addInstance(id), [addInstance])
  const removeWidget = useCallback((id: string) => {
    // Remove first instance matching this template
    setInstances(prev => {
      const idx = prev.findIndex(i => i.templateId === id)
      if (idx === -1) return prev
      return [...prev.slice(0, idx), ...prev.slice(idx + 1)]
    })
    setDirty(true)
  }, [])
  const reorderWidgets = useCallback((ids: string[]) => setWidgets(ids), [setWidgets])

  return {
    instances,
    widgetIds,
    loading: isLoading,
    addInstance,
    updateInstance,
    removeInstance,
    duplicateInstance,
    setWidgets,
    resetToDefaults,
    save,
    dirty,
    addWidget,
    removeWidget,
    reorderWidgets,
  }
}
