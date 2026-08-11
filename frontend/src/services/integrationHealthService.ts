import { api } from './api';

export interface IntegrationStatus {
  provider: string;
  status: string;
  last_checked_at: string | null;
  message: string | null;
  response_ms: number | null;
  consecutive_failures: number;
  last_success_at: string | null;
}

export interface ProbeResult {
  provider: string;
  status: string;
  response_ms: number;
  message: string;
  auth_valid: boolean | null;
  payload_valid: boolean | null;
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

export async function getAllStatus(): Promise<IntegrationStatus[]> {
  const { data } = await api.get<IntegrationStatus[]>('/api/v1/integration-health/status');
  return data;
}

export async function triggerProbe(provider?: string): Promise<ProbeResult[]> {
  const { data } = await api.post<ProbeResult[]>('/api/v1/integration-health/probe', null, {
    params: provider ? { provider } : undefined,
  });
  return data;
}

export async function getProviderHistory(provider: string, days?: number): Promise<ProbeHistoryEntry[]> {
  const { data } = await api.get<ProbeHistoryEntry[]>(`/api/v1/integration-health/history/${provider}`, {
    params: days ? { days } : undefined,
  });
  return data;
}

export async function getHealthTrends(days?: number): Promise<HealthTrend[]> {
  const { data } = await api.get<HealthTrend[]>('/api/v1/integration-health/trends', {
    params: days ? { days } : undefined,
  });
  return data;
}
