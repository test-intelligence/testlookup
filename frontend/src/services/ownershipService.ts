import { api } from './api';

// ── Types ───────────────────────────────────────────────────────────────────

export interface OwnershipRule {
  id: string;
  project_id: string;
  match_type: 'suite_name' | 'component' | 'package' | 'path' | 'label';
  match_pattern: string;
  service_name: string;
  team_name: string;
  team_contact: string | null;
  priority: number;
  is_active: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface OwnershipResolution {
  service_name: string | null;
  team_name: string | null;
  team_contact: string | null;
  confidence: 'high' | 'medium' | 'low' | 'none';
  matched_rule_id: string | null;
  match_source: string | null;
  fallback_reason: string | null;
}

// US-7.3: team → notification channel mapping (ownership-routed notifications)
export interface TeamChannel {
  id: string;
  project_id: string;
  team_name: string;
  channel_type: 'email' | 'slack' | 'teams';
  target: string;
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
}

export const MATCH_TYPES = [
  { value: 'suite_name', label: 'Suite Name' },
  { value: 'component', label: 'Component / Class' },
  { value: 'package', label: 'Package' },
  { value: 'path', label: 'Full Path' },
  { value: 'label', label: 'Feature / Epic Label' },
] as const;

// ── API ─────────────────────────────────────────────────────────────────────

export async function listOwnershipRules(projectId: string): Promise<OwnershipRule[]> {
  const { data } = await api.get<OwnershipRule[]>(`/api/v1/projects/${projectId}/ownership/rules`);
  return data;
}

export async function createOwnershipRule(projectId: string, payload: {
  match_type: string;
  match_pattern: string;
  service_name: string;
  team_name: string;
  team_contact?: string;
  priority?: number;
}): Promise<OwnershipRule> {
  const { data } = await api.post<OwnershipRule>(`/api/v1/projects/${projectId}/ownership/rules`, payload);
  return data;
}

export async function updateOwnershipRule(projectId: string, ruleId: string, payload: {
  match_type?: string;
  match_pattern?: string;
  service_name?: string;
  team_name?: string;
  team_contact?: string;
  priority?: number;
  is_active?: boolean;
}): Promise<OwnershipRule> {
  const { data } = await api.patch<OwnershipRule>(`/api/v1/projects/${projectId}/ownership/rules/${ruleId}`, payload);
  return data;
}

export async function deleteOwnershipRule(projectId: string, ruleId: string): Promise<void> {
  await api.delete(`/api/v1/projects/${projectId}/ownership/rules/${ruleId}`);
}

export async function bulkImportRules(projectId: string, rules: Array<{
  match_type: string;
  match_pattern: string;
  service_name: string;
  team_name: string;
  team_contact?: string;
  priority?: number;
}>, replaceExisting: boolean = false): Promise<OwnershipRule[]> {
  const { data } = await api.post<OwnershipRule[]>(`/api/v1/projects/${projectId}/ownership/rules/bulk-import`, {
    rules,
    replace_existing: replaceExisting,
  });
  return data;
}

export async function exportOwnershipRules(projectId: string): Promise<OwnershipRule[]> {
  const { data } = await api.get<OwnershipRule[]>(`/api/v1/projects/${projectId}/ownership/rules/export`);
  return data;
}

export async function resolveClusterOwnership(projectId: string, clusterId: string): Promise<OwnershipResolution> {
  const { data } = await api.get<OwnershipResolution>(`/api/v1/projects/${projectId}/ownership/resolve/${clusterId}`);
  return data;
}

// ── Team notification channels (US-7.3) ─────────────────────────────────────

export async function listTeamChannels(projectId: string): Promise<TeamChannel[]> {
  const { data } = await api.get<TeamChannel[]>(`/api/v1/projects/${projectId}/ownership/team-channels`);
  return data;
}

export async function upsertTeamChannel(projectId: string, teamName: string, payload: {
  channel_type: 'email' | 'slack' | 'teams';
  target: string;
  is_active?: boolean;
}): Promise<TeamChannel> {
  const { data } = await api.put<TeamChannel>(
    `/api/v1/projects/${projectId}/ownership/team-channels/${encodeURIComponent(teamName)}`,
    payload,
  );
  return data;
}

export async function deleteTeamChannel(projectId: string, teamName: string): Promise<void> {
  await api.delete(`/api/v1/projects/${projectId}/ownership/team-channels/${encodeURIComponent(teamName)}`);
}
