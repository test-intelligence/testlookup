import { useMemo, useState } from 'react';
import useSWR from 'swr';
import toast from 'react-hot-toast';
import { clsx } from 'clsx';
import PageHeader from '@/components/ui/PageHeader';
import WorkflowTimeline from '@/components/workflow/WorkflowTimeline';
import { buildAIEvalWorkflow } from '@/components/workflow/workflowPresets';
import {
  type EvalGateResult,
  createDatasetFromFeedback,
  getReportEvalReadiness,
  getDashboard,
  listReportEvalCycles,
  listBaselines,
  listDatasets,
  runEvaluation,
  runPreReleaseGate,
  seedGoldenDatasets,
} from '../../services/aiEvalService';

type Tab = 'dashboard' | 'datasets' | 'gate';

const GATE_STATUS_STYLES: Record<string, string> = {
  PASS: 'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)] border-[var(--status-passed-bd)]/50',
  FAIL: 'bg-[var(--status-failed-bg)]/40 text-[var(--status-failed)] border-[var(--status-failed-bd)]/50',
  WARN: 'bg-[var(--status-broken-bg)]/40 text-[var(--status-broken)] border-[var(--status-broken-bd)]/50',
  NO_BASELINE: 'bg-[var(--color-bg-card)] text-[var(--color-text-muted)] border-[var(--color-border)]',
};

export default function AIEvalDashboardPage() {
  const [tab, setTab] = useState<Tab>('dashboard');
  const [gateResult, setGateResult] = useState<EvalGateResult | null>(null);
  const [gateRunning, setGateRunning] = useState(false);

  // Data via SWR instead of load-on-tab-change effects. Dashboard + baselines
  // feed the always-visible workflow timeline, so they stay fetched across
  // tabs; the spinner below is gated to the active tab to preserve the prior
  // per-tab loading UX.
  const {
    data: dashboard = null,
    isLoading: dashboardLoading,
    mutate: mutateDashboard,
  } = useSWR(['ai-eval/dashboard', 30], () => getDashboard(30));
  const {
    data: datasets = [],
    isLoading: datasetsLoading,
    mutate: mutateDatasets,
  } = useSWR('ai-eval/datasets', () => listDatasets());
  const { data: reportCycles = [] } = useSWR(
    'ai-eval/report-cycles',
    () => listReportEvalCycles({ limit: 10 }),
  );
  const readinessCorpus = reportCycles[0]?.corpus_version;
  const { data: reportReadiness } = useSWR(
    readinessCorpus ? (['ai-eval/report-cycles/readiness', readinessCorpus] as const) : null,
    ([, corpus]) => getReportEvalReadiness(corpus),
  );
  const { data: baselines = [], isLoading: baselinesLoading } = useSWR(
    'ai-eval/baselines',
    () => listBaselines(),
  );
  const loading =
    (tab === 'dashboard' && dashboardLoading) ||
    (tab === 'datasets' && datasetsLoading) ||
    (tab === 'gate' && baselinesLoading) ||
    gateRunning;

  const workflow = useMemo(
    () => buildAIEvalWorkflow(dashboard, baselines, gateResult),
    [dashboard, baselines, gateResult],
  );
  const latestActionCount = reportCycles[0]?.metrics?.action_count;
  const latestActionResolution = reportCycles[0]?.metrics?.action_resolution_rate;

  const handleGenerateDataset = async () => {
    try {
      await createDatasetFromFeedback();
      toast.success('Dataset generated from feedback');
      mutateDatasets();
    } catch { toast.error('No feedback data available'); }
  };

  const handleSeedGolden = async () => {
    try {
      const result = await seedGoldenDatasets();
      if (result.total > 0) {
        toast.success(`Seeded ${result.total} golden datasets`);
      } else {
        toast.success('Golden datasets already exist');
      }
      mutateDatasets();
    } catch { toast.error('Failed to seed golden datasets'); }
  };

  const handleRunEval = async (datasetId: string) => {
    try {
      await runEvaluation(datasetId);
      toast.success('Evaluation complete');
      mutateDashboard();
    } catch { toast.error('Evaluation failed'); }
  };

  const handleRunGate = async (taskType: string, agentName: string) => {
    setGateRunning(true);
    try {
      const result = await runPreReleaseGate(taskType, agentName);
      setGateResult(result);
      if (result.status === 'PASS') toast.success('Evaluation gate: PASS');
      else if (result.status === 'FAIL') toast.error('Evaluation gate: FAIL');
      else if (result.status === 'WARN') toast.success('Evaluation gate: WARN');
      else toast.success('No baseline configured');
    } catch { toast.error('Gate evaluation failed'); }
    finally { setGateRunning(false); }
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: 'dashboard', label: 'Quality Overview' },
    { key: 'datasets', label: 'Evaluation Datasets' },
    { key: 'gate', label: 'Evaluation Gate' },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="AI Evaluation Dashboard"
        subtitle="Measure AI output quality: precision, recall, agreement, and drift over time."
      />

      <WorkflowTimeline
        title="Evaluation workflow"
        subtitle="Create datasets, run evaluations, compare with a baseline, and keep the gate honest"
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

      {loading ? <div className="text-[var(--color-text-muted)] text-center py-8">Loading...</div> : (
        <>
          {/* Dashboard Tab */}
          {tab === 'dashboard' && dashboard && (
            <div className="space-y-6">
              {dashboard.eval_provenance && (
                <div className={clsx(
                  'rounded-lg border p-4',
                  dashboard.eval_provenance.has_unresolvable_checksums
                    ? 'border-[var(--status-failed-bd)]/50 bg-[var(--status-failed-bg)]/20'
                    : 'border-[var(--status-passed-bd)]/50 bg-[var(--status-passed-bg)]/20',
                )}>
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <h2 className="text-sm font-semibold text-[var(--color-text)]">Runtime eval provenance</h2>
                      <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                        Evaluation manifests for agent runs in the last {dashboard.eval_provenance.window_days} days
                      </p>
                    </div>
                    <span className={clsx(
                      'rounded px-2 py-1 text-xs font-medium',
                      dashboard.eval_provenance.has_unresolvable_checksums
                        ? 'bg-[var(--status-failed-bg)]/40 text-[var(--status-failed)]'
                        : 'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]',
                    )}>
                      {dashboard.eval_provenance.has_unresolvable_checksums ? 'UNRESOLVED' : 'RESOLVED'}
                    </span>
                  </div>
                  <p className={clsx(
                    'mt-3 text-sm',
                    dashboard.eval_provenance.has_unresolvable_checksums
                      ? 'text-[var(--status-failed)]'
                      : 'text-[var(--status-passed)]',
                  )}>
                    {dashboard.eval_provenance.has_unresolvable_checksums
                      ? `${dashboard.eval_provenance.unresolvable_run_count} of ${dashboard.eval_provenance.total_runs} recent runs have a missing or unresolvable eval manifest.`
                      : `All ${dashboard.eval_provenance.total_runs} recent runs resolve to an eval manifest.`}
                  </p>
                  {dashboard.eval_provenance.unresolvable_checksums.length > 0 && (
                    <p className="mt-2 break-all font-mono text-[10px] text-[var(--color-text-muted)]">
                      Unknown: {dashboard.eval_provenance.unresolvable_checksums.join(', ')}
                    </p>
                  )}
                </div>
              )}

              {/* Agreement metrics */}
              {dashboard.agreement && (
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                  <h2 className="text-sm font-semibold text-[var(--color-text)] mb-3">Human-AI Agreement (30d)</h2>
                  {/*
                    Five tiles, not four. The backend returns THREE verdict
                    buckets — correct, partially_correct, incorrect — and
                    computes agreement_rate as
                    (correct + partially_correct * 0.5) / total.

                    Rendering only correct + incorrect left the visible numbers
                    unable to add up: with 60 correct, 20 partial and 20
                    incorrect the page showed "100 total, 60 correct, 20
                    incorrect", and the headline 70% could not be derived from
                    any of them (60/100 = 60%, 60/80 = 75%). The missing bucket
                    was in the API response and the TypeScript type the whole
                    time; only the tile was absent.
                  */}
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-2xl font-bold text-[var(--status-passed)]">
                        {dashboard.agreement.agreement_rate != null ? `${(dashboard.agreement.agreement_rate * 100).toFixed(1)}%` : 'N/A'}
                      </div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Agreement Rate</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--color-text)]">{dashboard.agreement.total_feedback}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Total Feedback</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--status-passed)]">{dashboard.agreement.correct}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Correct</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--status-broken)]">{dashboard.agreement.partially_correct}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Partially Correct</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--status-failed)]">{dashboard.agreement.incorrect}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Incorrect</div>
                    </div>
                  </div>
                </div>
              )}

              {/* Training label health (AI-F1) */}
              {dashboard.label_health && (
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                  <div className="flex items-center gap-3 mb-3">
                    <h2 className="text-sm font-semibold text-[var(--color-text)]">Training Label Health</h2>
                    <span className={clsx('px-2 py-0.5 rounded text-[10px] font-medium', {
                      'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]': dashboard.label_health.ml_maturity === 'human_calibrated',
                      'bg-[var(--status-broken-bg)]/40 text-[var(--status-broken)]': dashboard.label_health.ml_maturity === 'bootstrap_llm_imitating',
                      'bg-[var(--color-bg-card)] text-[var(--color-text-muted)]': dashboard.label_health.ml_maturity === 'not_trained',
                    })}>
                      {dashboard.label_health.ml_maturity === 'human_calibrated' ? 'Human-calibrated'
                        : dashboard.label_health.ml_maturity === 'bootstrap_llm_imitating' ? 'Bootstrap (LLM-imitating)'
                        : 'Not trained'}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className={clsx('text-2xl font-bold', dashboard.label_health.meets_human_label_floor ? 'text-[var(--status-passed)]' : 'text-[var(--status-broken)]')}>
                        {dashboard.label_health.human_label_total}
                      </div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">
                        Human Labels (floor: {dashboard.label_health.human_label_floor})
                      </div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--color-text)]">{dashboard.label_health.human_direct}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Direct Corrections</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--color-text)]">{dashboard.label_health.llm_pseudo_candidates}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">LLM Pseudo-labels</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--color-text)]">
                        {dashboard.label_health.human_share_of_pool != null
                          ? `${(dashboard.label_health.human_share_of_pool * 100).toFixed(1)}%`
                          : 'N/A'}
                      </div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Human Share of Pool</div>
                    </div>
                  </div>
                  {dashboard.label_health.last_trained_composition && (
                    <p className="text-xs text-[var(--color-text-muted)] mt-3">
                      Deployed model trained on{' '}
                      {dashboard.label_health.last_trained_composition.human_label_count} human label
                      {dashboard.label_health.last_trained_composition.human_label_count === 1 ? '' : 's'}
                      {' '}({((1 - (dashboard.label_health.last_trained_composition.fractions?.llm_pseudo ?? 0)) * 100).toFixed(0)}%)
                      {' '}+ {dashboard.label_health.last_trained_composition.counts?.llm_pseudo ?? 0} LLM pseudo-label
                      {(dashboard.label_health.last_trained_composition.counts?.llm_pseudo ?? 0) === 1 ? '' : 's'}
                      {' '}(weight {dashboard.label_health.last_trained_composition.pseudo_weight})
                      {dashboard.label_health.last_trained_composition.cap_exceeded_to_fill_floor
                        ? ' — pseudo-label cap exceeded to reach the minimum training-set size.'
                        : '.'}
                    </p>
                  )}
                  {dashboard.label_health.ml_maturity === 'bootstrap_llm_imitating' && (
                    <p className="text-xs text-[var(--status-broken)]/80 mt-2">
                      Below the human-label floor the ML classifier is trained mostly on the LLM&apos;s
                      own high-confidence verdicts — it imitates the LLM rather than learning from your
                      corrections. Confirm or correct AI verdicts to move it to human-calibrated.
                    </p>
                  )}
                </div>
              )}

              <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <h2 className="text-sm font-semibold text-[var(--color-text)]">Decision Report Evaluation</h2>
                    <p className="text-xs text-[var(--color-text-muted)]">Durable corpus-cycle evidence for pilot gates</p>
                  </div>
                  {reportCycles[0] && (
                    <span className={clsx('px-2 py-1 rounded text-xs font-medium', {
                      'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]': reportCycles[0].status === 'pass',
                      'bg-[var(--status-broken-bg)]/40 text-[var(--status-broken)]': reportCycles[0].status === 'warn',
                      'bg-[var(--status-failed-bg)]/40 text-[var(--status-failed)]': reportCycles[0].status === 'fail',
                    })}>
                      {reportCycles[0].status.toUpperCase()}
                    </span>
                  )}
                </div>
                {reportCycles.length ? (
                  <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--color-text)]">{reportCycles[0].report_count}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Reports in latest cycle</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--color-text)]">{reportCycles[0].consecutive_passes}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Consecutive passes</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--color-text)]">{reportCycles[0].corpus_version}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Corpus version</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--color-text)]">{reportCycles.length}</div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Recorded cycles</div>
                    </div>
                    <div className="bg-[var(--color-bg)]/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--color-text)]">
                        {typeof latestActionCount === 'number' ? latestActionCount : 'N/A'}
                      </div>
                      <div className="text-[10px] text-[var(--color-text-muted)]">Proposed actions</div>
                      {typeof latestActionResolution === 'number' && (
                        <div className="text-[10px] text-[var(--color-text-muted)]">
                          {`${(latestActionResolution * 100).toFixed(0)}% resolved`}
                        </div>
                      )}
                    </div>
                  </div>
                ) : (
                  <p className="text-xs text-[var(--color-text-muted)]">No report evaluation cycles recorded yet.</p>
                )}
                {reportReadiness && (
                  <div className={clsx(
                    'mt-4 rounded border p-3 text-xs',
                    reportReadiness.status === 'ready'
                      ? 'border-[var(--status-passed-bd)]/50 bg-[var(--status-passed-bg)]/20 text-[var(--status-passed)]'
                      : 'border-[var(--status-broken-bd)]/50 bg-[var(--status-broken-bg)]/20 text-[var(--status-broken)]',
                  )}>
                    <div className="font-semibold">
                      Pilot readiness: {reportReadiness.status === 'ready' ? 'READY' : 'NOT READY'}
                    </div>
                    <div className="mt-1">
                      Same-corpus passes: {reportReadiness.consecutive_passes}/{reportReadiness.required_consecutive_passes}
                      {' · '}
                      Qualified-user utility: {reportReadiness.utility_rate == null ? 'not evaluated' : `${(reportReadiness.utility_rate * 100).toFixed(0)}%`}
                    </div>
                    {reportReadiness.reasons.length > 0 && (
                      <div className="mt-1">Reasons: {reportReadiness.reasons.join(', ')}</div>
                    )}
                  </div>
                )}
              </div>

              {/* Drift detection */}
              {dashboard.drift && (
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                  <h2 className="text-sm font-semibold text-[var(--color-text)] mb-3">Quality Drift</h2>
                  <div className="flex items-center gap-4">
                    <span className={clsx('px-3 py-1 rounded text-sm font-medium', {
                      'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]': dashboard.drift.drift_direction === 'improving',
                      'bg-[var(--status-failed-bg)]/40 text-[var(--status-failed)]': dashboard.drift.drift_direction === 'degrading',
                      'bg-[var(--color-bg-card)] text-[var(--color-text-muted)]': dashboard.drift.drift_direction === 'stable',
                    })}>
                      {dashboard.drift.drift_direction === 'improving' ? 'Improving' : dashboard.drift.drift_direction === 'degrading' ? 'Degrading' : 'Stable'}
                    </span>
                    {dashboard.drift.drift != null && (
                      <span className="text-sm text-[var(--color-text-muted)]">
                        Accuracy delta: <span className={dashboard.drift.drift >= 0 ? 'text-[var(--status-passed)]' : 'text-[var(--status-failed)]'}>
                          {dashboard.drift.drift >= 0 ? '+' : ''}{(dashboard.drift.drift * 100).toFixed(1)}%
                        </span>
                      </span>
                    )}
                    <span className="text-xs text-[var(--color-text-muted)]">
                      Current accuracy: {dashboard.drift.current?.accuracy != null ? `${(dashboard.drift.current.accuracy * 100).toFixed(1)}%` : 'N/A'}
                    </span>
                  </div>
                </div>
              )}

              {/* Recent eval runs */}
              {dashboard.recent_eval_runs.length > 0 && (
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                  <h2 className="text-sm font-semibold text-[var(--color-text)] mb-3">Recent Evaluation Runs</h2>
                  <div className="space-y-2">
                    {dashboard.recent_eval_runs.map(run => (
                      <div key={run.id} className="bg-[var(--color-bg)]/50 rounded px-3 py-2 flex justify-between items-center text-sm">
                        <div className="flex items-center gap-3">
                          <span className="text-[var(--color-text-secondary)] font-mono text-xs">{run.model_name}</span>
                          <span className="text-[var(--color-text-muted)] text-xs">{run.task_type}</span>
                        </div>
                        <div className="flex items-center gap-4 text-xs">
                          {run.accuracy != null && <span className="text-[var(--status-passed)]">Acc: {(run.accuracy * 100).toFixed(1)}%</span>}
                          {run.f1_score != null && <span className="text-[var(--color-text)]">F1: {(run.f1_score * 100).toFixed(1)}%</span>}
                          <span className="text-[var(--color-text-muted)]">{run.total_items} items</span>
                          <span className="text-[var(--color-text-muted)]">{new Date(run.evaluated_at).toLocaleDateString()}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Model versions */}
              {dashboard.model_versions.length > 0 && (
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                  <h2 className="text-sm font-semibold text-[var(--color-text)] mb-3">Model Version History</h2>
                  <div className="space-y-2">
                    {dashboard.model_versions.map(v => (
                      <div key={v.id} className="bg-[var(--color-bg)]/50 rounded px-3 py-2 flex justify-between items-center text-sm">
                        <div className="flex items-center gap-2">
                          <span className="text-[var(--color-text-secondary)]">{v.model_name}</span>
                          <span className="text-xs text-[var(--color-text-muted)]">{v.track}</span>
                          <span className={clsx('px-2 py-0.5 rounded text-[10px]', {
                            'bg-[var(--status-passed-bg)]/40 text-[var(--status-passed)]': v.status === 'active',
                            'bg-[var(--status-skipped-bg)]/40 text-[var(--status-skipped)]': v.status === 'training' || v.status === 'evaluating',
                            'bg-[var(--color-bg-card)] text-[var(--color-text-muted)]': v.status === 'retired',
                            'bg-[var(--status-failed-bg)]/40 text-[var(--status-failed)]': v.status === 'failed',
                          })}>{v.status}</span>
                        </div>
                        <span className="text-xs text-[var(--color-text-muted)]">
                          {v.eval_accuracy != null ? `Acc: ${(v.eval_accuracy * 100).toFixed(1)}%` : '—'}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Datasets Tab */}
          {tab === 'datasets' && (
            <div className="space-y-4">
              <div className="flex gap-2">
                <button onClick={handleGenerateDataset}
                  className="px-4 py-2 bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] rounded-lg hover:bg-[var(--color-bg-hover)] text-sm">
                  Generate from Feedback
                </button>
                <button onClick={handleSeedGolden}
                  className="px-4 py-2 bg-[var(--status-passed-bg)] text-[var(--color-text)] rounded-lg hover:bg-[var(--status-passed-bg)] text-sm">
                  Seed Golden Datasets
                </button>
              </div>

              {datasets.length === 0 && <div className="text-center py-8 text-[var(--color-text-muted)]">No evaluation datasets. Generate one from human feedback or seed golden datasets.</div>}
              {datasets.map(ds => (
                <div key={ds.id} className="bg-[var(--color-bg-secondary)] rounded-lg p-4 flex justify-between items-center">
                  <div>
                    <span className="text-[var(--color-text)] font-medium">{ds.name}</span>
                    <p className="text-xs text-[var(--color-text-muted)] mt-1">{ds.task_type} · {ds.item_count} items · Created {new Date(ds.created_at).toLocaleDateString()}</p>
                  </div>
                  <button onClick={() => handleRunEval(ds.id)}
                    className="px-3 py-1.5 bg-[var(--status-flaky-bg)] text-[var(--color-text)] rounded text-xs hover:bg-[var(--status-flaky-bg)]">
                    Run Evaluation
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Evaluation Gate Tab */}
          {tab === 'gate' && (
            <div className="space-y-6">
              {/* Gate runner */}
              <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                <h2 className="text-sm font-semibold text-[var(--color-text)] mb-3">Run Pre-Release Gate</h2>
                <p className="text-xs text-[var(--color-text-muted)] mb-4">
                  Evaluate agent quality against baselines. Prompt, model, or routing changes should not ship if the gate returns FAIL.
                </p>
                <div className="flex flex-wrap gap-2">
                  {[
                    { task: 'classification', agent: 'AnalysisAgent', label: 'Classification' },
                    { task: 'root_cause', agent: 'AnalysisAgent', label: 'Root Cause' },
                    { task: 'duplicate_detection', agent: 'ClusterAgent', label: 'Duplicate Detection' },
                    { task: 'release_decision', agent: 'ReleaseRiskAgent', label: 'Release Decision' },
                  ].map(g => (
                    <button key={g.task} onClick={() => handleRunGate(g.task, g.agent)}
                      className="px-4 py-2 bg-[var(--color-accent-muted)] text-[var(--color-text)] rounded-lg hover:bg-[var(--color-accent-muted)] text-sm">
                      Gate: {g.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Gate result */}
              {gateResult && (
                <div className={clsx('rounded-lg border p-4', GATE_STATUS_STYLES[gateResult.status] || 'bg-[var(--color-bg-secondary)]')}>
                  <div className="flex items-center justify-between mb-3">
                    <h2 className="text-sm font-semibold">
                      Gate Result: {gateResult.status}
                    </h2>
                    <span className="text-xs text-[var(--color-text-muted)]">
                      {gateResult.agent_name} / {gateResult.task_type}
                    </span>
                  </div>

                  {/* Current metrics */}
                  {gateResult.current_metrics && (
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
                      {[
                        { label: 'Accuracy', value: gateResult.current_metrics.accuracy },
                        { label: 'Precision', value: gateResult.current_metrics.precision },
                        { label: 'Recall', value: gateResult.current_metrics.recall },
                        { label: 'F1 Score', value: gateResult.current_metrics.f1_score },
                      ].map(m => (
                        <div key={m.label} className="bg-[var(--color-bg)]/50 rounded p-2 text-center">
                          <div className="text-lg font-bold text-[var(--color-text)]">
                            {m.value != null ? `${(m.value * 100).toFixed(1)}%` : 'N/A'}
                          </div>
                          <div className="text-[10px] text-[var(--color-text-muted)]">{m.label}</div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Rule results */}
                  <div className="space-y-1">
                    <h3 className="text-xs font-medium text-[var(--color-text-muted)] mb-1">Rules</h3>
                    {gateResult.rule_results.map((rule, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className={rule.passed ? 'text-[var(--status-passed)]' : 'text-[var(--status-failed)]'}>
                          {rule.passed ? 'PASS' : 'FAIL'}
                        </span>
                        <span className="text-[var(--color-text-muted)]">{rule.rule}</span>
                        <span className="text-[var(--color-text-muted)]">{rule.detail}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Active baselines */}
              <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                <h2 className="text-sm font-semibold text-[var(--color-text)] mb-3">Active Baselines</h2>
                {baselines.length === 0 && (
                  <p className="text-xs text-[var(--color-text-muted)]">No baselines configured. Set a baseline from the API to enable gate comparisons.</p>
                )}
                {baselines.map(b => (
                  <div key={b.id} className="bg-[var(--color-bg)]/50 rounded px-3 py-2 mb-2 flex justify-between items-center text-sm">
                    <div>
                      <span className="text-[var(--color-text-secondary)]">{b.agent_name}</span>
                      <span className="text-[var(--color-text-muted)] text-xs ml-2">{b.task_type} / {b.prompt_version}</span>
                    </div>
                    <div className="flex gap-3 text-xs text-[var(--color-text-muted)]">
                      <span>Acc: {b.baseline_accuracy != null ? `${(b.baseline_accuracy * 100).toFixed(1)}%` : 'N/A'}</span>
                      <span>Min: {(b.min_accuracy * 100).toFixed(0)}%</span>
                      <span>Max drop: {b.max_regression_pct.toFixed(1)}%</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
