/**
 * Digests & Saved Views (`/settings/digests`).
 *
 * W09:
 *  - NTF-03: a Per Release digest names a release of the top bar project
 *    (`DigestReleaseSelect`, fed by the top bar's own cached release list: no
 *    request of its own); a row says what it covers (`Per Release · 2026.10
 *    via email · …`) and, when the server says it cannot deliver, a badge and
 *    an inline fix (`SubscriptionScopeFix`).
 *  - NTF-04: Saved Views lists the project's REPORT views and the legacy
 *    empty rows, edited in the page (`DigestViewEditor`); a Daily or Weekly
 *    digest may name one (`DigestViewPicker`), and the preview says what a
 *    filtered digest covers (`DigestScopeBlock`).
 *
 * Nothing new is asked on load (contract §6.5): the views are read when the
 * Saved Views or Preview tab opens, or when a picker on the Subscriptions tab
 * is clicked. Every new form is in the page (owner rule 2026-10-16); since
 * W27b the two questions (Unsubscribe, Delete view) are asked in the page too,
 * each right under its row (`InlineConfirm`), never with the browser's prompt.
 */
import { Fragment, useMemo, useState } from 'react';
import toast from 'react-hot-toast';
import { clsx } from 'clsx';
import PageHeader from '@/components/ui/PageHeader';
import Tabs, { type TabItem } from '@/components/ui/Tabs';
import { useTabParam } from '@/components/ui/useTabParam';
import { helpTopicParam } from '@/components/help/helpTopics';
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline';
import Disclosure from '@/components/ui/Disclosure';
import InlineConfirm from '@/components/ui/inPage/InlineConfirm';
import { buildDigestWorkflow } from '@/components/workflow/workflowPresets';
import {
  type DigestContent,
  type DigestScheduleType,
  createSubscription,
  deleteSubscription,
  pauseSubscription,
  previewDigest,
  resumeSubscription,
} from '../../services/digestService';
import { deleteSavedView } from '../../services/savedViewsService';
import { extractErrorMessage } from '@/services/apiErrors';
import { useDigestSavedViews, useDigestSubscriptions } from '@/hooks/useDigestData';
import { useFeatureEnabled } from '@/hooks/useFeatureFlags';
import { useReleases } from '@/hooks/useReleases';
import { useProjectStore, ALL_PROJECTS_ID } from '../../store/projectStore';
import Field from '@/components/ui/Field'
import DigestReleaseSelect from '@/components/digests/DigestReleaseSelect';
import DigestViewPicker, { type ViewSource } from '@/components/digests/DigestViewPicker';
import SubscriptionScopeFix from '@/components/digests/SubscriptionScopeFix';
import DigestViewEditor from '@/components/digests/DigestViewEditor';
import DigestViewRow from '@/components/digests/DigestViewRow';
import DigestScopeBlock from '@/components/digests/DigestScopeBlock';
import {
  isListedView,
  pickableViews,
  releaseOptions,
  subscriptionSummary,
  viewAppliesTo,
} from '@/components/digests/digestScopeModel';

type Tab = 'subscriptions' | 'saved-views' | 'preview';

const TAB_IDS: readonly Tab[] = ['subscriptions', 'saved-views', 'preview'];
const TAB_ITEMS: readonly TabItem<Tab>[] = [
  { id: 'subscriptions', label: 'Digest Subscriptions' },
  { id: 'saved-views', label: 'Saved Views' },
  { id: 'preview', label: 'Preview Digest' },
];
const HELP_TOPIC = helpTopicParam('/settings/digests');

const FORM_LABEL = 'block text-xs text-[var(--color-text-muted)]';
const SELECT = 'bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1';

export const PICK_PROJECT_FOR_RELEASE = 'Pick a project in the top bar to choose a release.';

type RequestError = { message?: string; response?: { data?: { detail?: unknown } } };

export default function DigestsPage() {
  // The tab lives in `?tab=` (the page template's Tabs + useTabParam).
  const [tab, setTab] = useTabParam(TAB_IDS, 'subscriptions');
  const activeProjectId = useProjectStore(s => s.activeProjectId);
  const projectId: string | undefined = activeProjectId === ALL_PROJECTS_ID ? undefined : (activeProjectId ?? undefined);

  // NTF-03: the top bar's own release list (the same SWR entry, never re-asked).
  const { data: releaseData } = useReleases(undefined, { cached: true });
  const releases = useMemo<unknown[]>(
    () => (projectId && Array.isArray(releaseData?.items) ? releaseData.items : []),
    [projectId, releaseData],
  );
  const releaseList = useMemo(() => releaseOptions(releases), [releases]);

  // Subscriptions
  const { subscriptions: subs, isLoading: subsLoading, isError: subsError, mutate: mutateSubs } =
    useDigestSubscriptions(tab === 'subscriptions');
  const [newSubName, setNewSubName] = useState('');
  const [newSubSchedule, setNewSubSchedule] = useState<DigestScheduleType>('WEEKLY');
  // Tier 2 item 12. Gated: when `weekly_retro_digest` is off the dispatch task
  // *silently skips* WEEKLY_RETRO subscriptions, so offering the option would
  // let a user create one that never delivers and never says why.
  const retroEnabled = useFeatureEnabled('weekly_retro_digest');
  const [newSubChannel, setNewSubChannel] = useState<'email' | 'slack' | 'teams'>('email');
  const [newSubTriggerFilter, setNewSubTriggerFilter] = useState<'all' | 'failed_only' | 'degraded_only'>('all');
  const [newSubScopeValue, setNewSubScopeValue] = useState('');
  const [newSubReleaseId, setNewSubReleaseId] = useState('');
  const [newSubViewId, setNewSubViewId] = useState('');
  // US-7.5: attach the self-contained HTML analysis report (email + daily/weekly only).
  const [newSubReportAttachment, setNewSubReportAttachment] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  // If the flag flips off mid-session (30s TTL) the option disappears from the
  // selector — adjust during render rather than in an effect, which this repo
  // lints against.
  if (!retroEnabled && newSubSchedule === 'WEEKLY_RETRO') {
    setNewSubSchedule('WEEKLY');
  }

  // Saved views: asked when their tab or the preview opens, or a picker is clicked.
  const [viewsRequested, setViewsRequested] = useState(false);
  const viewsWanted = tab === 'saved-views' || tab === 'preview' || viewsRequested;
  const { views, isLoading: viewsLoading, isError: viewsError, mutate: mutateViews } =
    useDigestSavedViews(projectId, viewsWanted);
  // The project's report views: what a digest of it may name (and what the pipeline counts).
  const reportViews = useMemo(() => pickableViews(views, projectId), [views, projectId]);
  const listedViews = useMemo(() => views.filter(v => isListedView(v)), [views]);
  const [editingView, setEditingView] = useState<'new' | string | null>(null);
  // The question asked under one row (a subscription's Unsubscribe or a view's
  // Delete); it belongs to its tab, so leaving the tab drops it.
  const [confirming, setConfirming] = useState<{ tab: Tab; id: string } | null>(null);
  if (confirming && confirming.tab !== tab) setConfirming(null);
  const viewSource: ViewSource = {
    requested: viewsWanted,
    views,
    loading: viewsLoading,
    error: viewsError,
    request: () => {
      setViewsRequested(true);
      if (viewsWanted) void mutateViews();
    },
  };

  // Preview
  const [preview, setPreview] = useState<DigestContent | null>(null);
  const [previewPeriod, setPreviewPeriod] = useState<'daily' | 'weekly'>('weekly');
  const [previewViewId, setPreviewViewId] = useState('');
  const [previewing, setPreviewing] = useState(false);

  const loading = tab === 'subscriptions' ? subsLoading : tab === 'saved-views' ? viewsLoading : false;
  const error =
    (tab === 'subscriptions' && subsError) || (tab === 'saved-views' && viewsError) ? 'Could not load data' : null;

  // A choice that no longer exists (another project, a deleted view) is no choice.
  const releaseId = releaseList.some(r => r.id === newSubReleaseId) ? newSubReleaseId : '';
  const viewId = viewAppliesTo(newSubSchedule) && reportViews.some(v => v.id === newSubViewId) ? newSubViewId : '';
  const previewView = reportViews.some(v => v.id === previewViewId) ? previewViewId : '';
  const needsRelease = newSubSchedule === 'PER_RELEASE' && (!projectId || !releaseId);

  const handleCreateSub = async () => {
    if (!newSubName.trim()) { toast.error('Name is required'); return; }
    if (needsRelease) return;
    setCreateError(null);
    try {
      await createSubscription({
        project_id: projectId || null,
        name: newSubName.trim(),
        schedule: newSubSchedule,
        channel: newSubChannel,
        ...(newSubSchedule === 'PER_SUITE' && newSubScopeValue ? { scope_type: 'suite' as const, scope_value: newSubScopeValue.trim() } : {}),
        ...(newSubSchedule === 'PER_RELEASE' ? { scope_type: 'release' as const, scope_value: releaseId } : {}),
        ...(newSubSchedule === 'PER_RUN' ? { trigger_filter: newSubTriggerFilter } : {}),
        ...(newSubChannel === 'email' && (newSubSchedule === 'DAILY' || newSubSchedule === 'WEEKLY')
          ? { report_attachment: newSubReportAttachment }
          : {}),
        ...(viewId ? { saved_view_id: viewId } : {}),
      });
      toast.success('Subscription created');
      setNewSubName('');
      mutateSubs();
    } catch (err: unknown) {
      // The server's own sentence, in the form (a refused scope is a 422 with one fixed text).
      const e = err as RequestError;
      setCreateError(extractErrorMessage(e?.response?.data?.detail, 'Failed to create subscription'));
    }
  };

  const handlePause = async (id: string) => {
    try { await pauseSubscription(id); mutateSubs(); } catch { toast.error('Failed'); }
  };
  const handleResume = async (id: string) => {
    try { await resumeSubscription(id); mutateSubs(); } catch { toast.error('Failed'); }
  };
  const handleDeleteSub = async (id: string) => {
    try { await deleteSubscription(id); toast.success('Unsubscribed'); mutateSubs(); } catch { toast.error('Failed'); }
  };

  const handleDeleteView = async (id: string) => {
    try { await deleteSavedView(id); mutateViews(); } catch { toast.error('Failed'); }
  };

  const handlePreview = async () => {
    setPreviewing(true);
    try {
      setPreview(await (previewView
        ? previewDigest(projectId, previewPeriod, previewView)
        : previewDigest(projectId, previewPeriod)));
    } catch { toast.error('Preview failed'); }
    finally { setPreviewing(false); }
  };

  const workflow = useMemo(
    () => buildDigestWorkflow(subs, reportViews.map(view => ({
      id: view.id,
      name: view.name,
      is_default: view.is_default,
      is_shared: view.is_shared,
    })), preview),
    [subs, reportViews, preview],
  );

  return (
    <div className="space-y-4">
      <PageHeader
        compact
        helpTopic={HELP_TOPIC}
        title="Digests & Saved Views"
        subtitle="Schedule quality digests and save filter views for quick access."
        tabs={<Tabs items={TAB_ITEMS} value={tab} onChange={setTab} ariaLabel="Digest sections" />}
      />

      {error && <div className="bg-[var(--status-failed-bg)]/30 border border-[var(--status-failed-bd)] rounded-lg p-3 text-[var(--status-failed)] text-sm">{error}</div>}

      {loading ? <div className="text-[var(--color-text-muted)] text-center py-8">Loading…</div> : (
        <>
          {/* Subscriptions tab */}
          {tab === 'subscriptions' && (
            <div className="space-y-4">
              {/* Wraps rather than squeezes (CI renders on wider Linux fonts). */}
              <div className="flex flex-wrap items-end gap-2" data-subscription-form="">
                <div className="flex-1 min-w-[220px]">
                  <Field label="Digest Name" labelClassName={FORM_LABEL}>
                    {id => (
                      <input id={id} value={newSubName} onChange={e => setNewSubName(e.target.value)}
                        placeholder="e.g., Weekly QA Summary"
                        className="w-full bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1" />
                    )}
                  </Field>
                </div>
                <div>
                  <Field label="Schedule" labelClassName={FORM_LABEL}>
                    {id => (
                      <select id={id} value={newSubSchedule} onChange={e => setNewSubSchedule(e.target.value as DigestScheduleType)}
                        className={SELECT}>
                        <option value="WEEKLY">Weekly</option>
                        {retroEnabled && <option value="WEEKLY_RETRO">Weekly Retro</option>}
                        <option value="DAILY">Daily</option>
                        <option value="PER_RUN">Per Run</option>
                        <option value="PER_RELEASE">Per Release</option>
                        <option value="PER_SUITE">Per Suite</option>
                      </select>
                    )}
                  </Field>
                </div>
                <div>
                  <Field label="Channel" labelClassName={FORM_LABEL}>
                    {id => (
                      <select id={id} value={newSubChannel} onChange={e => setNewSubChannel(e.target.value as 'email' | 'slack' | 'teams')}
                        className={SELECT}>
                        <option value="email">Email</option>
                        <option value="slack">Slack</option>
                        <option value="teams">Teams</option>
                      </select>
                    )}
                  </Field>
                </div>
                {newSubSchedule === 'PER_RUN' && (
                  <div>
                    <Field label="Trigger" labelClassName={FORM_LABEL}>
                      {id => (
                        <select id={id} value={newSubTriggerFilter} onChange={e => setNewSubTriggerFilter(e.target.value as typeof newSubTriggerFilter)}
                          className={SELECT}>
                          <option value="all">All Runs</option>
                          <option value="failed_only">Failed Only</option>
                          <option value="degraded_only">Degraded Only</option>
                        </select>
                      )}
                    </Field>
                  </div>
                )}
                {newSubSchedule === 'PER_SUITE' && (
                  <div className="w-[240px]">
                    <Field label="Suite Name" labelClassName={FORM_LABEL}>
                      {id => (
                        <input id={id} value={newSubScopeValue} onChange={e => setNewSubScopeValue(e.target.value)}
                          placeholder="e.g., smoke-tests"
                          className="w-full bg-[var(--color-bg-card)] text-[var(--color-text)] rounded px-3 py-2 text-sm mt-1" />
                      )}
                    </Field>
                  </div>
                )}
                {newSubSchedule === 'PER_RELEASE' && (
                  <div className="max-w-[240px]">
                    {projectId ? (
                      <Field label="Release" labelClassName={FORM_LABEL}>
                        {id => (
                          <DigestReleaseSelect id={id} releases={releases} value={releaseId} onChange={setNewSubReleaseId} className="mt-1" />
                        )}
                      </Field>
                    ) : (
                      <p className="text-xs text-[var(--color-text-muted)] pb-2" data-release-needs-project="">{PICK_PROJECT_FOR_RELEASE}</p>
                    )}
                  </div>
                )}
                {viewAppliesTo(newSubSchedule) && projectId && (
                  <div className="max-w-[240px]">
                    <Field label="Saved view (optional)" labelClassName={FORM_LABEL}>
                      {id => (
                        <div className="mt-1">
                          <DigestViewPicker id={id} source={viewSource} projectId={projectId} value={viewId} onChange={setNewSubViewId} />
                        </div>
                      )}
                    </Field>
                  </div>
                )}
                <button
                  onClick={handleCreateSub}
                  disabled={needsRelease}
                  title={needsRelease ? (projectId ? 'Choose a release first' : PICK_PROJECT_FOR_RELEASE) : undefined}
                  className="btn-primary"
                >
                  Subscribe
                </button>
              </div>
              {createError && (
                <p role="alert" className="text-sm text-[var(--status-failed)]" data-subscription-error="">{createError}</p>
              )}

              {newSubSchedule === 'WEEKLY_RETRO' && (
                <p className="text-xs text-[var(--color-text-muted)]">
                  <span className="text-[var(--color-text)]">Weekly Retro</span> is a narrative
                  &ldquo;week in review&rdquo; — what changed, what regressed and what stabilised —
                  rather than the current-state snapshot a Weekly digest sends. Delivered every
                  Monday 07:00 UTC.
                </p>
              )}

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
                <Fragment key={sub.id}>
                  <div data-subscription-row={sub.id} className="bg-[var(--color-bg-secondary)] rounded-lg p-4 flex justify-between items-start gap-4">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[var(--color-text)] font-medium">{sub.name}</span>
                        <span className={clsx('px-2 py-0.5 rounded text-xs', sub.is_paused ? 'bg-[var(--status-skipped-bg)]/40 text-[var(--status-skipped)]' : sub.is_active ? 'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]' : 'bg-[var(--color-bg-card)] text-[var(--color-text-muted)]')}>
                          {sub.is_paused ? 'Paused' : sub.is_active ? 'Active' : 'Inactive'}
                        </span>
                      </div>
                      <p className="text-xs text-[var(--color-text-muted)] mt-1" data-subscription-summary="">
                        {subscriptionSummary(sub)}
                      </p>
                      <SubscriptionScopeFix
                        key={`${sub.id}:${sub.scope_problem ?? ''}:${sub.saved_view_id ?? ''}`}
                        sub={sub}
                        releases={releases}
                        topBarProjectId={projectId ?? null}
                        views={viewSource}
                        onSaved={() => void mutateSubs()}
                      />
                    </div>
                    <div className="flex gap-2 shrink-0">
                      {sub.is_paused ? (
                        <button onClick={() => handleResume(sub.id)} className="px-3 py-1 text-xs bg-[var(--status-passed-bg)]/50 rounded text-[var(--status-passed)] hover:bg-[var(--status-passed-bg)]">Resume</button>
                      ) : (
                        <button onClick={() => handlePause(sub.id)} className="px-3 py-1 text-xs bg-[var(--status-skipped-bg)]/50 rounded text-[var(--status-skipped)] hover:bg-[var(--status-skipped-bg)]">Pause</button>
                      )}
                      <button onClick={() => setConfirming({ tab: 'subscriptions', id: sub.id })} className="px-3 py-1 text-xs bg-[var(--status-failed-bg)]/50 rounded text-[var(--status-failed)] hover:bg-[var(--status-failed-bg)]">Unsubscribe</button>
                    </div>
                  </div>
                  {confirming?.tab === 'subscriptions' && confirming.id === sub.id && (
                    <div data-in-page-row="">
                      <InlineConfirm
                        message="Unsubscribe from this digest?"
                        confirmLabel="Unsubscribe"
                        tone="danger"
                        onConfirm={() => { setConfirming(null); void handleDeleteSub(sub.id); }}
                        onCancel={() => setConfirming(null)}
                      />
                    </div>
                  )}
                </Fragment>
              ))}
            </div>
          )}

          {/* Saved Views tab */}
          {tab === 'saved-views' && (
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-4">
                <p className="text-xs text-[var(--color-text-muted)]">
                  {projectId
                    ? "This project's report views. A Daily or Weekly digest with a view covers only its releases, suites and window."
                    : 'Pick a project in the top bar to save a view for its digests.'}
                </p>
                {projectId && editingView !== 'new' && (
                  <button
                    type="button"
                    onClick={() => setEditingView('new')}
                    className="btn-primary shrink-0"
                  >
                    New view
                  </button>
                )}
              </div>

              {editingView === 'new' && projectId && (
                <DigestViewEditor
                  projectId={projectId}
                  releases={releases}
                  onSaved={() => { setEditingView(null); void mutateViews(); }}
                  onCancel={() => setEditingView(null)}
                />
              )}

              {listedViews.length === 0 && editingView !== 'new' && (
                <div className="text-center py-8 text-[var(--color-text-muted)]">No saved views.</div>
              )}
              {listedViews.map(view => (
                editingView === view.id && projectId ? (
                  <DigestViewEditor
                    key={view.id}
                    view={view}
                    projectId={projectId}
                    releases={releases}
                    onSaved={() => { setEditingView(null); void mutateViews(); }}
                    onCancel={() => setEditingView(null)}
                  />
                ) : (
                  <Fragment key={view.id}>
                    <DigestViewRow
                      view={view}
                      releases={releaseList}
                      onEdit={() => { setConfirming(null); setEditingView(view.id); }}
                      onDelete={() => setConfirming({ tab: 'saved-views', id: view.id })}
                      canEdit={!!projectId}
                    />
                    {confirming?.tab === 'saved-views' && confirming.id === view.id && (
                      <div data-in-page-row="">
                        <InlineConfirm
                          message="Delete this saved view?"
                          confirmLabel="Delete"
                          tone="danger"
                          onConfirm={() => { setConfirming(null); void handleDeleteView(view.id); }}
                          onCancel={() => setConfirming(null)}
                        />
                      </div>
                    )}
                  </Fragment>
                )
              ))}
            </div>
          )}

          {/* Preview tab */}
          {tab === 'preview' && (
            <div className="space-y-4">
              <div className="flex flex-wrap gap-2 items-end">
                <div>
                  <Field label="Period" labelClassName={FORM_LABEL}>
                    {id => (
                      <select id={id} value={previewPeriod} onChange={e => setPreviewPeriod(e.target.value as 'daily' | 'weekly')}
                        className={SELECT}>
                        <option value="weekly">Weekly</option>
                        <option value="daily">Daily</option>
                      </select>
                    )}
                  </Field>
                </div>
                {projectId && (
                  <div className="max-w-[240px]">
                    <Field label="Saved view" labelClassName={FORM_LABEL}>
                      {id => (
                        <div className="mt-1">
                          <DigestViewPicker id={id} source={viewSource} projectId={projectId} value={previewView} onChange={setPreviewViewId} />
                        </div>
                      )}
                    </Field>
                  </div>
                )}
                <button onClick={handlePreview} disabled={previewing}
                  className="btn-secondary disabled:opacity-50">
                  {previewing ? 'Generating...' : 'Generate Preview'}
                </button>
              </div>

              {preview && (
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-6 space-y-4">
                  <h2 className="text-lg font-semibold text-[var(--color-text)]">{preview.period.charAt(0).toUpperCase() + preview.period.slice(1)} Digest — {preview.project_name || 'All Projects'}</h2>
                  <DigestScopeBlock scope={preview.scope} />

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

      {/* The digest stage flow, built from the subscriptions and views above:
          collapsed at the bottom of the page. */}
      <Disclosure title="Pipeline" defaultOpen={false}>
        <WorkflowTimeline
          title="Digest workflow"
          subtitle="Collect signals, resolve saved views, compile the digest, and deliver it to the right channel"
          stages={workflow.stages}
          events={workflow.events}
          stageOrder={workflow.stageOrder}
          showInspector
        />
      </Disclosure>
    </div>
  );
}
