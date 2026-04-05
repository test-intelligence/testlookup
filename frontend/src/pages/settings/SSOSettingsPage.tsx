import { useCallback, useEffect, useState } from 'react';
import {
  type IdentityEvent,
  type IdentitySyncStatus,
  type SCIMToken,
  type SCIMTokenCreated,
  type SSOConfig,
  type SSOConfigCreate,
  type SSOTestResult,
  createSCIMToken,
  createSSOConfig,
  deleteSSOConfig,
  getIdentitySyncStatus,
  listIdentityEvents,
  listSCIMTokens,
  listSSOConfigs,
  revokeSCIMToken,
  testSSOConnection,
  updateSSOConfig,
} from '../../services/ssoService';

type Tab = 'config' | 'scim' | 'events' | 'sync';

export default function SSOSettingsPage() {
  const [tab, setTab] = useState<Tab>('config');
  const [configs, setConfigs] = useState<SSOConfig[]>([]);
  const [scimTokens, setScimTokens] = useState<SCIMToken[]>([]);
  const [events, setEvents] = useState<IdentityEvent[]>([]);
  const [syncStatus, setSyncStatus] = useState<IdentitySyncStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // SSO Config form
  const [showForm, setShowForm] = useState(false);
  const [formData, setFormData] = useState<Partial<SSOConfigCreate>>({});
  const [testResult, setTestResult] = useState<SSOTestResult | null>(null);
  const [newToken, setNewToken] = useState<SCIMTokenCreated | null>(null);
  const [tokenName, setTokenName] = useState('');

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (tab === 'config') {
        setConfigs(await listSSOConfigs());
      } else if (tab === 'scim') {
        setScimTokens(await listSCIMTokens());
      } else if (tab === 'events') {
        const result = await listIdentityEvents({ days: 30, page_size: 50 });
        setEvents(result.items);
      } else if (tab === 'sync') {
        setSyncStatus(await getIdentitySyncStatus());
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load data');
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const handleCreateConfig = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formData.display_name || !formData.idp_entity_id || !formData.idp_sso_url || !formData.idp_certificate || !formData.sp_entity_id || !formData.sp_acs_url) {
      setError('Please fill in all required fields');
      return;
    }
    try {
      await createSSOConfig(formData as SSOConfigCreate);
      setShowForm(false);
      setFormData({});
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create SSO config');
    }
  };

  const handleToggleActive = async (config: SSOConfig) => {
    try {
      await updateSSOConfig(config.id, { is_active: !config.is_active });
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to toggle SSO config');
    }
  };

  const handleTestConnection = async (configId: string) => {
    try {
      const result = await testSSOConnection(configId);
      setTestResult(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Test connection failed');
    }
  };

  const handleDeleteConfig = async (configId: string) => {
    if (!confirm('Are you sure you want to delete this SSO configuration?')) return;
    try {
      await deleteSSOConfig(configId);
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete SSO config');
    }
  };

  const handleCreateToken = async () => {
    if (!tokenName.trim()) return;
    try {
      const created = await createSCIMToken(tokenName.trim());
      setNewToken(created);
      setTokenName('');
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create SCIM token');
    }
  };

  const handleRevokeToken = async (tokenId: string) => {
    if (!confirm('Revoke this SCIM token?')) return;
    try {
      await revokeSCIMToken(tokenId);
      loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke SCIM token');
    }
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: 'config', label: 'SSO Configuration' },
    { key: 'scim', label: 'SCIM Tokens' },
    { key: 'events', label: 'Identity Events' },
    { key: 'sync', label: 'Sync Status' },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-100">SSO & Identity Management</h1>
        <p className="mt-1 text-sm text-[var(--color-text-muted)]">
          Configure SAML SSO, manage SCIM provisioning tokens, and monitor identity events.
        </p>
      </div>

      {/* Tab navigation */}
      <div className="flex gap-1 border-b border-gray-700">
        {tabs.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium rounded-t-lg ${
              tab === t.key
                ? 'bg-[var(--color-bg-secondary)] text-[var(--color-text)] border-b-2 border-neutral-500'
                : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg p-3 text-red-300 text-sm">
          {error}
          <button onClick={() => setError(null)} className="ml-2 text-red-400 hover:text-red-200">Dismiss</button>
        </div>
      )}

      {loading ? (
        <div className="text-[var(--color-text-muted)] text-center py-8">Loading...</div>
      ) : (
        <>
          {/* SSO Configuration Tab */}
          {tab === 'config' && (
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <h2 className="text-lg font-semibold text-neutral-200">SSO Configurations</h2>
                <button
                  onClick={() => setShowForm(!showForm)}
                  className="px-4 py-2 bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] rounded-lg hover:bg-neutral-200 text-sm"
                >
                  {showForm ? 'Cancel' : 'Add SSO Configuration'}
                </button>
              </div>

              {showForm && (
                <form onSubmit={handleCreateConfig} className="bg-[var(--color-bg-secondary)] rounded-lg p-4 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <input
                      placeholder="Display Name *"
                      value={formData.display_name || ''}
                      onChange={e => setFormData(d => ({ ...d, display_name: e.target.value }))}
                      className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm"
                      required
                    />
                    <input
                      placeholder="IdP Entity ID *"
                      value={formData.idp_entity_id || ''}
                      onChange={e => setFormData(d => ({ ...d, idp_entity_id: e.target.value }))}
                      className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm"
                      required
                    />
                    <input
                      placeholder="IdP SSO URL *"
                      value={formData.idp_sso_url || ''}
                      onChange={e => setFormData(d => ({ ...d, idp_sso_url: e.target.value }))}
                      className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm"
                      required
                    />
                    <input
                      placeholder="SP Entity ID *"
                      value={formData.sp_entity_id || ''}
                      onChange={e => setFormData(d => ({ ...d, sp_entity_id: e.target.value }))}
                      className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm"
                      required
                    />
                    <input
                      placeholder="SP ACS URL *"
                      value={formData.sp_acs_url || ''}
                      onChange={e => setFormData(d => ({ ...d, sp_acs_url: e.target.value }))}
                      className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm"
                      required
                    />
                    <input
                      placeholder="Group Attribute (e.g., memberOf)"
                      value={formData.group_attribute || ''}
                      onChange={e => setFormData(d => ({ ...d, group_attribute: e.target.value }))}
                      className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm"
                    />
                  </div>
                  <textarea
                    placeholder="IdP Certificate (PEM) *"
                    value={formData.idp_certificate || ''}
                    onChange={e => setFormData(d => ({ ...d, idp_certificate: e.target.value }))}
                    className="w-full bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm font-mono h-32"
                    required
                  />
                  <div className="flex gap-2">
                    <select
                      value={formData.enforcement_mode || 'OPTIONAL'}
                      onChange={e => setFormData(d => ({ ...d, enforcement_mode: e.target.value as 'OPTIONAL' | 'SSO_REQUIRED' }))}
                      className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm"
                    >
                      <option value="OPTIONAL">Optional (password login allowed)</option>
                      <option value="SSO_REQUIRED">SSO Required (admin fallback only)</option>
                    </select>
                    <select
                      value={formData.default_role || 'VIEWER'}
                      onChange={e => setFormData(d => ({ ...d, default_role: e.target.value }))}
                      className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm"
                    >
                      <option value="VIEWER">Default Role: Viewer</option>
                      <option value="TESTER">Default Role: Tester</option>
                      <option value="QA_ENGINEER">Default Role: QA Engineer</option>
                      <option value="QA_LEAD">Default Role: QA Lead</option>
                    </select>
                  </div>
                  <button type="submit" className="px-4 py-2 bg-green-600 text-[var(--color-text)] rounded hover:bg-green-700 text-sm">
                    Create Configuration
                  </button>
                </form>
              )}

              {/* Config list */}
              {configs.length === 0 && !showForm && (
                <div className="text-center py-8 text-gray-500">
                  No SSO configurations. Click "Add SSO Configuration" to get started.
                </div>
              )}

              {configs.map(config => (
                <div key={config.id} className="bg-[var(--color-bg-secondary)] rounded-lg p-4 space-y-2">
                  <div className="flex justify-between items-start">
                    <div>
                      <h3 className="text-gray-100 font-medium">{config.display_name}</h3>
                      <p className="text-xs text-[var(--color-text-muted)] mt-1">
                        {config.provider_type} | Entity: {config.idp_entity_id}
                      </p>
                      <p className="text-xs text-gray-500 mt-1">
                        Cert fingerprint: {config.idp_certificate_fingerprint.slice(0, 16)}...
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`px-2 py-0.5 rounded text-xs ${config.is_active ? 'bg-green-900/40 text-green-400' : 'bg-gray-700 text-[var(--color-text-muted)]'}`}>
                        {config.is_active ? 'Active' : 'Inactive'}
                      </span>
                      <span className={`px-2 py-0.5 rounded text-xs ${config.enforcement_mode === 'SSO_REQUIRED' ? 'bg-yellow-900/40 text-yellow-400' : 'bg-gray-700 text-[var(--color-text-muted)]'}`}>
                        {config.enforcement_mode === 'SSO_REQUIRED' ? 'Enforced' : 'Optional'}
                      </span>
                    </div>
                  </div>
                  <div className="flex gap-2 pt-2">
                    <button onClick={() => handleToggleActive(config)} className="px-3 py-1 text-xs bg-gray-700 rounded hover:bg-gray-600 text-gray-300">
                      {config.is_active ? 'Deactivate' : 'Activate'}
                    </button>
                    <button onClick={() => handleTestConnection(config.id)} className="px-3 py-1 text-xs bg-neutral-200 rounded hover:bg-white text-[var(--color-text-secondary)]">
                      Test Connection
                    </button>
                    <button onClick={() => handleDeleteConfig(config.id)} className="px-3 py-1 text-xs bg-red-700/50 rounded hover:bg-red-600 text-red-300">
                      Delete
                    </button>
                  </div>
                  {config.last_test_at && (
                    <p className="text-xs text-gray-500">
                      Last test: {new Date(config.last_test_at).toLocaleString()} —{' '}
                      <span className={config.last_test_success ? 'text-green-400' : 'text-red-400'}>
                        {config.last_test_success ? 'Passed' : `Failed: ${config.last_test_error}`}
                      </span>
                    </p>
                  )}
                </div>
              ))}

              {testResult && (
                <div className={`rounded-lg p-3 text-sm ${testResult.success ? 'bg-green-900/30 border border-green-700 text-green-300' : 'bg-red-900/30 border border-red-700 text-red-300'}`}>
                  <strong>Test Result:</strong> {testResult.message}
                  <button onClick={() => setTestResult(null)} className="ml-2 underline text-xs">Dismiss</button>
                </div>
              )}
            </div>
          )}

          {/* SCIM Tokens Tab */}
          {tab === 'scim' && (
            <div className="space-y-4">
              <h2 className="text-lg font-semibold text-neutral-200">SCIM Provisioning Tokens</h2>

              <div className="flex gap-2">
                <input
                  placeholder="Token name"
                  value={tokenName}
                  onChange={e => setTokenName(e.target.value)}
                  className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm flex-1"
                />
                <button onClick={handleCreateToken} disabled={!tokenName.trim()} className="px-4 py-2 bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] rounded hover:bg-neutral-200 text-sm disabled:opacity-50">
                  Generate Token
                </button>
              </div>

              {newToken && (
                <div className="bg-yellow-900/30 border border-yellow-700 rounded-lg p-3 text-sm text-yellow-300">
                  <strong>New token created.</strong> Copy it now — it won't be shown again:
                  <code className="block mt-1 bg-gray-900 p-2 rounded text-xs font-mono break-all">{newToken.raw_token}</code>
                  <button onClick={() => setNewToken(null)} className="mt-2 text-xs underline">Dismiss</button>
                </div>
              )}

              {scimTokens.length === 0 && (
                <div className="text-center py-8 text-gray-500">No SCIM tokens.</div>
              )}

              <div className="space-y-2">
                {scimTokens.map(token => (
                  <div key={token.id} className="bg-[var(--color-bg-secondary)] rounded-lg p-3 flex justify-between items-center">
                    <div>
                      <span className="text-gray-100 text-sm font-medium">{token.name}</span>
                      <span className="text-gray-500 text-xs ml-2">{token.token_hint}</span>
                      {token.last_used_at && (
                        <span className="text-gray-500 text-xs ml-2">Last used: {new Date(token.last_used_at).toLocaleString()}</span>
                      )}
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`px-2 py-0.5 rounded text-xs ${token.is_active ? 'bg-green-900/40 text-green-400' : 'bg-red-900/40 text-red-400'}`}>
                        {token.is_active ? 'Active' : 'Revoked'}
                      </span>
                      {token.is_active && (
                        <button onClick={() => handleRevokeToken(token.id)} className="px-2 py-1 text-xs bg-red-700/50 rounded hover:bg-red-600 text-red-300">
                          Revoke
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Identity Events Tab */}
          {tab === 'events' && (
            <div className="space-y-4">
              <h2 className="text-lg font-semibold text-neutral-200">Identity Events (Last 30 Days)</h2>

              {events.length === 0 && (
                <div className="text-center py-8 text-gray-500">No identity events found.</div>
              )}

              <div className="space-y-1">
                {events.map(event => (
                  <div key={event.id} className="bg-[var(--color-bg-secondary)] rounded px-3 py-2 flex justify-between items-center text-sm">
                    <div className="flex items-center gap-3">
                      <span className={`w-2 h-2 rounded-full ${event.success ? 'bg-green-400' : 'bg-red-400'}`} />
                      <span className="text-gray-300 font-mono text-xs">{event.event_type}</span>
                      {event.actor_name && <span className="text-gray-500 text-xs">by {event.actor_name}</span>}
                    </div>
                    <div className="flex items-center gap-3">
                      {event.ip_address && <span className="text-gray-600 text-xs">{event.ip_address}</span>}
                      <span className="text-gray-500 text-xs">{new Date(event.created_at).toLocaleString()}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Sync Status Tab */}
          {tab === 'sync' && syncStatus && (
            <div className="space-y-4">
              <h2 className="text-lg font-semibold text-neutral-200">Identity Sync Status</h2>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4 text-center">
                  <div className="text-2xl font-bold text-[var(--color-text)]">{syncStatus.total_federated_users}</div>
                  <div className="text-xs text-[var(--color-text-muted)] mt-1">Federated Users</div>
                </div>
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4 text-center">
                  <div className="text-2xl font-bold text-green-400">
                    {syncStatus.last_sso_login_at ? new Date(syncStatus.last_sso_login_at).toLocaleDateString() : 'Never'}
                  </div>
                  <div className="text-xs text-[var(--color-text-muted)] mt-1">Last SSO Login</div>
                </div>
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4 text-center">
                  <div className="text-2xl font-bold text-purple-400">
                    {syncStatus.last_scim_sync_at ? new Date(syncStatus.last_scim_sync_at).toLocaleDateString() : 'Never'}
                  </div>
                  <div className="text-xs text-[var(--color-text-muted)] mt-1">Last SCIM Sync</div>
                </div>
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4 text-center">
                  <div className={`text-2xl font-bold ${syncStatus.recent_failures > 0 ? 'text-red-400' : 'text-green-400'}`}>
                    {syncStatus.recent_failures}
                  </div>
                  <div className="text-xs text-[var(--color-text-muted)] mt-1">Failures (24h)</div>
                </div>
              </div>

              {syncStatus.recent_events.length > 0 && (
                <div>
                  <h3 className="text-sm font-medium text-gray-300 mb-2">Recent Events</h3>
                  <div className="space-y-1">
                    {syncStatus.recent_events.map(event => (
                      <div key={event.id} className="bg-[var(--color-bg-secondary)] rounded px-3 py-2 flex justify-between items-center text-xs">
                        <div className="flex items-center gap-2">
                          <span className={`w-1.5 h-1.5 rounded-full ${event.success ? 'bg-green-400' : 'bg-red-400'}`} />
                          <span className="text-gray-300 font-mono">{event.event_type}</span>
                        </div>
                        <span className="text-gray-500">{new Date(event.created_at).toLocaleString()}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
