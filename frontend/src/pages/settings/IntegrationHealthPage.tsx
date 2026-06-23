import { useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { clsx } from 'clsx';
import PageHeader from '@/components/ui/PageHeader';
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline';
import { buildIntegrationHealthWorkflow } from '@/components/workflow/workflowPresets';
import {
  useHealthTrends,
  useIntegrationStatus,
  useProviderHistory,
  refreshIntegrationHealth,
} from '@/hooks/useIntegrationHealth';
import { triggerProbe } from '../../services/integrationHealthService';

const STATUS_COLORS: Record<string, string> = {
  healthy: 'bg-green-900/40 text-green-400',
  degraded: 'bg-yellow-900/40 text-yellow-400',
  down: 'bg-red-900/40 text-red-400',
  auth_error: 'bg-red-900/40 text-red-400',
  timeout: 'bg-orange-900/40 text-orange-400',
  unknown: 'bg-gray-700 text-[var(--color-text-muted)]',
  skipped: 'bg-gray-700 text-gray-500',
};

type Tab = 'status' | 'trends' | 'history';

export default function IntegrationHealthPage() {
  const [tab, setTab] = useState<Tab>('status');
  const [selectedProvider, setSelectedProvider] = useState<string>('');
  const [probing, setProbing] = useState(false);

  const { statuses, isLoading: statusLoading } = useIntegrationStatus();
  const { trends, isLoading: trendsLoading } = useHealthTrends(tab === 'trends');
  const { history, isLoading: historyLoading } = useProviderHistory(selectedProvider, tab === 'history');

  const loading =
    tab === 'status' ? statusLoading : tab === 'trends' ? trendsLoading : historyLoading;

  const handleProbe = async (provider?: string) => {
    setProbing(true);
    try {
      await triggerProbe(provider);
      toast.success(provider ? `Probed ${provider}` : 'All integrations probed');
      refreshIntegrationHealth();
    } catch { toast.error('Probe failed'); }
    finally { setProbing(false); }
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: 'status', label: 'Current Status' },
    { key: 'trends', label: 'Trends (7d)' },
    { key: 'history', label: 'Probe History' },
  ];

  const workflow = useMemo(
    () => buildIntegrationHealthWorkflow(statuses, history, selectedProvider),
    [statuses, history, selectedProvider],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Integration Health"
        subtitle="Active health probes for Jira, Splunk, GitHub, Slack, Teams, SMTP, and more."
        actions={
          <button onClick={() => handleProbe()} disabled={probing}
            className="px-4 py-2 bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] rounded-lg hover:bg-neutral-200 text-sm disabled:opacity-50">
            {probing ? 'Probing...' : 'Probe All Now'}
          </button>
        }
      />

      <WorkflowTimeline
        title="Health workflow"
        subtitle="Probe providers, validate payloads, and roll up the tenant health picture"
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
        showInspector
      />

      <div className="flex gap-1 border-b border-gray-700">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={clsx('px-4 py-2 text-sm font-medium rounded-t-lg', tab === t.key ? 'bg-[var(--color-bg-secondary)] text-[var(--color-text)] border-b-2 border-neutral-500' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]')}>
            {t.label}
          </button>
        ))}
      </div>

      {loading ? <div className="text-[var(--color-text-muted)] text-center py-8">Loading...</div> : (
        <>
          {/* Current Status */}
          {tab === 'status' && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {statuses.length === 0 && <div className="col-span-full text-center py-8 text-gray-500">No health data yet. Click "Probe All Now" to start.</div>}
              {statuses.map(s => (
                <div key={s.provider} className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                  <div className="flex justify-between items-center mb-2">
                    <span className="text-gray-100 font-medium capitalize">{s.provider}</span>
                    <span className={clsx('px-2 py-0.5 rounded text-xs font-medium', STATUS_COLORS[s.status] || STATUS_COLORS.unknown)}>
                      {s.status}
                    </span>
                  </div>
                  <div className="space-y-1 text-xs text-gray-500">
                    {s.response_ms != null && <p>Latency: <span className="text-gray-300">{s.response_ms}ms</span></p>}
                    {s.message && <p className="truncate" title={s.message}>{s.message}</p>}
                    {s.consecutive_failures > 0 && (
                      <p className="text-red-400">Consecutive failures: {s.consecutive_failures}</p>
                    )}
                    {s.last_checked_at && <p>Last checked: {new Date(s.last_checked_at).toLocaleString()}</p>}
                  </div>
                  <button onClick={() => handleProbe(s.provider)} disabled={probing}
                    className="mt-2 text-xs text-[var(--color-text)] hover:text-[var(--color-text-secondary)]">
                    Probe now
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Trends */}
          {tab === 'trends' && (
            <div className="space-y-2">
              {trends.length === 0 && <div className="text-center py-8 text-gray-500">No trend data available.</div>}
              <div className="bg-[var(--color-bg-hover)]/50 rounded-lg px-4 py-2 grid grid-cols-6 gap-2 text-xs text-gray-500 font-medium">
                <span>Provider</span><span>Uptime</span><span>Healthy</span><span>Degraded</span><span>Down</span><span>Avg Latency</span>
              </div>
              {trends.map(t => (
                <div key={t.provider} className="bg-[var(--color-bg-secondary)] rounded-lg px-4 py-3 grid grid-cols-6 gap-2 items-center text-sm">
                  <span className="text-neutral-200 capitalize font-medium">{t.provider}</span>
                  <span className={clsx('font-mono', t.uptime_pct >= 99 ? 'text-green-400' : t.uptime_pct >= 90 ? 'text-yellow-400' : 'text-red-400')}>
                    {t.uptime_pct}%
                  </span>
                  <span className="text-green-400 text-xs">{t.healthy}</span>
                  <span className="text-yellow-400 text-xs">{t.degraded}</span>
                  <span className="text-red-400 text-xs">{t.down}</span>
                  <span className="text-[var(--color-text-muted)] text-xs">{t.avg_response_ms}ms</span>
                </div>
              ))}
            </div>
          )}

          {/* History */}
          {tab === 'history' && (
            <div className="space-y-4">
              <div className="flex gap-2">
                <select value={selectedProvider} onChange={e => setSelectedProvider(e.target.value)}
                  className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm">
                  <option value="">Select provider...</option>
                  {statuses.map(s => <option key={s.provider} value={s.provider}>{s.provider}</option>)}
                </select>
              </div>
              {!selectedProvider && <div className="text-center py-8 text-gray-500">Select a provider to view probe history.</div>}
              {selectedProvider && history.length === 0 && <div className="text-center py-8 text-gray-500">No history for {selectedProvider}.</div>}
              {history.map(h => (
                <div key={h.id} className="bg-[var(--color-bg-secondary)] rounded px-3 py-2 flex justify-between items-center text-sm">
                  <div className="flex items-center gap-3">
                    <span className={clsx('px-2 py-0.5 rounded text-xs', STATUS_COLORS[h.status] || STATUS_COLORS.unknown)}>{h.status}</span>
                    <span className="text-[var(--color-text-muted)] text-xs">{h.response_ms != null ? `${h.response_ms}ms` : '—'}</span>
                    {h.auth_valid === false && <span className="text-red-400 text-xs">Auth failed</span>}
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-gray-500 text-xs truncate max-w-64">{h.message}</span>
                    <span className="text-gray-600 text-xs">{h.checked_at ? new Date(h.checked_at).toLocaleString() : '—'}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
