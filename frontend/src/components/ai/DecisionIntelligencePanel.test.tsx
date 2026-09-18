import { fireEvent, render, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type {
  DecisionIntelligence,
  DecisionReportAttempt,
  DecisionReportVerification,
} from '@/services/runIntelligenceService'
import DecisionIntelligencePanel from './DecisionIntelligencePanel'

function report(overrides: Partial<DecisionIntelligence> = {}): DecisionIntelligence {
  return {
    schema_version: 1,
    status: 'complete',
    generated_at: '2026-08-11T19:00:00Z',
    metrics: {
      total_tests: 10,
      passed_tests: 7,
      failed_tests: 2,
      broken_tests: 1,
      pass_rate: 70,
      failure_cluster_count: 1,
      flaky_finding_count: 1,
      test_health_finding_count: 1,
    },
    failure_clusters: [{ cluster_id: 'c1', label: 'Authentication', size: 2 }],
    deep_findings: {},
    flaky_findings: [{ test_case_id: 't1', test_name: 'login retry', recommendation: 'Quarantine' }],
    test_health_findings: [{ test_case_id: 't2', health_score: 62, recommendation: 'Refactor' }],
    release_decision: { recommendation: 'NO_GO', risk_score: 78, reasoning: 'Blocking regressions.' },
    quality_review: {
      missing_or_failed_specialists: [],
      contradictions: [],
      gap_report: null,
      refined_report: null,
      requires_human_review: false,
    },
    source_stages: ['release_risk', 'decision_report'],
    evidence_bundle_sha256: 'a'.repeat(64),
    verification: {
      status: 'passed',
      checks: [{ name: 'durable_snapshot_binding', status: 'pass' }],
      repairs: [],
    },
    ...overrides,
  }
}

const passed: DecisionReportVerification = {
  status: 'passed',
  checks: [{ name: 'release_policy_replay', status: 'pass' }],
}
const published: DecisionReportAttempt = {
  pipeline_run_id: 'pipeline-1',
  status: 'published',
  verification_status: 'passed',
  at: '2026-08-11T19:01:00Z',
}
const failed: DecisionReportVerification = {
  status: 'failed',
  checks: [{ name: 'release_policy_replay', status: 'fail' }],
  unresolved_failures: ['release_policy_replay'],
}
const rejected: DecisionReportAttempt = {
  pipeline_run_id: 'pipeline-2',
  status: 'rejected',
  verification_status: 'failed',
  at: '2026-08-11T19:02:00Z',
}

function renderPanel(
  retained: DecisionIntelligence | null | undefined,
  verification: DecisionReportVerification | null | undefined,
  attempt: DecisionReportAttempt | null | undefined,
  versionProps: Partial<ComponentProps<typeof DecisionIntelligencePanel>> = {},
) {
  return render(
    <MemoryRouter>
      <DecisionIntelligencePanel
        runId="run-1"
        report={retained}
        latestVerification={verification}
        latestAttempt={attempt}
        {...versionProps}
      />
    </MemoryRouter>,
  )
}

describe('DecisionIntelligencePanel', () => {
  it('renders a verified report only when all verification scopes agree', () => {
    renderPanel(report(), passed, published)
    expect(screen.getByRole('status')).toBeTruthy()
    expect(screen.getByText('Verified', { selector: 'span' })).toBeTruthy()
    expect(screen.getByText('NO_GO')).toBeTruthy()
    const disclosure = screen.getByRole('button', { name: 'Verification and provenance' })
    expect(disclosure.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(disclosure)
    expect(disclosure.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText(/Report checks: 1 passed, 0 failed/)).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Inspect evidence' }).getAttribute('href')).toBe('/deep-investigate/run-1')
    expect(screen.getByRole('link', { name: 'Review policy and overrides' }).getAttribute('href')).toBe('/release-gate/run-1')
  })

  it('shows report-level quality evaluation status and unavailable metrics', () => {
    renderPanel(report({
      verification: {
        status: 'passed',
        checks: [{ name: 'report_level_quality_evaluation', status: 'pass' }],
        repairs: [],
        report_evaluation: {
          status: 'warn',
          metrics: { citation_validity: 1, groundedness: 1 },
          unavailable_metrics: ['calibration', 'utility'],
        },
      },
    }), passed, published)
    fireEvent.click(screen.getByRole('button', { name: 'Verification and provenance' }))
    expect(screen.getByText(/Report quality gate: WARN/)).toBeTruthy()
    expect(screen.getByText(/calibration, utility not evaluated/)).toBeTruthy()
  })

  it('separates typed claims and proposed actions for decision makers', () => {
    renderPanel(report({
      claims: [
        {
          claim_id: 'fact-1', kind: 'fact', text: '2 of 10 tests failed.', confidence: 1,
          confidence_basis: 'Deterministic metric snapshot.', evidence: [{ id: 'bundle-1' }],
          counter_evidence: [], source_stage: 'decision_report',
        },
        {
          claim_id: 'inference-1', kind: 'inference', text: 'Release is blocked.', confidence: 0.95,
          confidence_basis: 'Release policy evaluation.', evidence: [{ id: 'bundle-1' }],
          counter_evidence: [{ id: 'quality-1' }], source_stage: 'release_risk', hypothesis: true,
        },
      ],
      proposed_actions: [
        {
          action_id: 'action-1', title: 'Investigate release blocker', owner: 'release_owner',
          rationale: 'Resolve the failed regression.', evidence: [{ id: 'bundle-1' }], risk: 'high',
          required_permission: 'release_review', idempotency_key: 'action:action-1', status: 'proposed',
        },
      ],
    }), passed, published)
    expect(screen.getByText(/Facts/)).toBeTruthy()
    expect(screen.getByText(/Inferences/)).toBeTruthy()
    expect(screen.getByText('2 of 10 tests failed.')).toBeTruthy()
    expect(screen.getByText('Investigate release blocker')).toBeTruthy()
    expect(screen.getByText(/release_owner/)).toBeTruthy()
    const evidenceTrigger = screen.getByRole('button', { name: 'Inspect evidence for fact-1' })
    evidenceTrigger.focus()
    fireEvent.click(evidenceTrigger)
    expect(screen.getByRole('dialog')).toBeTruthy()
    expect(screen.getByText('bundle-1')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Close evidence' })).toHaveFocus()
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(evidenceTrigger).toHaveFocus()
  })

  it('does not call missing specialists an analysis gap', () => {
    renderPanel(report({
      status: 'degraded',
      quality_review: {
        missing_or_failed_specialists: ['test_health'],
        contradictions: [{ field: 'release' }],
        gap_report: null,
        refined_report: null,
        requires_human_review: true,
        data_quality_flags: [{
          code: 'historical_baseline_unavailable',
          severity: 'warning',
          detail: 'No immutable historical baseline was present.',
        }],
      },
    }), passed, published)
    // status==='degraded' means a specialist stage was missing or the payload
    // was truncated. It is NOT a statement about analysis coverage, and this
    // report carries no gap_report at all.
    expect(screen.getByText('Verified with missing specialist evidence')).toBeTruthy()
    expect(screen.queryByText(/not analysed/)).toBeNull()
    expect(screen.getByText(/Missing specialists: test_health/)).toBeTruthy()
    expect(screen.getByText(/1 evidence contradiction/)).toBeTruthy()
    expect(screen.getByText(/Data quality: historical_baseline_unavailable/)).toBeTruthy()
  })

  it('says how many failures were never analysed, on an otherwise clean report', () => {
    // The defect: status is 'complete' (every specialist ran, nothing was
    // truncated) so this rendered a green "Verified" -- while the release
    // recommendation rested on 1 analysed failure out of 50. The pipeline
    // computed exactly that and threw it away.
    renderPanel(report({
      status: 'complete',
      quality_review: {
        missing_or_failed_specialists: [],
        contradictions: [],
        gap_report: {
          failed_count: 50,
          analyzed_count: 1,
          skipped_count: 49,
          errored_count: 0,
          coverage_ratio: 0.02,
          integrity_ok: true,
          inconclusive_count: 0,
          no_evidence_count: 0,
          gaps: [],
        },
        refined_report: null,
        requires_human_review: false,
        data_quality_flags: [],
      },
    }), passed, published)
    expect(screen.getByText('Verified — 49 of 50 failures not analysed')).toBeTruthy()
    expect(screen.getByText(/never analysed/)).toBeTruthy()
  })

  it('shows the retained verified report as stale after a rejected attempt', () => {
    renderPanel(report(), failed, rejected)
    expect(screen.getAllByRole('alert')).toHaveLength(1)
    expect(screen.getByText(/Showing the last verified report/)).toBeTruthy()
    expect(screen.getByText('NO_GO')).toBeTruthy()
    expect(screen.getByText(/Failed checks: release_policy_replay/)).toBeTruthy()
  })

  it('fails closed when the latest attempt is rejected without a retained report', () => {
    renderPanel(null, failed, rejected)
    expect(screen.getByText(/No verified decision report is available/)).toBeTruthy()
    expect(screen.queryByText('NO_GO')).toBeNull()
    expect(screen.queryByText(/Risk 78/)).toBeNull()
  })

  it('treats a rejected attempt with an unverified retained payload as no verified report', () => {
    renderPanel(report({ verification: { status: 'failed' } }), failed, rejected)
    expect(screen.getByText(/No verified decision report is available/)).toBeTruthy()
    expect(screen.queryByText('NO_GO')).toBeNull()
  })

  it('fails closed while terminal verification is pending', () => {
    renderPanel(report({ verification: { status: 'pending' } }), { status: 'pending' }, null)
    expect(screen.getByText('Verification pending')).toBeTruthy()
    expect(screen.getByText(/No decision-ready recommendation/)).toBeTruthy()
    expect(screen.queryByText('NO_GO')).toBeNull()
  })

  it('fails closed when verification metadata is missing or inconsistent', () => {
    renderPanel(report(), passed, rejected)
    expect(screen.getByText('Verification unavailable')).toBeTruthy()
    expect(screen.getByText(/not decision-ready/)).toBeTruthy()
    expect(screen.queryByText('NO_GO')).toBeNull()
  })

  it('allows an authorized user to select an immutable report version', () => {
    const onSelect = vi.fn()
    renderPanel(report(), passed, published, {
      reportVersion: { report_id: 'report-2', report_version: 2, status: 'published', generated_at: '2026-08-12T19:00:00Z' },
      reportVersions: [
        { report_id: 'report-2', report_version: 2, status: 'published', generated_at: '2026-08-12T19:00:00Z' },
        { report_id: 'report-1', report_version: 1, status: 'published', generated_at: '2026-08-11T19:00:00Z' },
      ],
      onSelectReportVersion: onSelect,
      selectedReportVersion: 2,
    })
    const select = screen.getByRole('combobox', { name: 'Decision report version' })
    expect(select).toHaveValue('2')
    fireEvent.change(select, { target: { value: '1' } })
    expect(onSelect).toHaveBeenCalledWith(1)
  })
})
