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
  getDashboard,
  listBaselines,
  listDatasets,
  runEvaluation,
  runPreReleaseGate,
  seedGoldenDatasets,
} from '../../services/aiEvalService';

type Tab = 'dashboard' | 'datasets' | 'gate';

const GATE_STATUS_STYLES: Record<string, string> = {
  PASS: 'bg-green-900/40 text-green-400 border-green-700/50',
  FAIL: 'bg-red-900/40 text-red-400 border-red-700/50',
  WARN: 'bg-amber-900/40 text-amber-400 border-amber-700/50',
  NO_BASELINE: 'bg-gray-700 text-[var(--color-text-muted)] border-gray-600',
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
          {/* Dashboard Tab */}
          {tab === 'dashboard' && dashboard && (
            <div className="space-y-6">
              {/* Agreement metrics */}
              {dashboard.agreement && (
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                  <h2 className="text-sm font-semibold text-neutral-200 mb-3">Human-AI Agreement (30d)</h2>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                      <div className="text-2xl font-bold text-green-400">
                        {dashboard.agreement.agreement_rate != null ? `${(dashboard.agreement.agreement_rate * 100).toFixed(1)}%` : 'N/A'}
                      </div>
                      <div className="text-[10px] text-gray-500">Agreement Rate</div>
                    </div>
                    <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-[var(--color-text)]">{dashboard.agreement.total_feedback}</div>
                      <div className="text-[10px] text-gray-500">Total Feedback</div>
                    </div>
                    <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-green-400">{dashboard.agreement.correct}</div>
                      <div className="text-[10px] text-gray-500">Correct</div>
                    </div>
                    <div className="bg-gray-900/50 rounded-lg p-3 text-center">
                      <div className="text-xl font-bold text-red-400">{dashboard.agreement.incorrect}</div>
                      <div className="text-[10px] text-gray-500">Incorrect</div>
                    </div>
                  </div>
                </div>
              )}

              {/* Drift detection */}
              {dashboard.drift && (
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                  <h2 className="text-sm font-semibold text-neutral-200 mb-3">Quality Drift</h2>
                  <div className="flex items-center gap-4">
                    <span className={clsx('px-3 py-1 rounded text-sm font-medium', {
                      'bg-green-900/40 text-green-400': dashboard.drift.drift_direction === 'improving',
                      'bg-red-900/40 text-red-400': dashboard.drift.drift_direction === 'degrading',
                      'bg-gray-700 text-[var(--color-text-muted)]': dashboard.drift.drift_direction === 'stable',
                    })}>
                      {dashboard.drift.drift_direction === 'improving' ? 'Improving' : dashboard.drift.drift_direction === 'degrading' ? 'Degrading' : 'Stable'}
                    </span>
                    {dashboard.drift.drift != null && (
                      <span className="text-sm text-[var(--color-text-muted)]">
                        Accuracy delta: <span className={dashboard.drift.drift >= 0 ? 'text-green-400' : 'text-red-400'}>
                          {dashboard.drift.drift >= 0 ? '+' : ''}{(dashboard.drift.drift * 100).toFixed(1)}%
                        </span>
                      </span>
                    )}
                    <span className="text-xs text-gray-600">
                      Current accuracy: {dashboard.drift.current?.accuracy != null ? `${(dashboard.drift.current.accuracy * 100).toFixed(1)}%` : 'N/A'}
                    </span>
                  </div>
                </div>
              )}

              {/* Recent eval runs */}
              {dashboard.recent_eval_runs.length > 0 && (
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                  <h2 className="text-sm font-semibold text-neutral-200 mb-3">Recent Evaluation Runs</h2>
                  <div className="space-y-2">
                    {dashboard.recent_eval_runs.map(run => (
                      <div key={run.id} className="bg-gray-900/50 rounded px-3 py-2 flex justify-between items-center text-sm">
                        <div className="flex items-center gap-3">
                          <span className="text-gray-300 font-mono text-xs">{run.model_name}</span>
                          <span className="text-gray-500 text-xs">{run.task_type}</span>
                        </div>
                        <div className="flex items-center gap-4 text-xs">
                          {run.accuracy != null && <span className="text-green-400">Acc: {(run.accuracy * 100).toFixed(1)}%</span>}
                          {run.f1_score != null && <span className="text-[var(--color-text)]">F1: {(run.f1_score * 100).toFixed(1)}%</span>}
                          <span className="text-gray-500">{run.total_items} items</span>
                          <span className="text-gray-600">{new Date(run.evaluated_at).toLocaleDateString()}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Model versions */}
              {dashboard.model_versions.length > 0 && (
                <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                  <h2 className="text-sm font-semibold text-neutral-200 mb-3">Model Version History</h2>
                  <div className="space-y-2">
                    {dashboard.model_versions.map(v => (
                      <div key={v.id} className="bg-gray-900/50 rounded px-3 py-2 flex justify-between items-center text-sm">
                        <div className="flex items-center gap-2">
                          <span className="text-gray-300">{v.model_name}</span>
                          <span className="text-xs text-gray-600">{v.track}</span>
                          <span className={clsx('px-2 py-0.5 rounded text-[10px]', {
                            'bg-green-900/40 text-green-400': v.status === 'active',
                            'bg-yellow-900/40 text-yellow-400': v.status === 'training' || v.status === 'evaluating',
                            'bg-gray-700 text-[var(--color-text-muted)]': v.status === 'retired',
                            'bg-red-900/40 text-red-400': v.status === 'failed',
                          })}>{v.status}</span>
                        </div>
                        <span className="text-xs text-gray-500">
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
                  className="px-4 py-2 bg-[var(--color-btn-primary-bg)] text-[var(--color-btn-primary-text)] rounded-lg hover:bg-neutral-200 text-sm">
                  Generate from Feedback
                </button>
                <button onClick={handleSeedGolden}
                  className="px-4 py-2 bg-emerald-600 text-[var(--color-text)] rounded-lg hover:bg-emerald-700 text-sm">
                  Seed Golden Datasets
                </button>
              </div>

              {datasets.length === 0 && <div className="text-center py-8 text-gray-500">No evaluation datasets. Generate one from human feedback or seed golden datasets.</div>}
              {datasets.map(ds => (
                <div key={ds.id} className="bg-[var(--color-bg-secondary)] rounded-lg p-4 flex justify-between items-center">
                  <div>
                    <span className="text-gray-100 font-medium">{ds.name}</span>
                    <p className="text-xs text-gray-500 mt-1">{ds.task_type} · {ds.item_count} items · Created {new Date(ds.created_at).toLocaleDateString()}</p>
                  </div>
                  <button onClick={() => handleRunEval(ds.id)}
                    className="px-3 py-1.5 bg-purple-600 text-[var(--color-text)] rounded text-xs hover:bg-purple-700">
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
                <h2 className="text-sm font-semibold text-neutral-200 mb-3">Run Pre-Release Gate</h2>
                <p className="text-xs text-gray-500 mb-4">
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
                      className="px-4 py-2 bg-indigo-600 text-[var(--color-text)] rounded-lg hover:bg-indigo-700 text-sm">
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
                    <span className="text-xs text-gray-500">
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
                        <div key={m.label} className="bg-gray-900/50 rounded p-2 text-center">
                          <div className="text-lg font-bold text-neutral-200">
                            {m.value != null ? `${(m.value * 100).toFixed(1)}%` : 'N/A'}
                          </div>
                          <div className="text-[10px] text-gray-500">{m.label}</div>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Rule results */}
                  <div className="space-y-1">
                    <h3 className="text-xs font-medium text-[var(--color-text-muted)] mb-1">Rules</h3>
                    {gateResult.rule_results.map((rule, i) => (
                      <div key={i} className="flex items-center gap-2 text-xs">
                        <span className={rule.passed ? 'text-green-400' : 'text-red-400'}>
                          {rule.passed ? 'PASS' : 'FAIL'}
                        </span>
                        <span className="text-[var(--color-text-muted)]">{rule.rule}</span>
                        <span className="text-gray-600">{rule.detail}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Active baselines */}
              <div className="bg-[var(--color-bg-secondary)] rounded-lg p-4">
                <h2 className="text-sm font-semibold text-neutral-200 mb-3">Active Baselines</h2>
                {baselines.length === 0 && (
                  <p className="text-xs text-gray-500">No baselines configured. Set a baseline from the API to enable gate comparisons.</p>
                )}
                {baselines.map(b => (
                  <div key={b.id} className="bg-gray-900/50 rounded px-3 py-2 mb-2 flex justify-between items-center text-sm">
                    <div>
                      <span className="text-gray-300">{b.agent_name}</span>
                      <span className="text-gray-600 text-xs ml-2">{b.task_type} / {b.prompt_version}</span>
                    </div>
                    <div className="flex gap-3 text-xs text-gray-500">
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
