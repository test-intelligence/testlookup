/**
 * useAnalyticsView — manages visualization instances and persistence for analytics pages.
 *
 * Loads from: server saved view → page defaults. Saves instance-based views (v2 format).
 */
import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import useSWR from 'swr'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import {
  getDefaultInstances,
  createInstance,
  MAX_INSTANCES_PER_PAGE,
} from '@/components/analytics/widgetRegistry'
import type { VisualizationInstance } from '@/components/analytics/widgetRegistry'

interface SavedViewData {
  id: string
  filters: {
    instances?: VisualizationInstance[]
    page?: string
    version?: number
    [key: string]: unknown
  }
}

export interface AnalyticsViewResult {
  /** Full instance list — use this for rendering. */
  instances: VisualizationInstance[]
  /** Derived widget IDs — used by pages that render from ID-based conditionals. */
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
  /** Replace all instances with a new template-ID selection (used by WidgetPicker). */
  setWidgets: (ids: string[]) => void
  /** Reset to page defaults. */
  resetToDefaults: () => void
  /** Persist current state to server. */
  save: () => Promise<void>
  dirty: boolean
}

export function useAnalyticsView(page: string): AnalyticsViewResult {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId

  const defaults = getDefaultInstances(page)
  const [instances, setInstances] = useState<VisualizationInstance[]>(defaults)
  const [savedViewId, setSavedViewId] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const initializedRef = useRef(false)

  const widgetIds = useMemo(() => instances.map(i => i.templateId), [instances])

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

  useEffect(() => {
    if (isLoading || initializedRef.current) return

    const pageView = savedViews?.find(v => v.filters?.page === page)
    if (pageView?.filters.instances && Array.isArray(pageView.filters.instances)) {
      setSavedViewId(pageView.id)
      setInstances(pageView.filters.instances)
      initializedRef.current = true
      return
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

  const setWidgets = useCallback((ids: string[]) => {
    setInstances(ids.map(id => createInstance(id)))
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
  }, [page, instances, savedViewId, projectId])

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
  }
}
