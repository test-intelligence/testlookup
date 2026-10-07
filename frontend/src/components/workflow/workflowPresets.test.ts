import { describe, expect, it } from 'vitest'
import type { ReleaseCouncilDecision } from '@/services/releaseCouncilService'
import {
  buildAIEvalWorkflow,
  buildAuditWorkflow,
  buildDigestWorkflow,
  buildIntegrationHealthWorkflow,
  buildOnboardingWorkflow,
  buildReleaseGateWorkflow,
  buildReleaseWorkflow,
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
    expect(workflow.events[0]?.event_type).toBe('stage_started')
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
