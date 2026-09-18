/**
 * useAnalyticsView — manages visualization instances and persistence for analytics pages.
 *
 * Loads from: server saved view → page defaults. Saves instance-based views (v2 format).
 */
import { useState, useCallback, useMemo } from 'react'
import useSWR from 'swr'
import { useProjectStore, ALL_PROJECTS_ID } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'
import {
  getDefaultInstances,
  createInstance,
  MAX_INSTANCES_PER_PAGE,
  normalizeInstances,
} from '@/components/analytics/widgetRegistry'
import type { VisualizationInstance } from '@/components/analytics/widgetRegistry'

interface SavedViewData {
  id: string
  user_id?: string
  project_id?: string | null
  page?: string | null
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
  error: unknown
  retry: () => Promise<unknown>
  /** Add a new instance from a template. */
  addInstance: (templateId: string, config?: Partial<Omit<VisualizationInstance, 'instanceId' | 'templateId'>>) => void
  /** Update an existing instance's config. */
  updateInstance: (instanceId: string, patch: Partial<Omit<VisualizationInstance, 'instanceId' | 'templateId'>>) => void
  /** Remove an instance by its instanceId. */
  removeInstance: (instanceId: string) => void
  /** Duplicate an instance with a new UUID. */
  duplicateInstance: (instanceId: string) => void
  /** Replace all instances with a new template-ID selection (used by WidgetPicker). */
  setWidgets: (ids: string[]) => Promise<void>
  /** Reset to page defaults. */
  resetToDefaults: () => void
  /** Persist current state to server. */
  save: () => Promise<void>
  dirty: boolean
}

export function useAnalyticsView(page: string): AnalyticsViewResult {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const currentUserId = useAuthStore(s => s.user?.id ?? null)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId

  const defaults = useMemo(() => getDefaultInstances(page), [page])
  const [instances, setInstances] = useState<VisualizationInstance[]>(defaults)
  const [savedViewId, setSavedViewId] = useState<string | null>(null)
  const [stateScope, setStateScope] = useState<string | null>(null)
  const [hydratedScope, setHydratedScope] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const scopeKey = `${projectId ?? 'all'}:${page}`

  const widgetIds = useMemo(() => instances.map(i => i.templateId), [instances])

  const { data: savedViews, error, isLoading, mutate } = useSWR<SavedViewData[]>(
    projectId !== undefined ? ['saved-views', projectId, page] : null,
    async () => {
      const { getData } = await import('@/services/http')
      return getData<SavedViewData[]>('/api/v1/saved-views', {
        params: { ...(projectId ? { project_id: projectId } : {}), page },
      })
    },
    { revalidateOnFocus: false },
  )

  // A project/page change is a new persistence scope. Reset synchronously so a
  // click during the next paint cannot PATCH the previous project's view.
  if (stateScope !== scopeKey) {
    setStateScope(scopeKey)
    setHydratedScope(null)
    setSavedViewId(null)
    setInstances(defaults)
    setDirty(false)
  } else if (!isLoading && !error && hydratedScope !== scopeKey) {
    const scopedViews = (savedViews ?? []).filter(view => (
      (view.page === page || view.filters?.page === page)
      && (projectId ? view.project_id === projectId : view.project_id == null)
    ))
    const ownedView = currentUserId
      ? scopedViews.find(view => view.user_id === currentUserId)
      : undefined
    const layoutView = ownedView ?? scopedViews[0]
    const stored = layoutView?.filters.instances ?? layoutView?.filters.widgets
    // Shared views may seed the layout, but never become a PATCH target.
    setSavedViewId(ownedView?.id ?? null)
    setInstances(layoutView ? normalizeInstances(page, stored) : defaults)
    setDirty(false)
    setHydratedScope(scopeKey)
  }

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

  const resetToDefaults = useCallback(() => {
    setInstances(getDefaultInstances(page))
    setDirty(true)
  }, [page])

  const persist = useCallback(async (nextInstances: VisualizationInstance[]) => {
    const { postData, patchData } = await import('@/services/http')
    const payload = {
      name: `${page} view`,
      page,
      filters: {
        page,
        instances: nextInstances,
        version: 2,
      },
      ...(projectId ? { project_id: projectId } : {}),
    }
    const scopedSavedViewId = hydratedScope === scopeKey ? savedViewId : null
    if (scopedSavedViewId) {
      await patchData(`/api/v1/saved-views/${scopedSavedViewId}`, payload)
    } else {
      const created = await postData<SavedViewData>('/api/v1/saved-views', payload)
      setSavedViewId(created.id)
    }
  }, [hydratedScope, page, projectId, savedViewId, scopeKey])

  const save = useCallback(async () => {
    if (hydratedScope !== scopeKey) return
    await persist(instances)
    setDirty(false)
  }, [hydratedScope, instances, persist, scopeKey])

  const setWidgets = useCallback(async (ids: string[]) => {
    if (hydratedScope !== scopeKey) return
    const next = normalizeInstances(page, ids)
    setInstances(next)
    setDirty(true)
    await persist(next)
    setDirty(false)
  }, [hydratedScope, page, persist, scopeKey])

  return {
    instances,
    widgetIds,
    loading: isLoading,
    error,
    retry: mutate,
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
