import { api } from './api';

export interface AuditEvent {
  source: string;
  action: string;
  actor_name: string | null;
  actor_id: string | null;
  project_id: string | null;
  detail: Record<string, unknown> | null;
  created_at: string | null;
  success?: boolean;
}

export interface AuditCategory {
  key: string;
  description: string;
}

export interface TenantObservability {
  project_id: string;
  period_days: number;
  total_runs: number;
  total_tests: number;
  avg_pass_rate: number | null;
  failed_runs: number;
  ai_analyses_count: number;
  release_decisions_count: number;
  audit_events_count: number;
}

export async function listAuditEvents(params?: {
  project_id?: string;
  category?: string;
  actor_id?: string;
  days?: number;
  page?: number;
  page_size?: number;
}): Promise<{ total: number; items: AuditEvent[] }> {
  const { data } = await api.get('/api/v1/audit-dashboard/events', { params });
  return data;
}

export async function listCategories(): Promise<AuditCategory[]> {
  const { data } = await api.get<AuditCategory[]>('/api/v1/audit-dashboard/categories');
  return data;
}

export async function getProjectObservability(projectId: string, days?: number): Promise<TenantObservability> {
  const { data } = await api.get<TenantObservability>(`/api/v1/audit-dashboard/observability/${projectId}`, {
    params: days ? { days } : undefined,
  });
  return data;
}

export async function exportAuditCSV(params?: {
  project_id?: string;
  category?: string;
  days?: number;
}): Promise<void> {
  const response = await api.get('/api/v1/audit-dashboard/export', {
    params,
    responseType: 'blob',
  });
  const url = URL.createObjectURL(response.data as Blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `audit-export.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
