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
