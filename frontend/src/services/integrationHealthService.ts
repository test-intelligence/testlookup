import { api } from './api';

/** The row kinds `GET /status?scope=all` answers (W10 INT-04). A missing `kind` reads `global`. */
export type IntegrationHealthRowKind = 'global' | 'project' | 'webhook';

export interface IntegrationStatus {
  provider: string;
  status: string;
  last_checked_at: string | null;
  message: string | null;
  response_ms: number | null;
  consecutive_failures: number;
  last_success_at: string | null;
  // W10 INT-04: every field below is optional so an older server, and every
  // fixture written before W10, still reads as a deployment-wide row.
  /** '' = deployment-wide; the project id (github / gitlab rows); the subscription id (webhook rows). */
  scope_key?: string;
  kind?: IntegrationHealthRowKind | string;
  project_id?: string | null;
  project_name?: string | null;
  /** The project's name, or the webhook's name. */
  label?: string | null;
  /** Host + path of the probed base, never a credential. */
  target?: string | null;
  /** False: no probe has run for this row yet ("Not probed yet"). A missing value reads true. */
  probed?: boolean;
  /** Webhook rows only. */
  breaker_threshold?: number | null;
}

export interface ProbeResult {
  provider: string;
  status: string;
  response_ms: number;
  message: string;
  auth_valid: boolean | null;
  payload_valid: boolean | null;
  /** W10 INT-04 (optional: an older server sends neither). */
  scope_key?: string;
  project_id?: string | null;
}

export interface ProbeHistoryEntry {
  id: string;
  status: string;
  response_ms: number | null;
  message: string | null;
  auth_valid: boolean | null;
  payload_valid: boolean | null;
  checked_at: string | null;
}

export interface HealthTrend {
  provider: string;
  total_probes: number;
  healthy: number;
  degraded: number;
  down: number;
  /** Probe timed out. Counted in `total_probes` but had no column until #557. */
  timeout: number;
  /** Credentials rejected — the most actionable failure this page can show. */
  auth_error: number;
  avg_response_ms: number;
  uptime_pct: number;
}

/**
 * `GET /integration-health/status`. Asks `?scope=all` (the deployment-wide rows,
 * every project's GitHub / GitLab row and the paused webhooks, W10 INT-04) ONLY
 * when `scope === 'all'`. `unknown` on purpose: SWR hands a bare fetcher its key
 * array, which must read as today's deployment-wide answer.
 */
export async function getAllStatus(scope?: unknown): Promise<IntegrationStatus[]> {
  const { data } = await api.get<IntegrationStatus[]>(
    '/api/v1/integration-health/status',
    scope === 'all' ? { params: { scope: 'all' } } : undefined,
  );
  return data;
}

/** `projectId` probes that project's GitHub / GitLab integration (W10 INT-04). */
export async function triggerProbe(provider?: string, projectId?: string): Promise<ProbeResult[]> {
  const params: Record<string, string> = {};
  if (provider) params.provider = provider;
  if (projectId) params.project_id = projectId;
  const { data } = await api.post<ProbeResult[]>('/api/v1/integration-health/probe', null, {
    params: Object.keys(params).length ? params : undefined,
  });
  return data;
}

/** Without `projectId`: the deployment-wide history; with it, that project's row (W10 INT-04). */
export async function getProviderHistory(provider: string, days?: number, projectId?: string): Promise<ProbeHistoryEntry[]> {
  const params: Record<string, string | number> = {};
  if (days) params.days = days;
  if (projectId) params.project_id = projectId;
  const { data } = await api.get<ProbeHistoryEntry[]>(
    `/api/v1/integration-health/history/${encodeURIComponent(provider)}`,
    { params: Object.keys(params).length ? params : undefined },
  );
  return data;
}

export async function getHealthTrends(days?: number): Promise<HealthTrend[]> {
  const { data } = await api.get<HealthTrend[]>('/api/v1/integration-health/trends', {
    params: days ? { days } : undefined,
  });
  return data;
}
