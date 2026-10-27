import { api } from './api';

// ── Types ───────────────────────────────────────────────────────────────────

export interface SSOConfig {
  id: string;
  display_name: string;
  provider_type: 'SAML' | 'OIDC';
  /** SAML fields — null on an OIDC configuration. */
  idp_entity_id: string | null;
  idp_sso_url: string | null;
  idp_slo_url: string | null;
  idp_certificate_fingerprint: string;
  sp_entity_id: string | null;
  sp_acs_url: string | null;
  audience: string | null;
  /** W20 ADM-08b: OIDC fields — null on a SAML configuration. The client secret is never returned. */
  oidc_issuer?: string | null;
  oidc_client_id?: string | null;
  oidc_scopes?: string | null;
  oidc_groups_claim?: string | null;
  oidc_client_secret_set?: boolean;
  oidc_redirect_uri?: string | null;
  role_mapping: Record<string, string> | null;
  default_role: string;
  group_attribute: string | null;
  enforcement_mode: 'OPTIONAL' | 'SSO_REQUIRED';
  is_active: boolean;
  last_test_at: string | null;
  last_test_success: boolean | null;
  last_test_error: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface SSOConfigCreate {
  display_name: string;
  provider_type?: 'SAML' | 'OIDC';
  idp_entity_id?: string;
  idp_sso_url?: string;
  idp_slo_url?: string;
  idp_certificate?: string;
  sp_entity_id?: string;
  sp_acs_url?: string;
  audience?: string;
  oidc_issuer?: string;
  oidc_client_id?: string;
  oidc_client_secret?: string;
  oidc_scopes?: string;
  oidc_groups_claim?: string;
  role_mapping?: Record<string, string>;
  default_role?: string;
  group_attribute?: string;
  enforcement_mode?: 'OPTIONAL' | 'SSO_REQUIRED';
}

export interface SSOConfigUpdate {
  display_name?: string;
  idp_entity_id?: string;
  idp_sso_url?: string;
  idp_slo_url?: string;
  idp_certificate?: string;
  sp_entity_id?: string;
  sp_acs_url?: string;
  audience?: string;
  oidc_issuer?: string;
  oidc_client_id?: string;
  /** Write-only: a non-empty value replaces the stored secret. */
  oidc_client_secret?: string;
  oidc_scopes?: string;
  oidc_groups_claim?: string;
  role_mapping?: Record<string, string>;
  default_role?: string;
  group_attribute?: string;
  enforcement_mode?: 'OPTIONAL' | 'SSO_REQUIRED';
  is_active?: boolean;
}

export interface SSOTestResult {
  success: boolean;
  message: string;
  idp_entity_id: string | null;
  certificate_valid: boolean | null;
  certificate_expires_at: string | null;
}

export interface SSOStatus {
  sso_enabled: boolean;
  has_active_config: boolean;
  enforcement_mode: string | null;
  /** W20 ADM-08b: the active configuration's type and name ("Sign in with <display_name>"). */
  provider_type?: 'SAML' | 'OIDC' | null;
  display_name?: string | null;
}

export interface SCIMToken {
  id: string;
  name: string;
  token_hint: string;
  sso_config_id: string | null;
  is_active: boolean;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
}

export interface SCIMTokenCreated extends SCIMToken {
  raw_token: string;
}

export interface IdentityEvent {
  id: string;
  event_type: string;
  user_id: string | null;
  sso_config_id: string | null;
  actor_id: string | null;
  actor_name: string | null;
  detail: Record<string, unknown> | null;
  ip_address: string | null;
  success: boolean;
  error_message: string | null;
  created_at: string;
}

export interface IdentitySyncStatus {
  sso_config_id: string | null;
  sso_display_name: string | null;
  total_federated_users: number;
  last_sso_login_at: string | null;
  last_scim_sync_at: string | null;
  recent_failures: number;
  recent_events: IdentityEvent[];
}

// ── SSO Config API ──────────────────────────────────────────────────────────

export async function getSSOStatus(): Promise<SSOStatus> {
  const { data } = await api.get<SSOStatus>('/api/v1/sso/status');
  return data;
}

/** Tokens the ACS parked for an SSO sign-in, for its one-time code (works once, for a minute). */
export interface SSOExchangeResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  is_new_user: boolean;
}

export async function exchangeSSOCode(code: string): Promise<SSOExchangeResponse> {
  // The callback page reports a failure itself.
  const { data } = await api.post<SSOExchangeResponse>('/api/v1/sso/exchange', { code }, { suppressToast: true });
  return data;
}

export async function listSSOConfigs(): Promise<SSOConfig[]> {
  const { data } = await api.get<SSOConfig[]>('/api/v1/sso/configs');
  return data;
}

export async function getSSOConfig(id: string): Promise<SSOConfig> {
  const { data } = await api.get<SSOConfig>(`/api/v1/sso/configs/${id}`);
  return data;
}

export async function createSSOConfig(payload: SSOConfigCreate): Promise<SSOConfig> {
  const { data } = await api.post<SSOConfig>('/api/v1/sso/configs', payload);
  return data;
}

export async function updateSSOConfig(id: string, payload: SSOConfigUpdate): Promise<SSOConfig> {
  const { data } = await api.patch<SSOConfig>(`/api/v1/sso/configs/${id}`, payload);
  return data;
}

/** W20 ADM-08a: the role a user in `groups` gets; `role_mapping` / `default_role` preview an unsaved edit. */
export interface SSORolePreview {
  role: string;
  matched_groups: string[];
  source: 'mapping' | 'default';
  ceiling: string;
  clamped: boolean;
}

export async function previewSSORole(
  id: string,
  body: { groups: string[]; role_mapping?: Record<string, string>; default_role?: string },
): Promise<SSORolePreview> {
  const { data } = await api.post<SSORolePreview>(`/api/v1/sso/configs/${id}/preview-role`, body);
  return data;
}

export async function deleteSSOConfig(id: string): Promise<void> {
  await api.delete(`/api/v1/sso/configs/${id}`);
}

export async function testSSOConnection(id: string): Promise<SSOTestResult> {
  const { data } = await api.post<SSOTestResult>(`/api/v1/sso/configs/${id}/test`);
  return data;
}

export async function getSPMetadata(): Promise<Record<string, string>> {
  const { data } = await api.get<Record<string, string>>('/api/v1/sso/metadata');
  return data;
}

// ── SCIM Token API ──────────────────────────────────────────────────────────

export async function listSCIMTokens(): Promise<SCIMToken[]> {
  const { data } = await api.get<SCIMToken[]>('/api/v1/scim-tokens');
  return data;
}

export async function createSCIMToken(
  name: string,
  ssoConfigId?: string,
  expiresDays?: number,
): Promise<SCIMTokenCreated> {
  const { data } = await api.post<SCIMTokenCreated>('/api/v1/scim-tokens', {
    name,
    sso_config_id: ssoConfigId,
    expires_days: expiresDays,
  });
  return data;
}

export async function revokeSCIMToken(id: string): Promise<void> {
  await api.delete(`/api/v1/scim-tokens/${id}`);
}

// ── Identity Events API ─────────────────────────────────────────────────────

export async function listIdentityEvents(params?: {
  event_type?: string;
  user_id?: string;
  success?: boolean;
  days?: number;
  page?: number;
  page_size?: number;
}): Promise<{ total: number; items: IdentityEvent[] }> {
  const { data } = await api.get('/api/v1/identity/events', { params });
  return data;
}

export async function getIdentitySyncStatus(ssoConfigId?: string): Promise<IdentitySyncStatus> {
  const { data } = await api.get<IdentitySyncStatus>('/api/v1/identity/sync-status', {
    params: ssoConfigId ? { sso_config_id: ssoConfigId } : undefined,
  });
  return data;
}

// ── SCIM groups → project roles (W20 ADM-09) ────────────────────────────────

export interface SCIMGroupMappingRow {
  project_id: string;
  project_name?: string;
  role: string;
}

export interface SCIMGroupSummary {
  id: string;
  display_name: string;
  external_id: string | null;
  member_count: number;
  mappings: SCIMGroupMappingRow[];
}

export async function listSCIMGroups(): Promise<{ groups: SCIMGroupSummary[]; roles: string[] }> {
  const { data } = await api.get<{ groups: SCIMGroupSummary[]; roles: string[] }>('/api/v1/sso/scim-groups');
  return data;
}

export async function putSCIMGroupMappings(
  groupId: string,
  mappings: { project_id: string; role: string }[],
): Promise<{ reconciled_users: number; groups: SCIMGroupSummary[] }> {
  const { data } = await api.put<{ reconciled_users: number; groups: SCIMGroupSummary[] }>(
    `/api/v1/sso/scim-groups/${groupId}/mappings`,
    { mappings },
  );
  return data;
}
