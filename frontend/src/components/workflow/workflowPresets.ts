import type { WorkflowEventNode, WorkflowStageNode } from './WorkflowTimeline'
import type { ReleaseCouncilDecision } from '@/services/releaseCouncilService'
import type { ReleaseDetail, ReleasePhase } from '@/types/releases'
import type { OnboardingStatus, OnboardingStep } from '@/services/onboardingService'
import type { DigestContent, DigestSubscription } from '@/services/digestService'
import type { IntegrationStatus, ProbeHistoryEntry } from '@/services/integrationHealthService'
import type { AIQualityDashboard, EvalBaseline, EvalGateResult } from '@/services/aiEvalService'
import type { AuditEvent, TenantObservability } from '@/services/auditDashboardService'
import type { SearchResponse } from '@/types/search'
import type { DashboardSummary, DashboardMetricValue } from '@/types/analytics'
import type { TrendResponse } from '@/types/metrics'
import type { CoverageSummary, CoverageSuite, DefectItem } from '@/types/analytics'
import type { ValueMetrics } from '@/services/valueMetricsService'
import type { FailureCluster, DeepFinding } from '@/types/deep-investigation'

type DashboardSummaryLike = DashboardSummary | null | undefined

type TrendResponseLike = TrendResponse | null | undefined

type CoverageSummaryLike = CoverageSummary | Partial<CoverageSummary> | null | undefined

type CoverageSuiteLike = CoverageSuite | {
  suite_name: string
  unique_tests: number
  passed: number
  failed: number
  skipped: number
  pass_rate: number
  project_name?: string
}

type DefectLike = DefectItem | {
  id: string
  jira_ticket_id?: string
  jira_ticket_url?: string
  jira_status?: string
  failure_category?: string
  resolution_status: string
  ai_confidence_score?: number
  created_at: string
  resolved_at?: string
  test_name: string
  suite_name?: string
}

type ValueMetricsLike = ValueMetrics | null | undefined

type DeepFindingLike = DeepFinding | null | undefined

type RunLike = {
  id: string
  status: string
  failed_tests: number
  total_tests: number
  build_number?: number
  branch?: string
  project_name?: string
  created_at: string
}

type BaseStageInput = Partial<WorkflowStageNode> & {
  stage_name: string
  status: WorkflowStageNode['status']
  label: string
  description: string
}

function toStage(input: BaseStageInput): WorkflowStageNode {
  return {
    stage_name: input.stage_name,
    status: input.status,
    label: input.label,
    description: input.description,
    started_at: input.started_at ?? null,
    completed_at: input.completed_at ?? null,
    result_data: input.result_data ?? null,
    error: input.error ?? null,
    skipped_reason: input.skipped_reason ?? null,
    execution_path: input.execution_path ?? null,
    fallback_used: input.fallback_used ?? null,
    input_tokens: input.input_tokens ?? null,
    output_tokens: input.output_tokens ?? null,
    total_tokens: input.total_tokens ?? null,
    llm_calls_count: input.llm_calls_count ?? null,
    cost_usd: input.cost_usd ?? null,
    error_category: input.error_category ?? null,
    confidence_score: input.confidence_score ?? null,
    evidence_count: input.evidence_count ?? null,
    route_rationale: input.route_rationale ?? null,
  }
}

function buildEventsFromLabels(labels: Array<{ event_type: string; stage_name: string; detail?: Record<string, unknown> }>): WorkflowEventNode[] {
  const now = Date.now()
  return labels.map((item, index) => ({
    event_type: item.event_type,
    stage_name: item.stage_name,
    timestamp: new Date(now - (labels.length - index - 1) * 45_000).toISOString(),
    test_case_id: null,
    detail: item.detail ?? {},
  }))
}

export function buildReleaseGateWorkflow(decision: ReleaseCouncilDecision): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const stages = [
    toStage({
      stage_name: 'policy_evaluation',
      status: decision.policy_level || (decision.rule_evaluations?.length ?? 0) > 0 ? 'completed' : 'pending',
      label: 'Policy Evaluation',
      description: 'Apply tenant, project, and team release rules',
      result_data: {
        policy_level: decision.policy_level,
        policy_version: decision.policy_version,
        rule_count: decision.rule_evaluations?.length ?? 0,
      },
      confidence_score: decision.dimension_scores?.length ? Math.round(decision.risk_score) : null,
      evidence_count: decision.rule_evaluations?.length ?? 0,
      route_rationale: decision.reasoning ?? null,
    }),
    toStage({
      stage_name: 'cluster_review',
      status: (decision.cluster_insights?.length ?? 0) > 0 ? 'completed' : 'skipped',
      label: 'Cluster Review',
      description: 'Review the failure clusters driving the recommendation',
      result_data: {
        cluster_count: decision.cluster_insights?.length ?? 0,
        blocking_issues: decision.blocking_issues?.length ?? 0,
      },
      skipped_reason: (decision.cluster_insights?.length ?? 0) === 0 ? 'No linked failure clusters were present for this gate' : null,
      evidence_count: decision.cluster_insights?.length ?? 0,
      route_rationale: decision.blocking_issues?.length ? 'Blocking issues were elevated into the gate' : null,
    }),
    toStage({
      stage_name: 'evidence_synthesis',
      status: decision.reasoning || decision.baseline_diff || (decision.dimension_scores?.length ?? 0) > 0 ? 'completed' : 'pending',
      label: 'Evidence Synthesis',
      description: 'Combine baseline drift, evidence, and release inputs',
      result_data: {
        has_baseline_diff: !!decision.baseline_diff,
        dimension_scores: decision.dimension_scores?.length ?? 0,
        open_defects: decision.open_defects_by_component?.length ?? 0,
      },
      confidence_score: decision.dimension_scores?.length ? decision.dimension_scores[0]?.score : null,
      evidence_count: (decision.baseline_diff?.new_failures?.length ?? 0) + (decision.open_defects_by_component?.length ?? 0),
      route_rationale: decision.reasoning ?? null,
    }),
    toStage({
      stage_name: 'release_decision',
      status: 'completed',
      label: 'Release Decision',
      description: 'Produce the final go / conditional go / no-go call',
      result_data: {
        recommendation: decision.recommendation,
        risk_score: decision.risk_score,
        overridden: !!decision.human_override,
      },
      confidence_score: Math.max(0, Math.min(100, decision.risk_score)),
      evidence_count: decision.override_audit?.length ?? 0,
      route_rationale: decision.human_override ?? decision.reasoning ?? null,
    }),
  ]

  const events = buildEventsFromLabels([
    { event_type: 'stage_started', stage_name: 'policy_evaluation', detail: { policy_level: decision.policy_level, policy_version: decision.policy_version } },
    { event_type: 'stage_completed', stage_name: 'policy_evaluation', detail: { rules: decision.rule_evaluations?.length ?? 0 } },
    { event_type: 'stage_completed', stage_name: 'cluster_review', detail: { clusters: decision.cluster_insights?.length ?? 0 } },
    { event_type: 'stage_completed', stage_name: 'evidence_synthesis', detail: { baseline: !!decision.baseline_diff } },
    { event_type: 'stage_completed', stage_name: 'release_decision', detail: { recommendation: decision.recommendation, risk_score: decision.risk_score } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildReleaseWorkflow(detail: ReleaseDetail): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const orderedPhases = [...(detail.phases ?? [])].sort((a, b) => a.order_index - b.order_index)
  const stages = orderedPhases.map((phase: ReleasePhase) => toStage({
    stage_name: `release_phase:${phase.id}`,
    status: phase.status === 'in_progress' ? 'running' : phase.status === 'completed' ? 'completed' : phase.status === 'skipped' ? 'skipped' : 'pending',
    label: phase.name,
    description: phase.description ?? `Release phase · ${phase.phase_type}`,
    started_at: phase.actual_start,
    completed_at: phase.actual_end,
    result_data: {
      planned_start: phase.planned_start,
      planned_end: phase.planned_end,
      exit_criteria: phase.exit_criteria,
      notes: phase.notes,
    },
    skipped_reason: phase.status === 'skipped' ? phase.notes ?? 'Phase was marked as skipped' : null,
    route_rationale: phase.exit_criteria ? 'Phase exit criteria are tracked here' : null,
  }))

  const events = buildEventsFromLabels([
    ...orderedPhases.map(phase => ({
      event_type: phase.status === 'skipped' ? 'stage_skipped' : phase.status === 'completed' ? 'stage_completed' : phase.status === 'in_progress' ? 'stage_started' : 'stage_started',
      stage_name: `release_phase:${phase.id}`,
      detail: { phase: phase.name, status: phase.status },
    })),
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildOnboardingWorkflow(status: OnboardingStatus | null): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const steps = status?.steps ?? []
  const stages = steps.map((step: OnboardingStep) => toStage({
    stage_name: step.key,
    status: step.status === 'completed' ? 'completed' : step.status === 'skipped' ? 'skipped' : 'pending',
    label: step.label,
    description: step.description,
    completed_at: step.completed_at,
    result_data: {
      completed_at: step.completed_at,
      status: step.status,
    },
    skipped_reason: step.status === 'skipped' ? 'User chose to skip this activation step' : null,
    route_rationale: step.status === 'completed' ? 'This activation step is now unlocked in the product' : null,
    evidence_count: step.status === 'completed' ? 1 : 0,
  }))

  const events = buildEventsFromLabels(
    steps.map(step => ({
      event_type: step.status === 'completed' ? 'stage_completed' : step.status === 'skipped' ? 'stage_skipped' : 'stage_started',
      stage_name: step.key,
      detail: { label: step.label, completed_at: step.completed_at },
    })),
  )

  return { stages, events, stageOrder: steps.map(step => step.key) }
}

export function buildDigestWorkflow(
  subs: DigestSubscription[],
  views: Array<{ id: string; name: string; is_default: boolean; is_shared: boolean }> = [],
  preview: DigestContent | null,
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const activeSubscriptions = subs.filter(sub => sub.is_active && !sub.is_paused)
  const stages = [
    toStage({
      stage_name: 'signal_collection',
      status: activeSubscriptions.length > 0 ? 'completed' : 'pending',
      label: 'Signal Collection',
      description: 'Capture runs, blockers, and quality trends',
      result_data: { subscriptions: activeSubscriptions.length, total_subscriptions: subs.length },
      evidence_count: activeSubscriptions.length,
    }),
    toStage({
      stage_name: 'view_resolution',
      status: views.length > 0 ? 'completed' : 'pending',
      label: 'Saved View Resolution',
      description: 'Apply saved filters and audience-specific views',
      result_data: { saved_views: views.length, shared_views: views.filter(v => v.is_shared).length, default_views: views.filter(v => v.is_default).length },
      evidence_count: views.length,
    }),
    toStage({
      stage_name: 'digest_compilation',
      status: preview ? 'completed' : 'pending',
      label: 'Digest Compilation',
      description: 'Rank blockers and summarize the week or day',
      result_data: preview ? {
        new_regressions: preview.new_regressions,
        top_clusters: preview.top_clusters.length,
        flaky_test_count: preview.flaky_test_count,
      } : null,
      confidence_score: preview ? 90 : 45,
      evidence_count: preview ? preview.top_clusters.length + preview.top_blockers.length : 0,
      route_rationale: preview ? 'Preview output is assembled from the current project scope' : 'Preview not yet generated',
    }),
    toStage({
      stage_name: 'delivery',
      status: activeSubscriptions.length > 0 ? 'completed' : 'pending',
      label: 'Delivery',
      description: 'Ship digests to email, Slack, or Teams',
      result_data: { channels: [...new Set(activeSubscriptions.map(sub => sub.channel))] },
      evidence_count: activeSubscriptions.length,
    }),
  ]

  const events = buildEventsFromLabels([
    { event_type: 'stage_completed', stage_name: 'signal_collection', detail: { subscriptions: activeSubscriptions.length } },
    { event_type: 'stage_completed', stage_name: 'view_resolution', detail: { saved_views: views.length } },
    { event_type: preview ? 'stage_completed' : 'stage_started', stage_name: 'digest_compilation', detail: { preview: !!preview } },
    { event_type: 'stage_completed', stage_name: 'delivery', detail: { channels: activeSubscriptions.map(sub => sub.channel) } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildIntegrationHealthWorkflow(
  statuses: IntegrationStatus[],
  history: ProbeHistoryEntry[],
  selectedProvider: string,
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const visibleStatuses = statuses.slice(0, 4)
  const stages = [
    ...visibleStatuses.map((status) => toStage({
      stage_name: `probe:${status.provider}`,
      status: status.status === 'healthy' ? 'completed' : status.status === 'degraded' ? 'running' : 'failed',
      label: status.provider,
      description: 'Active health probe result',
      result_data: {
        response_ms: status.response_ms,
        message: status.message,
        consecutive_failures: status.consecutive_failures,
      },
      confidence_score: status.status === 'healthy' ? 95 : status.status === 'degraded' ? 65 : 25,
      evidence_count: status.last_success_at ? 1 : 0,
      route_rationale: status.last_checked_at ? 'Fresh probe data is available' : 'Probe has not run yet',
    })),
    toStage({
      stage_name: 'health_rollup',
      status: statuses.some(s => s.status === 'down' || s.status === 'auth_error') ? 'failed' : statuses.some(s => s.status === 'degraded') ? 'running' : 'completed',
      label: 'Health Rollup',
      description: 'Aggregate provider status and recent probe history',
      result_data: {
        providers: statuses.length,
        selected_provider: selectedProvider || null,
        history_count: history.length,
      },
      evidence_count: history.length,
    }),
  ]

  const events = buildEventsFromLabels([
    ...visibleStatuses.map(status => ({
      event_type: 'stage_completed',
      stage_name: `probe:${status.provider}`,
      detail: { provider: status.provider, status: status.status },
    })),
    ...(selectedProvider && history.length > 0
      ? history.slice(0, 5).map(entry => ({
          event_type: entry.status === 'healthy' ? 'stage_completed' : 'stage_failed',
          stage_name: `history:${selectedProvider}`,
          detail: { response_ms: entry.response_ms, auth_valid: entry.auth_valid, payload_valid: entry.payload_valid },
        }))
      : []),
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildAIEvalWorkflow(
  dashboard: AIQualityDashboard | null,
  baselines: EvalBaseline[],
  gateResult: EvalGateResult | null,
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const stages = [
    toStage({
      stage_name: 'dataset_refresh',
      status: dashboard?.recent_eval_runs.length ? 'completed' : 'pending',
      label: 'Dataset Refresh',
      description: 'Ingest feedback and create evaluation datasets',
      result_data: {
        datasets: baselines.length,
        recent_runs: dashboard?.recent_eval_runs.length ?? 0,
      },
      evidence_count: baselines.length,
    }),
    toStage({
      stage_name: 'quality_evaluation',
      status: dashboard?.recent_eval_runs.length ? 'completed' : 'pending',
      label: 'Quality Evaluation',
      description: 'Measure accuracy, F1, agreement, and drift',
      result_data: {
        agreement_rate: dashboard?.agreement?.agreement_rate ?? null,
        drift: dashboard?.drift?.drift ?? null,
      },
      confidence_score: dashboard?.drift?.current?.accuracy != null ? Math.round((dashboard?.drift?.current?.accuracy ?? 0) * 100) : null,
      evidence_count: dashboard?.agreement?.total_feedback ?? 0,
    }),
    toStage({
      stage_name: 'gate_review',
      status: gateResult ? (gateResult.status === 'PASS' ? 'completed' : gateResult.status === 'WARN' ? 'running' : 'failed') : 'pending',
      label: 'Gate Review',
      description: 'Compare the model against the release baseline',
      result_data: gateResult ? {
        status: gateResult.status,
        task_type: gateResult.task_type,
        agent_name: gateResult.agent_name,
      } : null,
      confidence_score: gateResult?.current_metrics?.accuracy != null ? Math.round((gateResult.current_metrics.accuracy ?? 0) * 100) : null,
      evidence_count: gateResult?.rule_results?.length ?? 0,
      route_rationale: gateResult?.baseline_metrics ? 'Release gate is anchored to a baseline' : 'No baseline has been selected yet',
    }),
    toStage({
      stage_name: 'model_governance',
      status: dashboard?.model_versions.length ? 'completed' : 'pending',
      label: 'Model Governance',
      description: 'Track prompt and model versions over time',
      result_data: {
        model_versions: dashboard?.model_versions.length ?? 0,
        feedback_summary: !!dashboard?.feedback_summary,
      },
      evidence_count: dashboard?.model_versions.length ?? 0,
    }),
  ]

  const events = buildEventsFromLabels([
    { event_type: 'stage_completed', stage_name: 'dataset_refresh', detail: { datasets: baselines.length } },
    { event_type: 'stage_completed', stage_name: 'quality_evaluation', detail: { recent_runs: dashboard?.recent_eval_runs.length ?? 0 } },
    { event_type: gateResult ? 'stage_completed' : 'stage_started', stage_name: 'gate_review', detail: { status: gateResult?.status ?? 'pending' } },
    { event_type: 'stage_completed', stage_name: 'model_governance', detail: { model_versions: dashboard?.model_versions.length ?? 0 } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildAuditWorkflow(obs: TenantObservability | null, events: AuditEvent[]): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const safeEvents = events.filter(Boolean)
  const stages = [
    toStage({
      stage_name: 'access_events',
      status: safeEvents.some(ev => ev.source === 'access') ? 'completed' : 'pending',
      label: 'Access Events',
      description: 'Track sign-ins, permission changes, and user access',
      result_data: { events: safeEvents.filter(ev => ev.source === 'access').length },
      evidence_count: safeEvents.filter(ev => ev.source === 'access').length,
    }),
    toStage({
      stage_name: 'configuration_changes',
      status: safeEvents.some(ev => ev.source === 'settings' || ev.source === 'identity') ? 'completed' : 'pending',
      label: 'Configuration Changes',
      description: 'Monitor settings, identity, and integration updates',
      result_data: { events: safeEvents.filter(ev => ev.source === 'settings' || ev.source === 'identity').length },
      evidence_count: safeEvents.filter(ev => ev.source === 'settings' || ev.source === 'identity').length,
    }),
    toStage({
      stage_name: 'quality_rollup',
      status: obs ? 'completed' : 'pending',
      label: 'Quality Rollup',
      description: 'Summarize runs, AI analyses, and release decisions',
      result_data: obs ? {
        total_runs: obs.total_runs,
        failed_runs: obs.failed_runs,
        ai_analyses_count: obs.ai_analyses_count,
      } : null,
      evidence_count: obs?.audit_events_count ?? 0,
    }),
    toStage({
      stage_name: 'observability_export',
      status: obs ? 'completed' : 'pending',
      label: 'Observability Export',
      description: 'Prepare tenant-scoped telemetry and audit exports',
      result_data: obs ? {
        total_tests: obs.total_tests,
        period_days: obs.period_days,
      } : null,
      evidence_count: obs?.audit_events_count ?? 0,
    }),
  ]

  const timelineEvents = buildEventsFromLabels([
    ...safeEvents.slice(0, 8).map(ev => ({
      event_type: 'tool_invoked',
      stage_name: ev.source,
      detail: {
        action: ev.action,
        actor: ev.actor_name,
        success: ev.success ?? true,
      },
    })),
    ...(obs
      ? [
          { event_type: 'stage_completed', stage_name: 'quality_rollup', detail: { total_runs: obs.total_runs, failed_runs: obs.failed_runs } },
          { event_type: 'stage_completed', stage_name: 'observability_export', detail: { audit_events: obs.audit_events_count } },
        ]
      : []),
  ])

  return { stages, events: timelineEvents, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildOverviewWorkflow(
  summary: DashboardSummaryLike,
  trends: TrendResponseLike,
  days: number,
  projectLabel: string,
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const readiness = summary?.release_readiness ?? null
  const trendPoints = trends?.data ?? []
  const metricValue = (metric?: DashboardMetricValue) => {
    if (metric == null) return undefined
    return typeof metric.value === 'number' ? metric.value : Number(metric.value)
  }
  const stages = [
    toStage({
      stage_name: 'quality_snapshot',
      status: summary ? 'completed' : 'skipped',
      label: 'Quality Snapshot',
      description: `Capture the current quality state for ${projectLabel}`,
      result_data: {
        days,
        total_executions: metricValue(summary?.total_executions_7d),
        active_defects: metricValue(summary?.active_defects),
      },
      evidence_count: summary ? 1 : 0,
      skipped_reason: !summary ? 'No quality data for this scope yet' : null,
    }),
    toStage({
      stage_name: 'readiness_check',
      status: readiness ? 'completed' : 'skipped',
      label: 'Readiness Check',
      description: 'Summarize release readiness and gate signals',
      result_data: { readiness },
      confidence_score: readiness === 'GREEN' ? 95 : readiness === 'AMBER' ? 70 : readiness === 'RED' ? 35 : null,
      evidence_count: readiness ? 1 : 0,
      skipped_reason: !readiness ? 'No readiness signal — needs runs and gate policy' : null,
    }),
    toStage({
      stage_name: 'trend_analysis',
      status: trendPoints.length > 0 ? 'completed' : 'skipped',
      label: 'Trend Analysis',
      description: 'Review pass rate, automation growth, and duration trends',
      result_data: { trend_points: trendPoints.length },
      evidence_count: trendPoints.length,
      skipped_reason: trendPoints.length === 0 ? 'No trend data for this scope yet' : null,
    }),
    toStage({
      stage_name: 'action_focus',
      status: summary?.new_failures_24h != null ? 'completed' : 'skipped',
      label: 'Action Focus',
      description: 'Turn the quality snapshot into next steps',
      result_data: {
        flaky_tests: metricValue(summary?.flaky_test_count),
        new_failures_24h: metricValue(summary?.new_failures_24h),
      },
      evidence_count: metricValue(summary?.new_failures_24h) ?? 0,
      skipped_reason: summary?.new_failures_24h == null ? 'No 24h failure signal yet' : null,
    }),
  ]

  const events = buildEventsFromLabels([
    { event_type: summary ? 'stage_completed' : 'stage_started', stage_name: 'quality_snapshot', detail: { project: projectLabel } },
    { event_type: readiness ? 'stage_completed' : 'stage_started', stage_name: 'readiness_check', detail: { readiness } },
    { event_type: trendPoints.length > 0 ? 'stage_completed' : 'stage_started', stage_name: 'trend_analysis', detail: { points: trendPoints.length } },
    { event_type: 'stage_completed', stage_name: 'action_focus', detail: { new_failures_24h: summary?.new_failures_24h ?? 0 } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildRunsWorkflow(
  runs: RunLike[],
  statusFilter: string,
  isAllProjects: boolean,
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const failedRuns = runs.filter(run => (run.status ?? '').toUpperCase() === 'FAILED')
  const passedRuns = runs.filter(run => (run.status ?? '').toUpperCase() === 'PASSED' || (run.status ?? '').toLowerCase() === 'passed')
  const stages = [
    toStage({
      stage_name: 'run_ingestion',
      status: runs.length > 0 ? 'completed' : 'skipped',
      label: 'Run Ingestion',
      description: 'Load the latest builds for the current project scope',
      result_data: { runs: runs.length, all_projects: isAllProjects },
      evidence_count: runs.length,
      skipped_reason: runs.length === 0 ? 'No runs ingested for this scope' : null,
    }),
    toStage({
      stage_name: 'failure_detection',
      status: failedRuns.length > 0 ? 'completed' : 'skipped',
      label: 'Failure Detection',
      description: 'Surface the failed runs that need intelligence first',
      result_data: { failed_runs: failedRuns.length, filter: statusFilter || 'all' },
      skipped_reason: failedRuns.length === 0 ? 'No failed runs match the current filter' : null,
      evidence_count: failedRuns.length,
    }),
    toStage({
      stage_name: 'intelligence_handoff',
      status: failedRuns.length > 0 ? 'completed' : 'skipped',
      label: 'Intelligence Handoff',
      description: 'Route failed runs into Run Intelligence',
      result_data: { intelligence_ready: failedRuns.length },
      evidence_count: failedRuns.length,
      skipped_reason: failedRuns.length === 0 ? 'Nothing to hand off — no failed runs' : null,
    }),
    toStage({
      stage_name: 'release_context',
      status: passedRuns.length > 0 ? 'completed' : 'skipped',
      label: 'Release Context',
      description: 'Keep passing runs available for comparison and release checks',
      result_data: { passed_runs: passedRuns.length },
      evidence_count: passedRuns.length,
      skipped_reason: passedRuns.length === 0 ? 'No passing runs in the current scope' : null,
    }),
  ]

  const events = buildEventsFromLabels([
    { event_type: runs.length > 0 ? 'stage_completed' : 'stage_skipped', stage_name: 'run_ingestion', detail: { runs: runs.length } },
    { event_type: failedRuns.length > 0 ? 'stage_completed' : 'stage_skipped', stage_name: 'failure_detection', detail: { failed_runs: failedRuns.length } },
    { event_type: failedRuns.length > 0 ? 'stage_completed' : 'stage_skipped', stage_name: 'intelligence_handoff', detail: { failed_runs: failedRuns.length } },
    { event_type: passedRuns.length > 0 ? 'stage_completed' : 'stage_skipped', stage_name: 'release_context', detail: { passed_runs: passedRuns.length } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildIntelligenceHubWorkflow(runs: Array<{ id: string; status: string; failed_tests: number; total_tests: number; created_at: string }>): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const failedRuns = runs.filter(run => (run.status ?? '').toUpperCase() === 'FAILED' || (run.status ?? '').toLowerCase() === 'failed')
  const passedRuns = runs.filter(run => (run.status ?? '').toUpperCase() === 'PASSED' || (run.status ?? '').toLowerCase() === 'passed')
  const stages = [
    toStage({
      stage_name: 'run_selection',
      status: runs.length > 0 ? 'completed' : 'skipped',
      label: 'Run Selection',
      description: 'Choose the run that needs investigation',
      result_data: { total_runs: runs.length },
      evidence_count: runs.length,
      skipped_reason: runs.length === 0 ? 'No runs available to investigate' : null,
    }),
    toStage({
      stage_name: 'failed_run_focus',
      status: failedRuns.length > 0 ? 'completed' : 'skipped',
      label: 'Failed Run Focus',
      description: 'Prioritize the runs with AI intelligence available',
      result_data: { failed_runs: failedRuns.length },
      skipped_reason: failedRuns.length === 0 ? 'No failed runs available in the current scope' : null,
      evidence_count: failedRuns.length,
    }),
    toStage({
      stage_name: 'intelligence_launch',
      status: failedRuns.length > 0 ? 'completed' : 'skipped',
      label: 'Intelligence Launch',
      description: 'Open the run intelligence view for the selected build',
      result_data: { ready_runs: failedRuns.length },
      evidence_count: failedRuns.length,
      skipped_reason: failedRuns.length === 0 ? 'No failed runs to investigate' : null,
    }),
    toStage({
      stage_name: 'passing_context',
      status: passedRuns.length > 0 ? 'completed' : 'skipped',
      label: 'Passing Context',
      description: 'Keep green runs ready for comparison',
      result_data: { passing_runs: passedRuns.length },
      evidence_count: passedRuns.length,
      skipped_reason: passedRuns.length === 0 ? 'No passing runs available yet' : null,
    }),
  ]
  const events = buildEventsFromLabels([
    { event_type: 'stage_completed', stage_name: 'run_selection', detail: { runs: runs.length } },
    { event_type: failedRuns.length > 0 ? 'stage_completed' : 'stage_skipped', stage_name: 'failed_run_focus', detail: { failed_runs: failedRuns.length } },
    { event_type: 'stage_completed', stage_name: 'intelligence_launch', detail: { failed_runs: failedRuns.length } },
    { event_type: 'stage_completed', stage_name: 'passing_context', detail: { passed_runs: passedRuns.length } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildFailureAnalysisWorkflow(
  flakyCount: number,
  categoryCount: number,
  topCount: number,
  days: number,
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const stages = [
    toStage({
      stage_name: 'flaky_detection',
      status: flakyCount > 0 ? 'completed' : 'skipped',
      label: 'Flaky Detection',
      description: 'Detect unstable tests across the selected time window',
      result_data: { flaky_count: flakyCount, days },
      evidence_count: flakyCount,
      skipped_reason: flakyCount === 0 ? 'No flaky tests detected in this window' : null,
    }),
    toStage({
      stage_name: 'category_clustering',
      status: categoryCount > 0 ? 'completed' : 'skipped',
      label: 'Category Clustering',
      description: 'Group failures into human-readable categories',
      result_data: { category_count: categoryCount },
      evidence_count: categoryCount,
      skipped_reason: categoryCount === 0 ? 'No failure categories yet — needs failed runs to cluster' : null,
    }),
    toStage({
      stage_name: 'hotspot_ranking',
      status: topCount > 0 ? 'completed' : 'skipped',
      label: 'Hotspot Ranking',
      description: 'Rank the worst failing tests for remediation',
      result_data: { hotspot_count: topCount },
      evidence_count: topCount,
      skipped_reason: topCount === 0 ? 'No hotspots — no failed tests in this window' : null,
    }),
    toStage({
      stage_name: 'remediation_focus',
      status: (flakyCount + topCount) > 0 ? 'completed' : 'skipped',
      label: 'Remediation Focus',
      description: 'Identify the tests and suites to stabilize first',
      result_data: { actionable_items: flakyCount + topCount },
      evidence_count: flakyCount + topCount,
      skipped_reason: (flakyCount + topCount) === 0 ? 'Nothing to remediate yet' : null,
    }),
  ]

  const events = buildEventsFromLabels([
    { event_type: flakyCount > 0 ? 'stage_completed' : 'stage_skipped', stage_name: 'flaky_detection', detail: { flaky_count: flakyCount } },
    { event_type: categoryCount > 0 ? 'stage_completed' : 'stage_skipped', stage_name: 'category_clustering', detail: { category_count: categoryCount } },
    { event_type: topCount > 0 ? 'stage_completed' : 'stage_skipped', stage_name: 'hotspot_ranking', detail: { hotspot_count: topCount } },
    { event_type: (flakyCount + topCount) > 0 ? 'stage_completed' : 'stage_skipped', stage_name: 'remediation_focus', detail: { actionable_items: flakyCount + topCount } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildCoverageWorkflow(
  summary: CoverageSummaryLike,
  suites: CoverageSuiteLike[],
  days: number,
  projectLabel: string,
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const totalExecutions = summary?.total_executions ?? suites.reduce((sum, suite) => sum + suite.passed + suite.failed + suite.skipped, 0)
  const failingSuites = suites.filter(suite => suite.failed > 0)
  const stages = [
    toStage({
      stage_name: 'coverage_snapshot',
      status: totalExecutions > 0 ? 'completed' : 'skipped',
      label: 'Coverage Snapshot',
      description: `Capture coverage data for ${projectLabel} over the last ${days} days`,
      result_data: {
        unique_tests: summary?.unique_tests ?? 0,
        suite_count: summary?.suite_count ?? suites.length,
        total_executions: totalExecutions,
      },
      evidence_count: totalExecutions,
      skipped_reason: totalExecutions === 0 ? `No executions in the last ${days} days` : null,
    }),
    toStage({
      stage_name: 'suite_breadth',
      status: suites.length > 0 ? 'completed' : 'skipped',
      label: 'Suite Breadth',
      description: 'Map execution coverage across suites and project scope',
      result_data: {
        suites: suites.length,
        failing_suites: failingSuites.length,
      },
      evidence_count: suites.length,
      skipped_reason: suites.length === 0 ? 'No suites have executed in this window' : null,
    }),
    toStage({
      stage_name: 'coverage_risk',
      // 'completed' for both branches — the work is already done; risk findings
      // are surfaced via route_rationale and the failing-suite badge, not by
      // showing a misleading "running" spinner.
      status: totalExecutions > 0 ? 'completed' : 'skipped',
      label: 'Coverage Risk',
      description: 'Highlight where coverage quality or execution balance needs attention',
      result_data: {
        avg_pass_rate: summary?.avg_pass_rate ?? null,
        days_with_runs: summary?.days_with_runs ?? 0,
      },
      confidence_score: summary?.avg_pass_rate != null ? Math.round(summary.avg_pass_rate) : null,
      evidence_count: failingSuites.length,
      route_rationale: failingSuites.length > 0 ? 'Some suites still require cleanup or deeper inspection' : 'Coverage is balanced across the current scope',
      skipped_reason: totalExecutions === 0 ? 'No executions to analyze yet' : null,
    }),
    toStage({
      stage_name: 'coverage_actions',
      status: totalExecutions > 0 ? 'completed' : 'skipped',
      label: 'Coverage Actions',
      description: 'Use the coverage signal to guide follow-up work',
      result_data: {
        top_suites: suites.slice(0, 5).map(suite => suite.suite_name),
      },
      evidence_count: failingSuites.length,
      skipped_reason: totalExecutions === 0 ? 'Nothing to act on without executions' : null,
    }),
  ]

  const events = buildEventsFromLabels([
    { event_type: 'stage_completed', stage_name: 'coverage_snapshot', detail: { total_executions: totalExecutions, project: projectLabel } },
    { event_type: suites.length > 0 ? 'stage_completed' : 'stage_started', stage_name: 'suite_breadth', detail: { suites: suites.length } },
    { event_type: failingSuites.length > 0 ? 'stage_started' : 'stage_completed', stage_name: 'coverage_risk', detail: { failing_suites: failingSuites.length } },
    { event_type: 'stage_completed', stage_name: 'coverage_actions', detail: { top_suites: Math.min(5, suites.length) } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildDefectsWorkflow(
  items: DefectLike[],
  resolutionFilter: string | undefined,
  projectLabel: string,
  page: number,
  pages: number,
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const linked = items.filter(item => !!item.jira_ticket_id)
  const openItems = items.filter(item => (item.resolution_status ?? '').toUpperCase() === 'OPEN' || (item.resolution_status ?? '').toUpperCase() === 'IN_PROGRESS')
  const resolvedItems = items.filter(item => (item.resolution_status ?? '').toUpperCase() === 'RESOLVED' || (item.resolution_status ?? '').toUpperCase() === 'CLOSED')
  const stages = [
    toStage({
      stage_name: 'defect_intake',
      status: items.length > 0 ? 'completed' : 'pending',
      label: 'Defect Intake',
      description: `Load defects for ${projectLabel}`,
      result_data: {
        total_defects: items.length,
        current_page: page,
        total_pages: pages,
      },
      evidence_count: items.length,
    }),
    toStage({
      stage_name: 'jira_linkage',
      status: linked.length > 0 ? 'completed' : items.length > 0 ? 'skipped' : 'pending',
      label: 'Jira Linkage',
      description: 'Track which defects are already routed to Jira',
      result_data: {
        linked: linked.length,
        unresolved: items.length - linked.length,
      },
      evidence_count: linked.length,
      skipped_reason: items.length > 0 && linked.length === 0 ? 'None of the loaded defects are linked to Jira yet' : null,
    }),
    toStage({
      stage_name: 'resolution_flow',
      status: resolvedItems.length > 0
        ? 'completed'
        : items.length > 0
          ? 'skipped'
          : 'pending',
      label: 'Resolution Flow',
      description: 'Show how open, in-progress, and resolved defects move through the system',
      result_data: {
        open: openItems.length,
        resolved: resolvedItems.length,
        filter: resolutionFilter ?? 'all',
      },
      evidence_count: openItems.length + resolvedItems.length,
      route_rationale: openItems.length > 0 ? 'Open defects still need owner follow-up' : 'No active defects in the current slice',
      skipped_reason:
        items.length > 0 && resolvedItems.length === 0
          ? 'No resolved defects in this slice yet'
          : null,
    }),
    toStage({
      stage_name: 'triage_focus',
      status: items.length > 0 ? 'completed' : 'pending',
      label: 'Triage Focus',
      description: 'Prioritize the next defect to review',
      result_data: {
        top_item: items[0]?.test_name ?? null,
        confidence: items[0]?.ai_confidence_score ?? null,
      },
      confidence_score: items[0]?.ai_confidence_score ?? null,
      evidence_count: items.length,
    }),
  ]

  const events = buildEventsFromLabels([
    { event_type: 'stage_completed', stage_name: 'defect_intake', detail: { defects: items.length, project: projectLabel } },
    {
      event_type: linked.length > 0 ? 'stage_completed' : items.length > 0 ? 'stage_skipped' : 'stage_started',
      stage_name: 'jira_linkage',
      detail: { linked: linked.length },
    },
    {
      event_type: resolvedItems.length > 0 ? 'stage_completed' : items.length > 0 ? 'stage_skipped' : 'stage_started',
      stage_name: 'resolution_flow',
      detail: { open: openItems.length, resolved: resolvedItems.length },
    },
    { event_type: 'stage_completed', stage_name: 'triage_focus', detail: { page, items: items.length } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildTrendsWorkflow(
  days: number,
  enabledCharts: string[],
  trendPoints: TrendResponse['data'],
  projectLabel: string,
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const points = trendPoints ?? []
  const stages = [
    toStage({
      stage_name: 'trend_capture',
      status: points.length > 0 ? 'completed' : 'pending',
      label: 'Trend Capture',
      description: `Collect quality signals for ${projectLabel} over ${days} days`,
      result_data: {
        trend_points: points.length,
        enabled_charts: enabledCharts.length,
      },
      evidence_count: points.length,
    }),
    toStage({
      stage_name: 'signal_comparison',
      status: enabledCharts.length > 0 ? 'completed' : 'pending',
      label: 'Signal Comparison',
      description: 'Compare pass rate, failure rate, and volume movements',
      result_data: {
        enabled_charts: enabledCharts,
      },
      evidence_count: enabledCharts.length,
    }),
    toStage({
      stage_name: 'report_delivery',
      status: points.length > 0 ? 'completed' : 'pending',
      label: 'Report Delivery',
      description: 'Package the trend view for export or email delivery',
      result_data: {
        available_actions: ['pdf', 'email', 'customize'],
      },
      evidence_count: points.length,
      route_rationale: 'The page lets users tune and export the trend view',
    }),
  ]

  const events = buildEventsFromLabels([
    { event_type: 'stage_completed', stage_name: 'trend_capture', detail: { points: points.length } },
    { event_type: enabledCharts.length > 0 ? 'stage_completed' : 'stage_started', stage_name: 'signal_comparison', detail: { charts: enabledCharts.length } },
    { event_type: 'stage_completed', stage_name: 'report_delivery', detail: { days, project: projectLabel } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildValueMetricsWorkflow(
  metrics: ValueMetricsLike,
  days: number,
  projectLabel: string,
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  // Confidence is only meaningful when there's at least one positive value signal.
  // An all-zero metrics payload was previously surfacing as 70% confident because
  // the formula used 70 as an unconditional floor.
  const hasValueSignal = !!metrics && (
    metrics.triage_time_saved_minutes > 0 ||
    metrics.defects_auto_grouped > 0 ||
    metrics.duplicate_tickets_avoided > 0 ||
    metrics.defects_promoted > 0 ||
    metrics.risky_releases_blocked > 0 ||
    metrics.releases_conditional > 0 ||
    metrics.intelligence_reports_generated > 0
  )
  const roiEvidenceCount =
    (metrics?.defects_auto_grouped ? 1 : 0) +
    (metrics?.duplicate_tickets_avoided ? 1 : 0) +
    (metrics?.risky_releases_blocked ? 1 : 0)

  const stages = [
    toStage({
      stage_name: 'value_capture',
      status: hasValueSignal ? 'completed' : 'pending',
      label: 'Value Capture',
      description: `Summarize the operational value generated for ${projectLabel}`,
      result_data: {
        triage_minutes: metrics?.triage_time_saved_minutes ?? 0,
        days,
      },
      evidence_count: (metrics?.triage_time_saved_minutes ?? 0) > 0 ? 1 : 0,
    }),
    toStage({
      stage_name: 'roi_calculation',
      status: hasValueSignal ? 'completed' : 'pending',
      label: 'ROI Calculation',
      description: 'Quantify defects grouped, duplicates avoided, and releases blocked',
      // `hasValueSignal` already implies `metrics` is non-null (it is
      // `!!metrics && …`), but that fact lives in a separate boolean TS can't
      // relate back to `metrics`. Re-checking `metrics &&` here narrows the
      // union without changing behaviour, dropping the non-null assertions.
      result_data: metrics && hasValueSignal ? {
        defects_auto_grouped: metrics.defects_auto_grouped,
        duplicate_tickets_avoided: metrics.duplicate_tickets_avoided,
        risky_releases_blocked: metrics.risky_releases_blocked,
      } : null,
      confidence_score: metrics && hasValueSignal
        ? Math.min(100, 70 + Math.round(metrics.triage_time_saved_hours))
        : null,
      evidence_count: roiEvidenceCount,
    }),
    toStage({
      stage_name: 'value_delivery',
      status: hasValueSignal ? 'completed' : 'pending',
      label: 'Value Delivery',
      description: 'Prepare the exportable result for leadership and customer success',
      result_data: {
        reports: metrics?.intelligence_reports_generated ?? 0,
        quarantine_recommended: metrics?.quarantine_recommended ?? 0,
      },
      evidence_count: metrics?.intelligence_reports_generated ?? 0,
      route_rationale: hasValueSignal
        ? 'Metrics are ready to share across the team'
        : 'No measurable value captured in the selected window',
    }),
  ]

  const events = buildEventsFromLabels([
    { event_type: hasValueSignal ? 'stage_completed' : 'stage_started', stage_name: 'value_capture', detail: { days } },
    { event_type: hasValueSignal ? 'stage_completed' : 'stage_started', stage_name: 'roi_calculation', detail: { reports: metrics?.intelligence_reports_generated ?? 0 } },
    { event_type: hasValueSignal ? 'stage_completed' : 'stage_started', stage_name: 'value_delivery', detail: { reports: metrics?.intelligence_reports_generated ?? 0 } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildDeepInvestigationWorkflow(
  runId: string,
  clusters: FailureCluster[],
  findings: DeepFindingLike[],
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const evidenceCount = findings.reduce((sum, finding) => sum + (finding?.evidence?.length ?? 0), 0)
  const actionableCount = findings.filter(finding => (finding?.recommended_actions?.length ?? 0) > 0).length
  const promotedCount = clusters.length > 0 ? 1 : 0
  const stages = [
    toStage({
      stage_name: 'failure_clustering',
      status: clusters.length > 0 ? 'completed' : 'pending',
      label: 'Failure Clustering',
      description: 'Group failures into investigation-ready clusters',
      result_data: {
        run_id: runId,
        clusters: clusters.length,
      },
      evidence_count: clusters.length,
    }),
    toStage({
      stage_name: 'root_cause_analysis',
      status: findings.length > 0 ? 'completed' : 'pending',
      label: 'Root Cause Analysis',
      description: 'Trace symptoms to likely causes and services',
      result_data: {
        findings: findings.length,
        actionable_findings: actionableCount,
      },
      evidence_count: evidenceCount,
    }),
    toStage({
      stage_name: 'evidence_synthesis',
      status: evidenceCount > 0 ? 'completed' : 'pending',
      label: 'Evidence Synthesis',
      description: 'Collect traces, logs, contract signals, and recommendations',
      result_data: {
        evidence_count: evidenceCount,
      },
      confidence_score: findings[0]?.confidence_score ?? null,
      evidence_count: evidenceCount,
    }),
    toStage({
      stage_name: 'triage',
      status: promotedCount > 0 ? 'completed' : 'pending',
      label: 'Defect Triage',
      description: 'Prepare the cluster for defect promotion',
      result_data: {
        promotable_clusters: promotedCount,
      },
      evidence_count: actionableCount,
      route_rationale: actionableCount > 0 ? 'The current cluster has recommendations and evidence' : 'More evidence is needed before promotion',
    }),
  ]

  const events = buildEventsFromLabels([
    { event_type: 'stage_completed', stage_name: 'failure_clustering', detail: { clusters: clusters.length } },
    { event_type: findings.length > 0 ? 'stage_completed' : 'stage_started', stage_name: 'root_cause_analysis', detail: { findings: findings.length } },
    { event_type: evidenceCount > 0 ? 'stage_completed' : 'stage_started', stage_name: 'evidence_synthesis', detail: { evidence_count: evidenceCount } },
    { event_type: promotedCount > 0 ? 'stage_completed' : 'stage_started', stage_name: 'triage', detail: { promoted_clusters: promotedCount } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

export function buildSearchWorkflow(
  query: string,
  searchType: string,
  results: SearchResponse | null,
): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  const total = results?.total ?? 0
  const hasQuery = query.trim().length > 0
  // Once a search has actually run, downstream stages with zero hits are
  // "skipped" (work was attempted, nothing to rank/drill into) rather than
  // "pending" (waiting for input). Pending implies activity; skipped does not.
  const searchAttempted = hasQuery && results !== null
  const downstreamStatus: WorkflowStageNode['status'] = total > 0
    ? 'completed'
    : searchAttempted
      ? 'skipped'
      : 'pending'

  const stages = [
    toStage({
      stage_name: 'query_capture',
      status: hasQuery ? 'completed' : 'pending',
      label: 'Query Capture',
      description: 'Capture the search intent and scope',
      result_data: { query },
      evidence_count: hasQuery ? 1 : 0,
    }),
    toStage({
      stage_name: 'retrieval_mode',
      status: 'completed',
      label: 'Retrieval Mode',
      description: 'Select keyword, semantic, or hybrid retrieval',
      result_data: { search_type: searchType },
      evidence_count: 1,
    }),
    toStage({
      stage_name: 'ranking',
      status: downstreamStatus,
      label: 'Ranking',
      description: 'Rank the best matches by relevance and evidence',
      result_data: { total_results: total, pages: results?.pages ?? 0 },
      evidence_count: total,
      skipped_reason: searchAttempted && total === 0 ? 'No results matched this query' : null,
    }),
    toStage({
      stage_name: 'drilldown',
      status: downstreamStatus,
      label: 'Drilldown',
      description: 'Open the right run or test from the matched result',
      result_data: { match_reasons: results?.items?.[0]?.match_reasons?.length ?? 0 },
      evidence_count: results?.items?.[0] ? 1 : 0,
      skipped_reason: searchAttempted && total === 0 ? 'Nothing to drill into without matches' : null,
    }),
  ]

  const downstreamEvent = total > 0
    ? 'stage_completed'
    : searchAttempted
      ? 'stage_skipped'
      : 'stage_started'

  const events = buildEventsFromLabels([
    { event_type: hasQuery ? 'stage_completed' : 'stage_started', stage_name: 'query_capture', detail: { query } },
    { event_type: 'stage_completed', stage_name: 'retrieval_mode', detail: { search_type: searchType } },
    { event_type: downstreamEvent, stage_name: 'ranking', detail: { total } },
    { event_type: downstreamEvent, stage_name: 'drilldown', detail: { total } },
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}
