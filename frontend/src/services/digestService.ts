import { api } from './api';

export type DigestScheduleType =
  | 'DAILY'
  | 'WEEKLY'
  | 'WEEKLY_RETRO'
  | 'PER_RUN'
  | 'PER_RELEASE'
  | 'PER_SUITE';
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
  send_when_unchanged: boolean;
  /** US-7.5: attach the self-contained HTML analysis report to email digests. */
  report_attachment: boolean;
  last_delivered_at: string | null;
  next_delivery_at: string | null;
  delivery_count: number;
  created_at: string;
  updated_at: string | null;
  /**
   * NTF-04: the bound view's name, snapshotted on every bind (migration 0221).
   * `saved_view_id === null` with a name = the view was deleted.
   */
  saved_view_name?: string | null;
  /** NTF-03: the release's name (Per Release) or the suite (Per Suite), else null. */
  scope_label?: string | null;
  /**
   * NTF-03 / NTF-04: why the row cannot deliver as configured:
   * "unscoped" | "release_missing" | "saved_view_deleted" |
   * "saved_view_unavailable", or null. A plain string (the server's vocabulary
   * may grow): `components/digests/digestScopeModel.ts` reads it.
   */
  scope_problem?: string | null;
}

/** NTF-04: what a digest covers (`DigestScopeInfo`, schemas.py). */
export interface DigestScopeInfo {
  /** "project" | "saved_view" | "saved_view_deleted" | "saved_view_unavailable" (plain string). */
  source: string;
  view_id: string | null;
  view_name: string | null;
  release_ids: string[];
  release_names: string[];
  suites: string[];
  window_days: number | null;
  /** Ids in the view that are not (or no longer) releases of this project. */
  dropped_release_count: number;
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
  /** NTF-04: present when the digest was asked for with a saved view; read through `parseDigestScope`. */
  scope?: DigestScopeInfo | null;
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

function strOrNull(v: unknown): string | null {
  return typeof v === 'string' ? v : null;
}

function strings(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((s): s is string => typeof s === 'string') : [];
}

/**
 * The defensive guard over a digest's `scope` (NTF-04): a missing or
 * malformed answer is `null` and the preview renders without its scope block.
 * `source` must be a non-empty string; a list of the wrong type reads as
 * empty, a name of the wrong type as absent, a window that is not a positive
 * whole number as no window, a bad dropped count as 0.
 */
export function parseDigestScope(raw: unknown): DigestScopeInfo | null {
  if (!isRecord(raw)) return null;
  if (typeof raw.source !== 'string' || raw.source === '') return null;
  const win = raw.window_days;
  const dropped = raw.dropped_release_count;
  return {
    source: raw.source,
    view_id: strOrNull(raw.view_id),
    view_name: strOrNull(raw.view_name),
    release_ids: strings(raw.release_ids),
    release_names: strings(raw.release_names),
    suites: strings(raw.suites),
    window_days: typeof win === 'number' && Number.isInteger(win) && win > 0 ? win : null,
    dropped_release_count:
      typeof dropped === 'number' && Number.isInteger(dropped) && dropped > 0 ? dropped : 0,
  };
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
  report_attachment?: boolean;
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
  /** NTF-04: bind a saved report view, or `null` to clear it (the whole project). */
  saved_view_id?: string | null;
  trigger_filter?: TriggerFilter;
  is_active?: boolean;
  is_paused?: boolean;
  report_attachment?: boolean;
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

export async function previewDigest(projectId?: string, period?: string, savedViewId?: string): Promise<DigestContent> {
  const params: Record<string, string | undefined> = { project_id: projectId, period: period || 'weekly' };
  // Only when a view is chosen: an unfiltered preview asks exactly what it did before NTF-04.
  if (savedViewId) params.saved_view_id = savedViewId;
  const { data } = await api.get<DigestContent>('/api/v1/digests/preview', { params });
  return data;
}
