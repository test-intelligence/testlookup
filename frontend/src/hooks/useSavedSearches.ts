import { useMemo } from 'react'
import useSWR from 'swr'
import { savedViewsKey } from '@/components/reports/savedViewsModel'
import { isSavedSearch } from '@/components/search/savedSearchesModel'
import { deleteData, patchData, postData } from '@/services/http'
import { listSavedViews, type SavedView } from '@/services/savedViewsService'

/**
 * W12 SRCH-03 — the reader's saved searches: `saved_views` rows with
 * `page: 'search'` (their own, and the ones shared in the pinned project), in
 * the server's order (default first, then by name). The key is the saved-views
 * key shape (`['saved-views', projectId, 'search']`).
 *
 * The writes below render their refusal in the page (the server's sentence in
 * the form), so they ask for no global toast; the routes are
 * `routers/saved_views.py`'s, unchanged (owner-only PATCH / DELETE, the
 * project check on every id).
 */
export function savedSearchesKey(projectId: string | null) {
  return savedViewsKey(projectId, 'search')
}

export function useSavedSearches(projectId: string | null) {
  const swr = useSWR<SavedView[]>(
    savedSearchesKey(projectId),
    () => listSavedViews(projectId ?? undefined, 'search'),
    { revalidateOnFocus: false },
  )
  const views = useMemo(() => (swr.data ?? []).filter(isSavedSearch), [swr.data])
  return { views, data: swr.data, error: swr.error, isLoading: swr.isLoading, mutate: swr.mutate }
}

export interface SavedSearchCreate {
  project_id: string | null
  name: string
  page: 'search'
  filters: Record<string, unknown>
  is_shared: boolean
}

export function createSavedSearch(payload: SavedSearchCreate): Promise<SavedView> {
  return postData<SavedView>('/api/v1/saved-views', payload, { suppressToast: true })
}

export function updateSavedSearch(id: string, payload: { name?: string; is_shared?: boolean }): Promise<SavedView> {
  return patchData<SavedView>(`/api/v1/saved-views/${encodeURIComponent(id)}`, payload, { suppressToast: true })
}

export function deleteSavedSearch(id: string): Promise<void> {
  return deleteData<void>(`/api/v1/saved-views/${encodeURIComponent(id)}`, { suppressToast: true })
}
