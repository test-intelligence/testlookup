import { useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { clsx } from 'clsx';
import PageHeader from '@/components/ui/PageHeader';
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline';
import { buildDigestWorkflow } from '@/components/workflow/workflowPresets';
import {
  type DigestContent,
  createSubscription,
  deleteSubscription,
  pauseSubscription,
  previewDigest,
  resumeSubscription,
} from '../../services/digestService';
import {
  createSavedView,
  deleteSavedView,
} from '../../services/savedViewsService';
import { useDigestSavedViews, useDigestSubscriptions } from '@/hooks/useDigestData';
import { useProjectStore, ALL_PROJECTS_ID } from '../../store/projectStore';

type Tab = 'subscriptions' | 'saved-views' | 'preview';

export default function DigestsPage() {
  const [tab, setTab] = useState<Tab>('subscriptions');
  const activeProjectId = useProjectStore(s => s.activeProjectId);
  const projectId: string | undefined = activeProjectId === ALL_PROJECTS_ID ? undefined : (activeProjectId ?? undefined);

  // Subscriptions
  const { subscriptions: subs, isLoading: subsLoading, isError: subsError, mutate: mutateSubs } =
    useDigestSubscriptions(tab === 'subscriptions');
  const [newSubName, setNewSubName] = useState('');
  const [newSubSchedule, setNewSubSchedule] = useState<import('@/services/digestService').DigestScheduleType>('WEEKLY');
  const [newSubChannel, setNewSubChannel] = useState<'email' | 'slack' | 'teams'>('email');
  const [newSubTriggerFilter, setNewSubTriggerFilter] = useState<'all' | 'failed_only' | 'degraded_only'>('all');
  const [newSubScopeValue, setNewSubScopeValue] = useState('');
  // US-7.5: attach the self-contained HTML analysis report (email + daily/weekly only).
  const [newSubReportAttachment, setNewSubReportAttachment] = useState(false);

  // Saved views
  const { views, isLoading: viewsLoading, isError: viewsError, mutate: mutateViews } =
    useDigestSavedViews(projectId, tab === 'saved-views');
  const [newViewName, setNewViewName] = useState('');

  // Preview
  const [preview, setPreview] = useState<DigestContent | null>(null);
  const [previewPeriod, setPreviewPeriod] = useState<'daily' | 'weekly'>('weekly');
  const [previewing, setPreviewing] = useState(false);

  const loading = subsLoading || viewsLoading;
  const error = subsError || viewsError ? 'Failed to load data' : null;

  const handleCreateSub = async () => {
    if (!newSubName.trim()) { toast.error('Name is required'); return; }
    try {
      await createSubscription({
        project_id: projectId || null,
        name: newSubName.trim(),
        schedule: newSubSchedule,
        channel: newSubChannel,
        ...(newSubSchedule === 'PER_SUITE' && newSubScopeValue ? { scope_type: 'suite' as const, scope_value: newSubScopeValue.trim() } : {}),
        ...(newSubSchedule === 'PER_RUN' ? { trigger_filter: newSubTriggerFilter } : {}),
        ...(newSubChannel === 'email' && (newSubSchedule === 'DAILY' || newSubSchedule === 'WEEKLY')
          ? { report_attachment: newSubReportAttachment }
          : {}),
      });
      toast.success('Subscription created');
      setNewSubName('');
      mutateSubs();
    } catch { toast.error('Failed to create subscription'); }
  };

  const handlePause = async (id: string) => {
    try { await pauseSubscription(id); mutateSubs(); } catch { toast.error('Failed'); }
  };
  const handleResume = async (id: string) => {
    try { await resumeSubscription(id); mutateSubs(); } catch { toast.error('Failed'); }
  };
  const handleDeleteSub = async (id: string) => {
    if (!confirm('Unsubscribe from this digest?')) return;
    try { await deleteSubscription(id); toast.success('Unsubscribed'); mutateSubs(); } catch { toast.error('Failed'); }
  };

  const handleCreateView = async () => {
    if (!newViewName.trim()) return;
    try {
      await createSavedView({
        project_id: projectId || null,
        name: newViewName.trim(),
        filters: {},
      });
      toast.success('View saved');
      setNewViewName('');
      mutateViews();
    } catch { toast.error('Failed to create view'); }
  };

  const handleDeleteView = async (id: string) => {
    if (!confirm('Delete this saved view?')) return;
    try { await deleteSavedView(id); mutateViews(); } catch { toast.error('Failed'); }
  };

  const handlePreview = async () => {
    setPreviewing(true);
    try {
      setPreview(await previewDigest(projectId, previewPeriod));
    } catch { toast.error('Preview failed'); }
    finally { setPreviewing(false); }
  };

  const workflow = useMemo(
    () => buildDigestWorkflow(subs, views.map(view => ({
      id: view.id,
      name: view.name,
      is_default: view.is_default,
      is_shared: view.is_shared,
    })), preview),
    [subs, views, preview],
  );

  const tabs: { key: Tab; label: string }[] = [
    { key: 'subscriptions', label: 'Digest Subscriptions' },
    { key: 'saved-views', label: 'Saved Views' },
    { key: 'preview', label: 'Preview Digest' },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Digests & Saved Views"
        subtitle="Schedule quality digests and save filter views for quick access."
      />

      <WorkflowTimeline
        title="Digest workflow"
        subtitle="Collect signals, resolve saved views, compile the digest, and deliver it to the right channel"
        stages={workflow.stages}
        events={workflow.events}
        stageOrder={workflow.stageOrder}
        compact
        showInspector
      />

      <div className="flex gap-1 border-b border-[var(--color-border)]">
        {tabs.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={clsx('px-4 py-2 text-sm font-medium rounded-t-lg', tab === t.key ? 'bg-[var(--color-bg-secondary)] text-[var(--color-text)] border-b-2 border-[var(--color-border)]' : 'text-[var(--color-text-muted)] hover:text-[var(--color-text)]')}>
            {t.label}
          </button>
        ))}
      </div>

      {error && <div className="bg-[var(--status-failed-bg)]/30 border border-[var(--status-failed-bd)] rounded-lg p-3 text-[var(--status-failed)] text-sm">{error}</div>}

      {loading ? <div className="text-[var(--color-text-muted)] text-center py-8">Loading...</div> : (
        <>
          {/* Subscriptions tab */}
          {tab === 'subscriptions' && (
            <div className="space-y-4">
              <div className="flex gap-2 items-end">
                <div className="flex-1">
                  <label className="text-xs text-[var(--color-text-muted)]">Digest Name</label>
                  <input value={newSubName} onChange={e => setNewSubName(e.target.value)}
                    placeholder="e.g., Weekly QA Summary"
                    className="w-full bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1" />
                </div>
                <div>
                  <label className="text-xs text-[var(--color-text-muted)]">Schedule</label>
                  <select value={newSubSchedule} onChange={e => setNewSubSchedule(e.target.value as typeof newSubSchedule)}
                    className="bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1">
                    <option value="WEEKLY">Weekly</option>
                    <option value="DAILY">Daily</option>
                    <option value="PER_RUN">Per Run</option>
                    <option value="PER_RELEASE">Per Release</option>
                    <option value="PER_SUITE">Per Suite</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-[var(--color-text-muted)]">Channel</label>
                  <select value={newSubChannel} onChange={e => setNewSubChannel(e.target.value as 'email' | 'slack' | 'teams')}
                    className="bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1">
                    <option value="email">Email</option>
                    <option value="slack">Slack</option>
                    <option value="teams">Teams</option>
                  </select>
                </div>
                {newSubSchedule === 'PER_RUN' && (
                  <div>
                    <label className="text-xs text-[var(--color-text-muted)]">Trigger</label>
                    <select value={newSubTriggerFilter} onChange={e => setNewSubTriggerFilter(e.target.value as typeof newSubTriggerFilter)}
                      className="bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1">
                      <option value="all">All Runs</option>
                      <option value="failed_only">Failed Only</option>
                      <option value="degraded_only">Degraded Only</option>
                    </select>
                  </div>
                )}
                {newSubSchedule === 'PER_SUITE' && (
                  <div className="flex-1">
                    <label className="text-xs text-[var(--color-text-muted)]">Suite Name</label>
                    <input value={newSubScopeValue} onChange={e => setNewSubScopeValue(e.target.value)}
                      placeholder="e.g., smoke-tests"
                      className="w-full bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1" />
                  </div>
                )}
                <button onClick={handleCreateSub} className="px-4 py-2 bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] rounded hover:bg-[var(--color-bg-hover)] text-sm">
                  Subscribe
                </button>
              </div>

              {newSubChannel === 'email' && (newSubSchedule === 'DAILY' || newSubSchedule === 'WEEKLY') && (
                <label className="flex items-start gap-2 text-sm text-[var(--color-text-muted)] cursor-pointer">
                  <input
                    type="checkbox"
                    checked={newSubReportAttachment}
                    onChange={e => setNewSubReportAttachment(e.target.checked)}
                    className="mt-0.5"
                  />
                  <span>
                    <span className="text-[var(--color-text)]">Attach analysis report</span>
                    <span className="block text-xs text-[var(--color-text-muted)]">
                      Adds a self-contained HTML report (runs, failures, flaky &amp; quarantine,
                      slowest tests, release gate, defects, ownership) covering the digest window
                      — 1 day for daily, 7 days for weekly. Email digests only; requires a
                      project-scoped subscription.
                    </span>
                  </span>
                </label>
              )}

              {subs.length === 0 && <div className="text-center py-8 text-[var(--color-text-muted)]">No digest subscriptions. Create one above.</div>}
              {subs.map(sub => (
                <div key={sub.id} className="bg-[var(--color-bg-secondary)] rounded-lg p-4 flex justify-between items-center">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-[var(--color-text)] font-medium">{sub.name}</span>
                      <span className={clsx('px-2 py-0.5 rounded text-xs', sub.is_paused ? 'bg-[var(--status-skipped-bg)]/40 text-[var(--status-skipped)]' : sub.is_active ? 'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]' : 'bg-[var(--color-bg-card)] text-[var(--color-text-muted)]')}>
                        {sub.is_paused ? 'Paused' : sub.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </div>
                    <p className="text-xs text-[var(--color-text-muted)] mt-1">
                      {sub.schedule.replace('_', ' ')} via {sub.channel}
                      {sub.scope_value ? ` · Scope: ${sub.scope_value}` : ''}
                      {sub.trigger_filter && sub.trigger_filter !== 'all' ? ` · ${sub.trigger_filter.replace('_', ' ')}` : ''}
                      {sub.report_attachment ? ' · Report attached' : ''}
                      {' '}· Delivered: {sub.delivery_count} times
                      {sub.next_delivery_at && ` · Next: ${new Date(sub.next_delivery_at).toLocaleDateString()}`}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    {sub.is_paused ? (
                      <button onClick={() => handleResume(sub.id)} className="px-3 py-1 text-xs bg-[var(--status-passed-bg)]/50 rounded text-[var(--status-passed)] hover:bg-[var(--status-passed-bg)]">Resume</button>
                    ) : (
                      <button onClick={() => handlePause(sub.id)} className="px-3 py-1 text-xs bg-[var(--status-skipped-bg)]/50 rounded text-[var(--status-skipped)] hover:bg-[var(--status-skipped-bg)]">Pause</button>
                    )}
                    <button onClick={() => handleDeleteSub(sub.id)} className="px-3 py-1 text-xs bg-[var(--status-failed-bg)]/50 rounded text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]">Unsubscribe</button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* Saved Views tab */}
          {tab === 'saved-views' && (
            <div className="space-y-4">
              <div className="flex gap-2">
                <input value={newViewName} onChange={e => setNewViewName(e.target.value)} placeholder="View name"
                  className="bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm flex-1" />
                <button onClick={handleCreateView} disabled={!newViewName.trim()}
                  className="px-4 py-2 bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] rounded hover:bg-[var(--color-bg-hover)] text-sm disabled:opacity-50">
                  Save View
                </button>
              </div>

              {views.length === 0 && <div className="text-center py-8 text-[var(--color-text-muted)]">No saved views.</div>}
              {views.map(view => (
                <div key={view.id} className="bg-[var(--color-bg-secondary)] rounded-lg p-3 flex justify-between items-center">
                  <div>
                    <span className="text-[var(--color-text)] text-sm font-medium">{view.name}</span>
                    {view.is_shared && <span className="ml-2 px-1.5 py-0.5 text-[10px] bg-[var(--color-bg-secondary)]/60 text-[var(--color-text)] rounded">Shared</span>}
                    {view.is_default && <span className="ml-2 px-1.5 py-0.5 text-[10px] bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)] rounded">Default</span>}
                    <p className="text-xs text-[var(--color-text-muted)] mt-0.5">{Object.keys(view.filters).length} filter(s) · Created {new Date(view.created_at).toLocaleDateString()}</p>
                  </div>
                  <button onClick={() => handleDeleteView(view.id)} className="px-2 py-1 text-xs bg-[var(--status-failed-bg)]/50 rounded text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]">Delete</button>
                </div>
              ))}
            </div>
          )}

          {/* Preview tab */}
          {tab === 'preview' && (
            <div className="space-y-4">
              <div className="flex gap-2 items-center">
                <select value={previewPeriod} onChange={e => setPreviewPeriod(e.target.value as 'daily' | 'weekly')}
                  className="bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm">
                  <option value="weekly">Weekly</option>
                  <option value="daily">Daily</option>
                </select>
                <button onClick={handlePreview} disabled={previewing}
                  className="px-4 py-2 bg-[var(--status-flaky-bg)] text-[var(--color-text)] rounded hover:bg-[var(--status-flaky-bg)] text-sm disabled:opacity-50">
                  {previewing ? 'Generating...' : 'Generate Preview'}
                </button>
              </div>

              {preview && (
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-6 space-y-4">
                  <h2 className="text-lg font-semibold text-[var(--color-text)]">{preview.period.charAt(0).toUpperCase() + preview.period.slice(1)} Digest — {preview.project_name || 'All Projects'}</h2>

                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--status-passed)]">{preview.avg_pass_rate?.toFixed(1) ?? 'N/A'}%</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Avg Pass Rate
                        {preview.pass_rate_trend != null && (
                          <span className={preview.pass_rate_trend >= 0 ? 'text-[var(--status-passed)]' : 'text-[var(--status-failed)]'}>
                            {' '}({preview.pass_rate_trend >= 0 ? '+' : ''}{preview.pass_rate_trend.toFixed(1)}%)
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--color-text)]">{preview.total_runs}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Runs</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--status-failed)]">{preview.new_regressions}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Regressions</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--status-broken)]">{preview.flaky_test_count}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Flaky Tests</div>
                    </div>
                  </div>

                  {preview.action_items.length > 0 && (
                    <div>
                      <h3 className="text-sm font-medium text-[var(--color-text-secondary)] mb-1">Action Items</h3>
                      {preview.action_items.map((item, i) => (
                        <p key={i} className="text-sm text-[var(--color-text-muted)]">→ {item}</p>
                      ))}
                    </div>
                  )}

                  {preview.top_blockers.length > 0 && (
                    <div>
                      <h3 className="text-sm font-medium text-[var(--color-text-secondary)] mb-1">Top Blockers</h3>
                      {preview.top_blockers.map((b, i) => (
                        <p key={i} className="text-sm text-[var(--status-failed)]">• {b}</p>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
