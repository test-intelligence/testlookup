import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ReleaseCouncilDecision } from '@/services/releaseCouncilService'
import {
  buildAIEvalWorkflow,
  buildAuditWorkflow,
  buildDigestWorkflow,
  buildIntegrationHealthWorkflow,
  buildOnboardingWorkflow,
  buildReleaseGateWorkflow,
  buildReleaseWorkflow,
  eventsFromRecords,
} from './workflowPresets'

describe('workflowPresets', () => {
  it('builds a release gate workflow with explicit policy and decision stages', () => {
    const decision: ReleaseCouncilDecision = {
      run_id: 'run-1',
      recommendation: 'CONDITIONAL_GO',
      risk_score: 42,
      composite_risk: 41,
      dimension_scores: [],
      blocking_issues: ['Need one more smoke pass'],
      conditions_for_go: ['Approve smoke run'],
      reasoning: 'Policy accepted with conditions',
      score_model_version: 2,
      input_snapshot: null,
      cluster_insights: [],
      baseline_diff: null,
      open_defects_by_component: [],
      human_override: null,
      overridden_by: null,
      original_recommendation: null,
      original_risk_score: null,
      override_audit: [],
      pass_rate: 86.2,
      build_number: '42',
      policy_id: 'policy-1',
      policy_version: 3,
      policy_level: 'project',
      rule_evaluations: [{ rule_id: 'r1', rule_name: 'Block on no-go', rule_type: 'threshold', passed: true, action: 'ALLOW', message: 'Within threshold', actual_value: 20, threshold_value: 30 }],
    }
    const workflow = buildReleaseGateWorkflow(decision)

    expect(workflow.stages.map(stage => stage.stage_name)).toEqual([
      'policy_evaluation',
      'cluster_review',
      'evidence_synthesis',
      'release_decision',
    ])
    // This decision carries no `created_at` (and no override): there is no
    // recorded time to show, so there is no event (HON-06).
    expect(workflow.events).toEqual([])
  })

  it('builds onboarding stages in the same order as the setup flow', () => {
    const workflow = buildOnboardingWorkflow({
      project_id: 'proj-1',
      steps: [
        { key: 'create_project', label: 'Create Project', description: 'Add a project', status: 'completed', completed_at: '2026-04-03T15:00:00Z' },
        { key: 'upload_run', label: 'Upload Run', description: 'Load a run', status: 'pending', completed_at: null },
        { key: 'view_intelligence', label: 'View Intelligence', description: 'Open the insights page', status: 'skipped', completed_at: null },
      ],
      completed_count: 1,
      total_count: 3,
      progress_pct: 33,
      is_complete: false,
    })

    expect(workflow.stageOrder).toEqual(['create_project', 'upload_run', 'view_intelligence'])
    expect(workflow.stages[0]?.status).toBe('completed')
    expect(workflow.stages[2]?.status).toBe('skipped')
  })

  it('builds a digest workflow and uses preview data when available', () => {
    const workflow = buildDigestWorkflow(
      [{ id: 'sub-1', user_id: 'u1', project_id: 'p1', saved_view_id: null, name: 'Weekly', schedule: 'WEEKLY', channel: 'email', is_active: true, is_paused: false, scope_type: 'project', scope_value: null, trigger_filter: 'all', send_when_unchanged: true, report_attachment: false, last_delivered_at: null, next_delivery_at: null, delivery_count: 2, created_at: '2026-04-03T15:00:00Z', updated_at: null }],
      [{ id: 'view-1', name: 'QA view', is_default: true, is_shared: true }],
      {
        project_name: 'Project A',
        period: 'weekly',
        generated_at: '2026-04-03T15:00:00Z',
        total_runs: 12,
        avg_pass_rate: 91.2,
        pass_rate_trend: 2.4,
        new_regressions: 3,
        top_blockers: ['DB pool exhaustion'],
        top_clusters: [{ label: 'DB', size: 5, criticality: 'HIGH' }],
        flaky_test_count: 1,
        release_decisions: [{ run_id: 'run-1', recommendation: 'NO_GO', risk_score: 72 }],
        action_items: ['Review blockers'],
      },
    )

    expect(workflow.stages.map(stage => stage.stage_name)).toContain('digest_compilation')
    expect(workflow.stages.find(stage => stage.stage_name === 'digest_compilation')?.status).toBe('completed')
  })

  it('builds a live integration health workflow with provider-specific stages', () => {
    const workflow = buildIntegrationHealthWorkflow(
      [
        { provider: 'jira', status: 'healthy', last_checked_at: '2026-04-03T15:00:00Z', message: null, response_ms: 200, consecutive_failures: 0, last_success_at: '2026-04-03T14:55:00Z' },
        { provider: 'smtp', status: 'down', last_checked_at: '2026-04-03T15:00:00Z', message: 'Timeout', response_ms: 900, consecutive_failures: 3, last_success_at: null },
      ],
      [{ id: 'h1', status: 'down', response_ms: 900, message: 'Timeout', auth_valid: false, payload_valid: false, checked_at: '2026-04-03T15:00:00Z' }],
      'smtp',
    )

    expect(workflow.stages[0]?.label).toBe('jira')
    expect(workflow.stages[workflow.stages.length - 1]?.stage_name).toBe('health_rollup')
    expect(workflow.events.length).toBeGreaterThan(0)
  })

  it('builds an AI evaluation workflow with a gate review stage', () => {
    const workflow = buildAIEvalWorkflow(
      {
        agreement: { total_feedback: 6, correct: 5, partially_correct: 0, incorrect: 1, agreement_rate: 0.83, period_days: 30 },
        drift: { task_type: 'classification', current: { accuracy: 0.92, f1_score: 0.9 }, previous: { accuracy: 0.88 }, drift: 0.04, drift_direction: 'improving' },
        recent_eval_runs: [{ id: 'eval-1', dataset_id: 'ds-1', model_name: 'gpt', task_type: 'classification', precision: 0.91, recall: 0.9, f1_score: 0.9, accuracy: 0.92, agreement_rate: 0.83, total_items: 10, correct_items: 9, fallback_used: false, evaluated_at: '2026-04-03T15:00:00Z', duration_ms: 200 }],
        model_versions: [{ id: 'mv-1', track: 'main', model_name: 'gpt', status: 'active', eval_accuracy: 0.92, created_at: '2026-04-03T15:00:00Z' }],
        feedback_summary: null,
        label_health: null,
      },
      [{ id: 'baseline-1', task_type: 'classification', agent_name: 'summary', prompt_version: '1', model_name: 'gpt', baseline_accuracy: 0.9, baseline_f1: 0.88, min_accuracy: 0.85, min_f1: 0.84, max_regression_pct: 0.1, created_at: '2026-04-03T15:00:00Z' }],
      {
        status: 'PASS',
        task_type: 'classification',
        agent_name: 'summary',
        current_metrics: { accuracy: 0.92, precision: 0.91, recall: 0.9, f1_score: 0.9, total: 10, correct: 9 },
        baseline_metrics: { accuracy: 0.9, precision: 0.88, recall: 0.87, f1_score: 0.88, min_accuracy: 0.85, min_f1: 0.84, max_regression_pct: 0.1, prompt_version: '1', model_name: 'gpt' },
        rule_results: [],
        evaluated_at: '2026-04-03T15:05:00Z',
      },
    )

    expect(workflow.stages.map(stage => stage.stage_name)).toContain('gate_review')
    expect(workflow.stages.find(stage => stage.stage_name === 'gate_review')?.status).toBe('completed')
  })

  it('builds an audit workflow from tenant observability and audit events', () => {
    const workflow = buildAuditWorkflow(
      {
        project_id: 'proj-1',
        period_days: 7,
        total_runs: 12,
        total_tests: 120,
        avg_pass_rate: 91.1,
        failed_runs: 2,
        ai_analyses_count: 4,
        release_decisions_count: 1,
        audit_events_count: 9,
      },
      [
        { source: 'access', action: 'login', actor_name: 'Anand', actor_id: 'u1', project_id: 'proj-1', detail: null, created_at: '2026-04-03T15:00:00Z', success: true },
        { source: 'settings', action: 'update', actor_name: 'Anand', actor_id: 'u1', project_id: 'proj-1', detail: null, created_at: '2026-04-03T15:01:00Z', success: true },
      ],
    )

    expect(workflow.stages.map(stage => stage.stage_name)).toContain('observability_export')
    expect(workflow.events.length).toBeGreaterThan(0)
  })

})

// ── The release timeline ─────────────────────────────────────────────────────
//
// An EMPTY input must produce a legible skipped/pending timeline rather than an
// empty or crashed one (the builder renders before its data arrives), and counts
// a stage reports must match the input it was given. (The page-ribbon builders
// these tests once covered beside it were deleted in the UX redesign P2.)
describe('workflowPresets — page timelines', () => {

  describe('buildReleaseWorkflow', () => {
    it('orders phases by order_index, not by array position', () => {
      const wf = buildReleaseWorkflow({
        phases: [
          { id: 'p2', name: 'Soak', order_index: 2, status: 'pending', phase_type: 'soak', description: null },
          { id: 'p1', name: 'Smoke', order_index: 1, status: 'completed', phase_type: 'smoke', description: null },
        ],
      } as never)

      // A release timeline that lists phases out of order misrepresents what
      // has and has not happened.
      expect(wf.stages.map(s => s.label)).toEqual(['Smoke', 'Soak'])
    })

    it('maps in_progress onto the running status the timeline renders', () => {
      const wf = buildReleaseWorkflow({
        phases: [
          { id: 'p1', name: 'Smoke', order_index: 1, status: 'in_progress', phase_type: 'smoke', description: null },
        ],
      } as never)

      expect(wf.stages[0]?.status).toBe('running')
    })

    it('handles a release with no phases', () => {
      const wf = buildReleaseWorkflow({ phases: [] } as never)
      expect(Array.isArray(wf.stages)).toBe(true)
    })
  })
})

// ── HON-06: every event time is a record's own time ──────────────────────────
//
// The builders stamped each event `now - n * 45 s` and rendered it as a real
// date, and attached literal "confidence" numbers (90/45, 95/65/25, the risk
// score). Each builder must now date an event only from a time its input
// carries, emit no event for an undated record, and attach no confidence the
// input does not measure.
describe('workflowPresets — events carry record times (HON-06)', () => {
  const decisionBase: ReleaseCouncilDecision = {
    run_id: 'run-1',
    recommendation: 'NO_GO',
    risk_score: 72,
    composite_risk: 70,
    dimension_scores: [{ dimension: 'user_impact', score: 80, weight: 0.3, label: 'User impact', weighted: 24, rationale: '' } as never],
    blocking_issues: [],
    conditions_for_go: [],
    reasoning: 'stored reasoning',
    score_model_version: 2,
    input_snapshot: null,
    cluster_insights: [],
    baseline_diff: null,
    open_defects_by_component: [],
    human_override: 'perf soak pending',
    overridden_by: 'u-1',
    original_recommendation: 'GO',
    original_risk_score: 30,
    override_audit: [
      {
        timestamp: '2026-10-02T11:15:00Z',
        actor_id: 'u-1',
        actor_name: 'priya',
        before_recommendation: 'GO',
        before_risk_score: 30,
        after_recommendation: 'NO_GO',
        reason: 'perf soak pending',
        policy_id: null,
        policy_version: null,
      },
    ],
    pass_rate: 91,
    build_number: '42',
    policy_id: null,
    policy_version: 1,
    policy_level: 'project',
    rule_evaluations: [],
    created_at: '2026-10-01T09:30:12Z',
  }

  // Fake timers: a builder that reads the clock gives different times at two
  // instants; one that reads its input gives the same.
  afterEach(() => {
    vi.useRealTimers()
  })

  function atTwoInstants<T>(build: () => T): [T, T] {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-14T12:00:00Z'))
    const first = build()
    vi.setSystemTime(new Date('2026-10-20T08:00:00Z'))
    const second = build()
    return [first, second]
  }

  it('eventsFromRecords drops undated and unparseable records and keeps the given times, newest first', () => {
    const events = eventsFromRecords([
      { event_type: 'a', stage_name: 's', timestamp: '2026-10-01T09:00:00Z' },
      { event_type: 'b', stage_name: 's', timestamp: null },
      { event_type: 'c', stage_name: 's', timestamp: 'not a date' },
      { event_type: 'd', stage_name: 's', timestamp: undefined },
      { event_type: 'e', stage_name: 's', timestamp: '2026-10-03T09:00:00Z' },
    ])

    expect(events.map(e => [e.event_type, e.timestamp])).toEqual([
      ['e', '2026-10-03T09:00:00Z'],
      ['a', '2026-10-01T09:00:00Z'],
    ])
  })

  it('release gate: the decision at its stored time, each override at its audit time with the actor', () => {
    const [first, second] = atTwoInstants(() => buildReleaseGateWorkflow(decisionBase))

    expect(first.events.map(e => [e.event_type, e.timestamp])).toEqual([
      ['decision_overridden', '2026-10-02T11:15:00Z'],
      ['stage_completed', '2026-10-01T09:30:12Z'],
    ])
    expect(first.events[0]?.detail).toMatchObject({ by: 'priya', change: 'GO → NO_GO', reason: 'perf soak pending' })
    // The stored decision, before the override: what the gate decided then.
    expect(first.events[1]?.detail).toMatchObject({ recommendation: 'GO', risk_score: 30 })
    expect(second.events).toEqual(first.events)
  })

  it('release gate: no stage shows the risk score (or a dimension score) as a confidence', () => {
    const workflow = buildReleaseGateWorkflow(decisionBase)
    for (const stage of workflow.stages) {
      expect(stage.confidence_score, stage.stage_name).toBeNull()
    }
  })

  it('release gate: a synthesised quick-look view has a pending, undated decision stage and no events', () => {
    const workflow = buildReleaseGateWorkflow({
      ...decisionBase,
      synthesized: true,
      created_at: null,
      override_audit: [],
      human_override: null,
    })
    const decisionStage = workflow.stages.find(s => s.stage_name === 'release_decision')
    expect(decisionStage?.status).toBe('pending')
    expect(decisionStage?.completed_at).toBeNull()
    expect(workflow.events).toEqual([])
  })

  it('release phases: started at actual_start, ended at actual_end, nothing for a phase not started', () => {
    const [first, second] = atTwoInstants(() => buildReleaseWorkflow({
      phases: [
        { id: 'p1', name: 'Smoke', order_index: 1, status: 'completed', phase_type: 'smoke', description: null, actual_start: '2026-09-01T08:00:00Z', actual_end: '2026-09-01T10:00:00Z' },
        { id: 'p2', name: 'Soak', order_index: 2, status: 'pending', phase_type: 'soak', description: null, actual_start: null, actual_end: null },
      ],
    } as never))

    expect(first.events.map(e => [e.event_type, e.stage_name, e.timestamp])).toEqual([
      ['stage_completed', 'release_phase:p1', '2026-09-01T10:00:00Z'],
      ['stage_started', 'release_phase:p1', '2026-09-01T08:00:00Z'],
    ])
    expect(second.events).toEqual(first.events)
  })

  it('onboarding: a completed step at its completed_at; a pending step has no event', () => {
    const workflow = buildOnboardingWorkflow({
      project_id: 'proj-1',
      steps: [
        { key: 'create_project', label: 'Create Project', description: '', status: 'completed', completed_at: '2026-04-03T15:00:00Z' },
        { key: 'upload_run', label: 'Upload Run', description: '', status: 'pending', completed_at: null },
      ],
      completed_count: 1,
      total_count: 2,
      progress_pct: 50,
      is_complete: false,
    })

    expect(workflow.events.map(e => [e.stage_name, e.timestamp])).toEqual([
      ['create_project', '2026-04-03T15:00:00Z'],
    ])
  })

  it('digests: subscription and delivery times from the subscription, compilation from the preview; no confidence', () => {
    const sub = {
      id: 'sub-1', user_id: 'u1', project_id: 'p1', saved_view_id: null, name: 'Weekly', schedule: 'WEEKLY' as const,
      channel: 'email' as const, is_active: true, is_paused: false, scope_type: 'project' as const, scope_value: null,
      trigger_filter: 'all' as const, send_when_unchanged: true, report_attachment: false,
      last_delivered_at: '2026-10-06T07:00:00Z', next_delivery_at: null, delivery_count: 3,
      created_at: '2026-09-01T12:00:00Z', updated_at: null,
    }
    const preview = {
      project_name: 'A', period: 'weekly', generated_at: '2026-10-14T09:00:00Z', total_runs: 1, avg_pass_rate: 90,
      pass_rate_trend: 0, new_regressions: 2, top_blockers: [], top_clusters: [], flaky_test_count: 0,
      release_decisions: [], action_items: [],
    }
    const [first, second] = atTwoInstants(() => buildDigestWorkflow([sub as never], [], preview as never))

    expect(first.events.map(e => [e.event_type, e.stage_name, e.timestamp])).toEqual([
      ['stage_completed', 'digest_compilation', '2026-10-14T09:00:00Z'],
      ['stage_completed', 'delivery', '2026-10-06T07:00:00Z'],
      ['subscription_created', 'signal_collection', '2026-09-01T12:00:00Z'],
    ])
    expect(second.events).toEqual(first.events)
    for (const stage of first.stages) expect(stage.confidence_score, stage.stage_name).toBeNull()
    expect(first.stages.find(s => s.stage_name === 'delivery')?.status).toBe('completed')

    // Subscribed but never delivered: the delivery stage is pending, undated.
    const undelivered = buildDigestWorkflow([{ ...sub, last_delivered_at: null } as never], [], null)
    expect(undelivered.stages.find(s => s.stage_name === 'delivery')?.status).toBe('pending')
    expect(undelivered.events.map(e => e.stage_name)).toEqual(['signal_collection'])
    for (const stage of undelivered.stages) expect(stage.confidence_score, stage.stage_name).toBeNull()
  })

  it('integration health: probe events at last_checked_at / checked_at; no confidence per status', () => {
    const workflow = buildIntegrationHealthWorkflow(
      [
        { provider: 'jira', status: 'healthy', last_checked_at: '2026-10-14T08:00:00Z', message: null, response_ms: 200, consecutive_failures: 0, last_success_at: '2026-10-14T08:00:00Z' },
        { provider: 'smtp', status: 'down', last_checked_at: '2026-10-14T08:05:00Z', message: 'Timeout', response_ms: 900, consecutive_failures: 3, last_success_at: null },
        { provider: 'slack', status: 'degraded', last_checked_at: null, message: null, response_ms: null, consecutive_failures: 0, last_success_at: null },
      ],
      [{ id: 'h1', status: 'down', response_ms: 900, message: 'Timeout', auth_valid: false, payload_valid: false, checked_at: '2026-10-13T22:00:00Z' }],
      'smtp',
    )

    expect(workflow.events.map(e => [e.stage_name, e.event_type, e.timestamp])).toEqual([
      ['probe:smtp', 'stage_failed', '2026-10-14T08:05:00Z'],
      ['probe:jira', 'stage_completed', '2026-10-14T08:00:00Z'],
      ['history:smtp', 'stage_failed', '2026-10-13T22:00:00Z'],
    ])
    for (const stage of workflow.stages) expect(stage.confidence_score, stage.stage_name).toBeNull()
    // Never probed: pending, not a status read off a probe that never ran.
    expect(workflow.stages.find(s => s.stage_name === 'probe:slack')?.status).toBe('pending')
  })

  it('AI evaluation: keeps the measured accuracy, dates events from eval runs, gate, baselines and model versions', () => {
    const workflow = buildAIEvalWorkflow(
      {
        agreement: null,
        drift: { task_type: 'classification', current: { accuracy: 0.92, f1_score: 0.9 }, previous: { accuracy: 0.88 }, drift: 0.04, drift_direction: 'improving' },
        recent_eval_runs: [{ id: 'eval-1', dataset_id: 'ds-1', model_name: 'gpt', task_type: 'classification', precision: 0.91, recall: 0.9, f1_score: 0.9, accuracy: 0.92, agreement_rate: 0.83, total_items: 10, correct_items: 9, fallback_used: false, evaluated_at: '2026-10-10T15:00:00Z', duration_ms: 200 }],
        model_versions: [{ id: 'mv-1', track: 'main', model_name: 'gpt', status: 'active', eval_accuracy: 0.92, created_at: '2026-09-20T10:00:00Z' }],
        feedback_summary: null,
        label_health: null,
      },
      [{ id: 'b-1', task_type: 'classification', agent_name: 'summary', prompt_version: '1', model_name: 'gpt', baseline_accuracy: 0.9, baseline_f1: 0.88, min_accuracy: 0.85, min_f1: 0.84, max_regression_pct: 0.1, created_at: null }],
      {
        status: 'FAIL', task_type: 'classification', agent_name: 'summary',
        current_metrics: { accuracy: 0.81, precision: 0.8, recall: 0.8, f1_score: 0.8, total: 10, correct: 8 },
        baseline_metrics: null, rule_results: [], evaluated_at: '2026-10-11T09:00:00Z',
      },
    )

    expect(workflow.events.map(e => [e.stage_name, e.event_type, e.timestamp])).toEqual([
      ['gate_review', 'stage_failed', '2026-10-11T09:00:00Z'],
      ['quality_evaluation', 'stage_completed', '2026-10-10T15:00:00Z'],
      ['model_governance', 'model_registered', '2026-09-20T10:00:00Z'],
    ])
    // Measured: the evaluation's own accuracy, not a literal.
    expect(workflow.stages.find(s => s.stage_name === 'quality_evaluation')?.confidence_score).toBe(92)
    expect(workflow.stages.find(s => s.stage_name === 'gate_review')?.confidence_score).toBe(81)
    expect(workflow.stages.find(s => s.stage_name === 'dataset_refresh')?.confidence_score).toBeNull()
  })

  it('audit: each event time equals its audit row time; the rollup adds no event', () => {
    const rows = [
      { source: 'settings', action: 'update_ai_config', actor_name: 'Anand', actor_id: 'u1', project_id: 'p', detail: null, created_at: '2026-10-14T10:01:00Z', success: true },
      { source: 'access', action: 'login', actor_name: 'Anand', actor_id: 'u1', project_id: 'p', detail: null, created_at: '2026-10-14T10:00:00Z', success: true },
      { source: 'report', action: 'share_link_created', actor_name: null, actor_id: null, project_id: 'p', detail: null, created_at: null, success: true },
    ]
    const obs = { project_id: 'p', period_days: 7, total_runs: 3, total_tests: 30, avg_pass_rate: 90, failed_runs: 1, ai_analyses_count: 0, release_decisions_count: 0, audit_events_count: 3 }
    const [first, second] = atTwoInstants(() => buildAuditWorkflow(obs, rows))

    expect(first.events.map(e => [e.stage_name, e.timestamp, e.detail?.action])).toEqual([
      ['configuration_changes', '2026-10-14T10:01:00Z', 'update_ai_config'],
      ['access_events', '2026-10-14T10:00:00Z', 'login'],
    ])
    expect(second.events).toEqual(first.events)
  })
})
