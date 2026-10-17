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

/**
 * One timeline event backed by a record. `timestamp` is that record's own
 * stored time (an audit row's `created_at`, a probe's `checked_at`, a phase's
 * `actual_end`, ...).
 */
export interface TimedRecord {
  event_type: string
  stage_name: string
  timestamp: string | null | undefined
  detail?: Record<string, unknown>
}

/**
 * Timeline events from records, newest first (the order of the audit table and
 * of every activity feed in the app). A record with no time, or an unparseable
 * one, produces NO event: the timeline never invents a time. These builders
 * used to stamp each event `now - n * 45 s`, which rendered as real dates that
 * moved on every reload (HON-06).
 */
export function eventsFromRecords(items: TimedRecord[]): WorkflowEventNode[] {
  const timed = items
    .map((item, index) => ({ item, index, at: item.timestamp ? Date.parse(item.timestamp) : Number.NaN }))
    .filter(entry => !Number.isNaN(entry.at))
  timed.sort((a, b) => (b.at - a.at) || (a.index - b.index))
  return timed.map(({ item }) => ({
    event_type: item.event_type,
    stage_name: item.stage_name,
    timestamp: item.timestamp as string,
    test_case_id: null,
    detail: item.detail ?? {},
  }))
}

/** The latest parseable time among `values`, or null when none is. */
function latestTime(values: Array<string | null | undefined>): string | null {
  let best: string | null = null
  let bestAt = Number.NEGATIVE_INFINITY
  for (const value of values) {
    if (!value) continue
    const at = Date.parse(value)
    if (!Number.isNaN(at) && at > bestAt) {
      best = value
      bestAt = at
    }
  }
  return best
}

export function buildReleaseGateWorkflow(decision: ReleaseCouncilDecision): { stages: WorkflowStageNode[]; events: WorkflowEventNode[]; stageOrder: string[] } {
  // A synthesised quick-look view has no stored decision: nothing was
  // decided at any time, so the decision stage stays pending and dateless.
  const stored = !decision.synthesized
  const decidedAt = decision.created_at ?? null
  const overrides = decision.override_audit ?? []
  // No stage carries a `confidence_score`: the gate measures none. The risk
  // score used to be shown as "N% confidence" (a 72-risk NO GO read as "72%
  // confidence"), and the first dimension's score as the synthesis's.
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
      evidence_count: (decision.baseline_diff?.new_failures?.length ?? 0) + (decision.open_defects_by_component?.length ?? 0),
      route_rationale: decision.reasoning ?? null,
    }),
    toStage({
      stage_name: 'release_decision',
      status: stored ? 'completed' : 'pending',
      label: 'Release Decision',
      description: 'Produce the final go / conditional go / no-go call',
      completed_at: decidedAt,
      result_data: {
        recommendation: decision.recommendation,
        risk_score: decision.risk_score,
        overridden: !!decision.human_override,
        decided_at: decidedAt,
      },
      evidence_count: overrides.length,
      route_rationale: stored
        ? (decision.human_override ?? decision.reasoning ?? null)
        : "Quick-look verdict computed from the run's aggregates; no decision has been stored yet",
    }),
  ]

  // The decision's own row time, then each override at its audit time. The
  // policy, cluster and evidence stages have no time of their own (they are
  // inputs to the same stored row), so they get no invented event.
  const events = eventsFromRecords([
    {
      event_type: 'stage_completed',
      stage_name: 'release_decision',
      timestamp: stored ? decidedAt : null,
      detail: {
        // What the gate stored, before any override or band downgrade.
        recommendation: decision.original_recommendation ?? decision.recommendation,
        risk_score: decision.original_risk_score ?? decision.risk_score,
      },
    },
    ...overrides.map(entry => ({
      event_type: 'decision_overridden',
      stage_name: 'release_decision',
      timestamp: entry.timestamp,
      detail: {
        by: entry.actor_name ?? 'Unknown',
        change: `${entry.before_recommendation} → ${entry.after_recommendation}`,
        reason: entry.reason,
      },
    })),
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

  // A phase starts at its recorded `actual_start` and ends at `actual_end`;
  // a phase with neither (not started) has no event at all.
  const events = eventsFromRecords(
    orderedPhases.flatMap(phase => [
      {
        event_type: 'stage_started',
        stage_name: `release_phase:${phase.id}`,
        timestamp: phase.actual_start,
        detail: { phase: phase.name },
      },
      {
        event_type: phase.status === 'skipped' ? 'stage_skipped' : 'stage_completed',
        stage_name: `release_phase:${phase.id}`,
        timestamp: phase.actual_end,
        detail: { phase: phase.name, status: phase.status },
      },
    ]),
  )

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

  // A step is an event only once it was completed (or skipped) at a recorded
  // time; a pending step has none.
  const events = eventsFromRecords(
    steps.map(step => ({
      event_type: step.status === 'skipped' ? 'stage_skipped' : 'stage_completed',
      stage_name: step.key,
      timestamp: step.status === 'pending' ? null : step.completed_at,
      detail: { label: step.label },
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
  const lastDelivery = latestTime(subs.map(sub => sub.last_delivered_at))
  // NTF-04: `views` are the project's REPORT views; a Daily or Weekly digest
  // bound to one is filtered by it.
  const subscriptionsWithView = subs.filter(sub => !!sub.saved_view_id).length
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
      status: views.length > 0 || subscriptionsWithView > 0 ? 'completed' : 'pending',
      label: 'Saved View Resolution',
      description: `Saved views: ${views.length} report views; a Daily or Weekly digest with a view is filtered by its releases, suites and window`,
      result_data: {
        saved_views: views.length,
        shared_views: views.filter(v => v.is_shared).length,
        default_views: views.filter(v => v.is_default).length,
        subscriptions_with_view: subscriptionsWithView,
      },
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
      // No confidence: nothing measures one. A literal "90% / 45% confidence"
      // was shown depending only on whether a preview existed.
      completed_at: preview?.generated_at ?? null,
      evidence_count: preview ? preview.top_clusters.length + preview.top_blockers.length : 0,
      route_rationale: preview ? 'Preview output is assembled from the current project scope' : 'Preview not yet generated',
    }),
    toStage({
      stage_name: 'delivery',
      // Done once a digest was actually delivered, not merely subscribed to.
      status: lastDelivery ? 'completed' : 'pending',
      label: 'Delivery',
      description: 'Ship digests to email, Slack, or Teams',
      completed_at: lastDelivery,
      result_data: {
        channels: [...new Set(activeSubscriptions.map(sub => sub.channel))],
        last_delivered_at: lastDelivery,
      },
      evidence_count: subs.reduce((sum, sub) => sum + (sub.delivery_count ?? 0), 0),
      route_rationale: lastDelivery ? null : 'No digest has been delivered yet',
    }),
  ]

  // Each subscription's creation and last delivery, and the preview's
  // generation time: the only times these records carry.
  const events = eventsFromRecords([
    ...subs.flatMap(sub => [
      {
        event_type: 'subscription_created',
        stage_name: 'signal_collection',
        timestamp: sub.created_at,
        detail: { subscription: sub.name, schedule: sub.schedule, channel: sub.channel },
      },
      {
        event_type: 'stage_completed',
        stage_name: 'delivery',
        timestamp: sub.last_delivered_at,
        detail: { subscription: sub.name, channel: sub.channel, deliveries: sub.delivery_count },
      },
    ]),
    {
      event_type: 'stage_completed',
      stage_name: 'digest_compilation',
      timestamp: preview?.generated_at,
      detail: { preview: true, new_regressions: preview?.new_regressions ?? 0 },
    },
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
      status: !status.last_checked_at
        ? 'pending'
        : status.status === 'healthy' ? 'completed' : status.status === 'degraded' ? 'running' : 'failed',
      label: status.provider,
      description: 'Active health probe result',
      completed_at: status.last_checked_at,
      result_data: {
        status: status.status,
        response_ms: status.response_ms,
        message: status.message,
        consecutive_failures: status.consecutive_failures,
        last_success_at: status.last_success_at,
      },
      // No confidence: a probe measures status and latency, not a confidence.
      // "95% / 65% / 25%" was a literal per status.
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

  // Each provider's latest probe at its `last_checked_at`, and the selected
  // provider's probe history at each entry's `checked_at`.
  const probeOutcome = (status: string) => (status === 'healthy' || status === 'degraded' ? 'stage_completed' : 'stage_failed')
  const events = eventsFromRecords([
    ...visibleStatuses.map(status => ({
      event_type: probeOutcome(status.status),
      stage_name: `probe:${status.provider}`,
      timestamp: status.last_checked_at,
      detail: { provider: status.provider, status: status.status, response_ms: status.response_ms },
    })),
    ...(selectedProvider
      ? history.slice(0, 5).map(entry => ({
          event_type: probeOutcome(entry.status),
          stage_name: `history:${selectedProvider}`,
          timestamp: entry.checked_at,
          detail: { status: entry.status, response_ms: entry.response_ms, auth_valid: entry.auth_valid },
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
      completed_at: latestTime((dashboard?.recent_eval_runs ?? []).map(run => run.evaluated_at)),
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
      completed_at: gateResult?.evaluated_at ?? null,
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

  // Each evaluation run, baseline, gate check and model version at its own
  // recorded time.
  const events = eventsFromRecords([
    ...(dashboard?.recent_eval_runs ?? []).map(run => ({
      event_type: 'stage_completed',
      stage_name: 'quality_evaluation',
      timestamp: run.evaluated_at,
      detail: { model: run.model_name, accuracy: run.accuracy, items: run.total_items },
    })),
    ...baselines.map(baseline => ({
      event_type: 'baseline_created',
      stage_name: 'gate_review',
      timestamp: baseline.created_at,
      detail: { agent: baseline.agent_name, prompt_version: baseline.prompt_version },
    })),
    {
      event_type: gateResult?.status === 'FAIL' ? 'stage_failed' : 'stage_completed',
      stage_name: 'gate_review',
      timestamp: gateResult?.evaluated_at,
      detail: { status: gateResult?.status, agent: gateResult?.agent_name },
    },
    ...(dashboard?.model_versions ?? []).map(version => ({
      event_type: 'model_registered',
      stage_name: 'model_governance',
      timestamp: version.created_at,
      detail: { model: version.model_name, track: version.track, status: version.status },
    })),
  ])

  return { stages, events, stageOrder: stages.map(stage => stage.stage_name) }
}

/** The audit timeline stage each audit source belongs to. */
const AUDIT_SOURCE_STAGE: Record<string, string> = {
  access: 'access_events',
  settings: 'configuration_changes',
  identity: 'configuration_changes',
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

  // The newest audit rows at their own `created_at` (the table above lists
  // the same rows at the same times). The observability rollup is a count
  // computed on request: it has no time of its own, so it gets no event.
  const timelineEvents = eventsFromRecords(
    safeEvents.slice(0, 8).map(ev => ({
      event_type: 'audit_recorded',
      stage_name: AUDIT_SOURCE_STAGE[ev.source] ?? ev.source,
      timestamp: ev.created_at,
      detail: {
        action: ev.action,
        actor: ev.actor_name ?? 'system',
        success: ev.success ?? true,
      },
    })),
  )

  return { stages, events: timelineEvents, stageOrder: stages.map(stage => stage.stage_name) }
}
