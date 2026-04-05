import { api } from './api';

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

export async function deleteSavedView(id: string): Promise<void> {
  await api.delete(`/api/v1/saved-views/${id}`);
}
