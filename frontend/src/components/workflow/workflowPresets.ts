import type { WorkflowEventNode, WorkflowStageNode } from './WorkflowTimeline'
import type { ReleaseCouncilDecision } from '@/services/releaseCouncilService'
import type { ReleaseDetail, ReleasePhase } from '@/types/releases'
import type { OnboardingStatus, OnboardingStep } from '@/services/onboardingService'
import type { DigestContent, DigestSubscription } from '@/services/digestService'
import type { IntegrationStatus, ProbeHistoryEntry } from '@/services/integrationHealthService'
import type { AIQualityDashboard, EvalBaseline, EvalGateResult } from '@/services/aiEvalService'
import type { AuditEvent, TenantObservability } from '@/services/auditDashboardService'

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
