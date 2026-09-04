import { api } from './api';

/** Whether THIS reader may apply the release stored in a view (S5-3f-ii).
 *
 * A view outlives the state it was saved in, and a shared view is read by
 * people who did not save it. The backend never refuses the view over a stale
 * release -- it drops the filter and says why, so the UI can tell the reader
 * their result set is wider than the view's author intended rather than
 * silently showing more rows.
 */
export interface SavedViewRelease {
  release_id: string | null;
  applied: boolean;
  reason: string | null;
}

export interface SavedView {
  id: string;
  user_id: string;
  project_id: string | null;
  name: string;
  description: string | null;
  filters: Record<string, unknown>;
  is_shared: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string | null;
  /** Absent on views carrying no release. */
  release?: SavedViewRelease | null;
}

/** The one key a release lives under inside `filters`.
 *
 * Mirrors `saved_view_release.RELEASE_KEY`. Spelled once on each side; a
 * literal repeated across writer and reader is how a saved view silently
 * stops carrying its release.
 */
export const SAVED_VIEW_RELEASE_KEY = 'release_id';

/** Put the active release into a view's filters, or take it out.
 *
 * Mirrors the backend's `store_release`: a falsy release REMOVES the key
 * rather than storing a null, so the two sides agree on what "no release"
 * looks like.
 */
export function withRelease(
  filters: Record<string, unknown>,
  releaseId: string | null | undefined,
): Record<string, unknown> {
  const next = { ...filters };
  if (releaseId) next[SAVED_VIEW_RELEASE_KEY] = releaseId;
  else delete next[SAVED_VIEW_RELEASE_KEY];
  return next;
}

export async function listSavedViews(projectId?: string): Promise<SavedView[]> {
  const { data } = await api.get<SavedView[]>('/api/v1/saved-views', {
    params: projectId ? { project_id: projectId } : undefined,
  });
  return data;
}

export async function createSavedView(payload: {
  project_id?: string | null;
  name: string;
  description?: string;
  filters: Record<string, unknown>;
  is_shared?: boolean;
  is_default?: boolean;
}): Promise<SavedView> {
  const { data } = await api.post<SavedView>('/api/v1/saved-views', payload);
  return data;
}

export async function updateSavedView(id: string, payload: {
  name?: string;
  description?: string;
  filters?: Record<string, unknown>;
  is_shared?: boolean;
  is_default?: boolean;
}): Promise<SavedView> {
  const { data } = await api.patch<SavedView>(`/api/v1/saved-views/${id}`, payload);
  return data;
}

export async function getSavedView(
  id: string,
  activeProjectId?: string | null,
): Promise<SavedView> {
  const { data } = await api.get<SavedView>(`/api/v1/saved-views/${id}`, {
    // "May I apply this view's release" is only answerable relative to the
    // project the reader is looking at now. Omitted, the backend reports the
    // release as not applied rather than guessing.
    params: activeProjectId ? { active_project_id: activeProjectId } : undefined,
  });
  return data;
}

export async function deleteSavedView(id: string): Promise<void> {
  await api.delete(`/api/v1/saved-views/${id}`);
}
