import { useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { clsx } from 'clsx';
import PageHeader from '@/components/ui/PageHeader';
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline';
import { buildAuditWorkflow } from '@/components/workflow/workflowPresets';
import { exportAuditCSV } from '../../services/auditDashboardService';
import {
  useAuditCategories,
  useAuditEvents,
  useProjectObservability,
} from '@/hooks/useAuditDashboard';
import { useProjectStore, ALL_PROJECTS_ID } from '../../store/projectStore';
import { snapToAllowed, useTimeWindowStore } from '../../store/timeWindowStore';

type Tab = 'events' | 'observability';

const SOURCE_COLORS: Record<string, string> = {
  access: 'bg-[var(--color-bg-secondary)]/60 text-[var(--color-text)]',
  settings: 'bg-purple-900/40 text-purple-400',
  test_management: 'bg-cyan-900/40 text-cyan-400',
  identity: 'bg-yellow-900/40 text-yellow-400',
  report: 'bg-green-900/40 text-green-400',
  notification: 'bg-gray-700 text-gray-300',
  release: 'bg-red-900/40 text-red-400',
};

export default function AuditDashboardPage() {
  const [tab, setTab] = useState<Tab>('events');
  const activeProjectId = useProjectStore(s => s.activeProjectId);
  const projectId: string | undefined = activeProjectId === ALL_PROJECTS_ID ? undefined : (activeProjectId ?? undefined);

  // Events tab
  const [selectedCategory, setSelectedCategory] = useState('');
  // Global shared time window — Audit only offers 7/30/90 in its
  // dropdown but participates in the shared preference so the user
  // doesn't keep flipping the window every time they navigate.
  const AUDIT_OPTIONS = [7, 30, 90] as const;
  const storedDays = useTimeWindowStore(s => s.days);
  const setStoredDays = useTimeWindowStore(s => s.setDays);
  const days = snapToAllowed(storedDays, AUDIT_OPTIONS);
  const setDays = setStoredDays;

  // Data fetches are SWR hooks — each owns its loading/data/error state and
  // re-fetches when its keyed params (project, category, day window) change,
  // so the page no longer drives state from a tab-keyed load-on-mount effect.
  const { categories } = useAuditCategories();
  const { events, total, isLoading: loading } = useAuditEvents(
    { projectId, category: selectedCategory, days },
    tab === 'events',
  );
  const { observability: obs } = useProjectObservability(projectId, tab === 'observability');

  const workflow = useMemo(
    () => buildAuditWorkflow(obs, events),
    [obs, events],
  );

  const handleExport = async () => {
    try {
      await exportAuditCSV({ project_id: projectId, category: selectedCategory || undefined, days });
      toast.success('Audit export downloaded');
    } catch { toast.error('Export failed'); }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Audit Dashboard"
        subtitle="Unified view of security actions, configuration changes, and tenant metrics."
        actions={
          <button onClick={handleExport} className="px-4 py-2 bg-gray-700 text-neutral-200 rounded-lg hover:bg-gray-600 text-sm">
            Export CSV
          </button>
        }
      />

      <WorkflowTimeline
        title="Tenant audit flow"
        subtitle="Access events, configuration changes, quality rollups, and scoped exports"
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
        showInspector
      />

      <div className="flex gap-1 border-b border-gray-700">
        {([
          { key: 'events' as Tab, label: 'Audit Events' },
          { key: 'observability' as Tab, label: 'Project Observability' },
        ]).map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={clsx('px-4 py-2 text-sm font-medium rounded-t-lg', tab === t.key ? 'bg-[var(--color-bg-secondary)] text-[var(--color-text)] border-b-2 border-neutral-500' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]')}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Events Tab */}
      {tab === 'events' && (
        <div className="space-y-4">
          <div className="flex gap-2 items-center flex-wrap">
            <select value={selectedCategory} onChange={e => setSelectedCategory(e.target.value)}
              className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm">
              <option value="">All categories</option>
              {categories.map(c => <option key={c.key} value={c.key}>{c.key} — {c.description}</option>)}
            </select>
            <select value={days} onChange={e => setDays(parseInt(e.target.value))}
              className="bg-gray-700 text-gray-100 rounded px-3 py-2 text-sm">
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </select>
            <span className="text-xs text-gray-500">{total} events</span>
          </div>

          {loading ? <div className="text-[var(--color-text-muted)] text-center py-8">Loading...</div> : (
            <div className="space-y-1">
              {events.length === 0 && <div className="text-center py-8 text-gray-500">No audit events found for this filter.</div>}
              {events.map((ev, i) => (
                <div key={i} className="bg-[var(--color-bg-secondary)] rounded px-3 py-2 flex justify-between items-center text-sm">
                  <div className="flex items-center gap-3">
                    <span className={clsx('px-2 py-0.5 rounded text-[10px] font-medium', SOURCE_COLORS[ev.source] || 'bg-gray-700 text-[var(--color-text-muted)]')}>
                      {ev.source}
                    </span>
                    <span className="text-gray-300 font-mono text-xs">{ev.action}</span>
                    {ev.actor_name && <span className="text-gray-500 text-xs">by {ev.actor_name}</span>}
                  </div>
                  <span className="text-gray-600 text-xs">{ev.created_at ? new Date(ev.created_at).toLocaleString() : '—'}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Observability Tab */}
      {tab === 'observability' && (
        <div className="space-y-4">
          {!projectId ? (
            <div className="text-center py-8 text-gray-500">Select a project to view tenant-scoped observability metrics.</div>
          ) : obs ? (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-[var(--color-text)]">{obs.total_runs}</div>
                <div className="text-xs text-[var(--color-text-muted)] mt-1">Runs ({obs.period_days}d)</div>
              </div>
              <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-green-400">{obs.avg_pass_rate?.toFixed(1) ?? 'N/A'}%</div>
                <div className="text-xs text-[var(--color-text-muted)] mt-1">Avg Pass Rate</div>
              </div>
              <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-red-400">{obs.failed_runs}</div>
                <div className="text-xs text-[var(--color-text-muted)] mt-1">Failed Runs</div>
              </div>
              <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-purple-400">{obs.ai_analyses_count}</div>
                <div className="text-xs text-[var(--color-text-muted)] mt-1">AI Analyses</div>
              </div>
              <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-amber-400">{obs.release_decisions_count}</div>
                <div className="text-xs text-[var(--color-text-muted)] mt-1">Release Decisions</div>
              </div>
              <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-cyan-400">{obs.total_tests}</div>
                <div className="text-xs text-[var(--color-text-muted)] mt-1">Total Tests</div>
              </div>
              <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4 text-center">
                <div className="text-2xl font-bold text-gray-300">{obs.audit_events_count}</div>
                <div className="text-xs text-[var(--color-text-muted)] mt-1">Audit Events</div>
              </div>
            </div>
          ) : (
            <div className="text-[var(--color-text-muted)] text-center py-8">Loading...</div>
          )}
        </div>
      )}
    </div>
  );
}
