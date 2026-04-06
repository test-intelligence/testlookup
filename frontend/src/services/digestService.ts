import { api } from './api';

export type DigestScheduleType = 'DAILY' | 'WEEKLY' | 'PER_RUN' | 'PER_RELEASE' | 'PER_SUITE';
export type ScopeType = 'project' | 'release' | 'suite' | 'global';
export type TriggerFilter = 'all' | 'failed_only' | 'degraded_only';

export interface DigestSubscription {
  id: string;
  user_id: string;
  project_id: string | null;
  saved_view_id: string | null;
  name: string;
  schedule: DigestScheduleType;
  channel: 'email' | 'slack' | 'teams';
  is_active: boolean;
  is_paused: boolean;
  scope_type: ScopeType | null;
  scope_value: string | null;
  trigger_filter: TriggerFilter | null;
  last_delivered_at: string | null;
  next_delivery_at: string | null;
  delivery_count: number;
  created_at: string;
  updated_at: string | null;
}

export interface DigestContent {
  project_name: string | null;
  period: string;
  generated_at: string;
  total_runs: number;
  avg_pass_rate: number | null;
  pass_rate_trend: number | null;
  new_regressions: number;
  top_blockers: string[];
  top_clusters: Array<{ label: string; size: number; criticality: string }>;
  flaky_test_count: number;
  release_decisions: Array<{ run_id: string; recommendation: string; risk_score: number }>;
  action_items: string[];
}

export async function listSubscriptions(): Promise<DigestSubscription[]> {
  const { data } = await api.get<DigestSubscription[]>('/api/v1/digests/subscriptions');
  return data;
}

export async function createSubscription(payload: {
  project_id?: string | null;
  saved_view_id?: string | null;
  name: string;
  schedule?: DigestScheduleType;
  channel?: 'email' | 'slack' | 'teams';
  scope_type?: ScopeType;
  scope_value?: string | null;
  trigger_filter?: TriggerFilter;
}): Promise<DigestSubscription> {
  const { data } = await api.post<DigestSubscription>('/api/v1/digests/subscriptions', payload);
  return data;
}

export async function updateSubscription(id: string, payload: {
  name?: string;
  schedule?: DigestScheduleType;
  channel?: 'email' | 'slack' | 'teams';
  scope_type?: ScopeType;
  scope_value?: string | null;
  trigger_filter?: TriggerFilter;
  is_active?: boolean;
  is_paused?: boolean;
}): Promise<DigestSubscription> {
  const { data } = await api.patch<DigestSubscription>(`/api/v1/digests/subscriptions/${id}`, payload);
  return data;
}

export async function deleteSubscription(id: string): Promise<void> {
  await api.delete(`/api/v1/digests/subscriptions/${id}`);
}

export async function pauseSubscription(id: string): Promise<DigestSubscription> {
  const { data } = await api.post<DigestSubscription>(`/api/v1/digests/subscriptions/${id}/pause`);
  return data;
}

export async function resumeSubscription(id: string): Promise<DigestSubscription> {
  const { data } = await api.post<DigestSubscription>(`/api/v1/digests/subscriptions/${id}/resume`);
  return data;
}

export async function previewDigest(projectId?: string, period?: string): Promise<DigestContent> {
  const { data } = await api.get<DigestContent>('/api/v1/digests/preview', {
    params: { project_id: projectId, period: period || 'weekly' },
  });
  return data;
}
