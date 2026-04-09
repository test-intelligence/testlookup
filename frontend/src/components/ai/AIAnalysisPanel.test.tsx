/**
 * Tests for the rewritten AIAnalysisPanel component.
 *
 * Verifies:
 * - Idle state shows "Analyse Root Cause" button
 * - Loading state shows spinner and honest messaging
 * - Error state shows retry button and resets on click
 * - Result state renders real tools_used from backend
 * - Fast-path (empty tools_used) shows classifier message
 * - Re-analyse button resets to idle state
 */
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import AIAnalysisPanel from './AIAnalysisPanel'

// ── Mocks ────────────────────────────────────────────────────────────────────

vi.mock('@/services/aiService', () => ({
  aiService: {
    analyze: vi.fn(),
    createJiraTicket: vi.fn(),
    getAnalysis: vi.fn().mockResolvedValue(null),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: {
    error: vi.fn(),
    success: vi.fn(),
  },
}))

const MOCK_ANALYSIS = {
  test_case_id: 'tc-1',
  root_cause_summary: 'Database connection pool exhausted at test execution time.',
  failure_category: 'INFRASTRUCTURE',
  backend_error_found: true,
  pod_issue_found: false,
  is_flaky: false,
  confidence_score: 92,
  recommended_actions: ['Investigate DB connection pool', 'Check recent infra changes'],
  evidence_references: [
    { source: 'splunk', reference_id: 'log-1', excerpt: 'ConnectionTimeoutException' },
  ],
  tools_used: ['fetch_allure_stacktrace', 'query_splunk_logs'],
  role_actions: {
    qa: 'Re-run the test in isolation and capture the full stack trace.',
    developer: 'Review connection pool config and recent code changes to DB layer.',
    sre: 'Check pod resource limits and recent deployment events.',
    release_manager: 'Hold release until infrastructure team confirms pool fix is deployed.',
  },
  confidence_why: {
    evidence_count: 1,
    data_sources: ['splunk'],
    is_llm_inference: true,
    investigation_depth: 'standard',
  },
  llm_provider: 'ollama',
  llm_model: 'qwen2.5:7b',
  requires_human_review: false,
}

const DEFAULT_PROPS = {
  testCaseId: 'tc-1',
  testName: 'testPaymentGatewayTimeout',
  runId: 'run-1',
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe('AIAnalysisPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  async function renderResolvedResult(analysis: Partial<typeof MOCK_ANALYSIS> = {}) {
    const { aiService } = await import('@/services/aiService')
    ;(aiService.analyze as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...MOCK_ANALYSIS,
      ...analysis,
    })
    render(<AIAnalysisPanel {...DEFAULT_PROPS} />)
    fireEvent.click(await screen.findByRole('button', { name: /Analyse Root Cause/i }))
    await waitFor(() => expect(screen.getByText(/%/)).toBeInTheDocument())
  }

  describe('idle state', () => {
    it('shows Analyse Root Cause button', async () => {
      render(<AIAnalysisPanel {...DEFAULT_PROPS} />)
      expect(await screen.findByRole('button', { name: /Analyse Root Cause/i })).toBeInTheDocument()
    })

    it('shows AI Root Cause Analysis heading', async () => {
      render(<AIAnalysisPanel {...DEFAULT_PROPS} />)
      expect(await screen.findByText('AI Root Cause Analysis')).toBeInTheDocument()
    })
  })

  describe('loading state', () => {
    it('shows Investigating message and honest tool disclosure', async () => {
      const { aiService } = await import('@/services/aiService')
      ;(aiService.analyze as ReturnType<typeof vi.fn>).mockImplementation(
        () => new Promise(() => {}), // never resolves
      )
      render(<AIAnalysisPanel {...DEFAULT_PROPS} />)
      fireEvent.click(await screen.findByRole('button', { name: /Analyse Root Cause/i }))

      await waitFor(() => {
        expect(screen.getByText(/Investigating/i)).toBeInTheDocument()
      })
      expect(
        screen.getByText(/Actual tools used will be shown when complete/i),
      ).toBeInTheDocument()
    })
  })

  describe('error state', () => {
    it('shows Analysis Failed heading when backend throws', async () => {
      const { aiService } = await import('@/services/aiService')
      ;(aiService.analyze as ReturnType<typeof vi.fn>).mockRejectedValue(
        new Error('LLM unavailable'),
      )
      render(<AIAnalysisPanel {...DEFAULT_PROPS} />)
      fireEvent.click(await screen.findByRole('button', { name: /Analyse Root Cause/i }))

      await waitFor(() => {
        expect(screen.getByText(/Analysis Failed/i)).toBeInTheDocument()
      })
      expect(screen.getByRole('button', { name: /Try Again/i })).toBeInTheDocument()
    })

    it('Try Again button returns to idle state', async () => {
      const { aiService } = await import('@/services/aiService')
      ;(aiService.analyze as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('fail'))
      render(<AIAnalysisPanel {...DEFAULT_PROPS} />)
      fireEvent.click(await screen.findByRole('button', { name: /Analyse Root Cause/i }))

      await waitFor(() => screen.getByRole('button', { name: /Try Again/i }))
      fireEvent.click(screen.getByRole('button', { name: /Try Again/i }))

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Analyse Root Cause/i })).toBeInTheDocument()
      })
    })
  })

  describe('result state', () => {
    it('renders confidence score', async () => {
      await renderResolvedResult()
      expect(screen.getByText('92%')).toBeInTheDocument()
    })

    it('renders failure category text', async () => {
      await renderResolvedResult()
      expect(screen.getByText('INFRASTRUCTURE')).toBeInTheDocument()
    })

    it('renders root cause summary', async () => {
      await renderResolvedResult()
      expect(
        screen.getByText('Database connection pool exhausted at test execution time.'),
      ).toBeInTheDocument()
    })

    it('renders actual investigation steps from tools_used', async () => {
      await renderResolvedResult()
      // TOOL_LABELS['fetch_allure_stacktrace'] = 'Fetched stack trace'
      expect(screen.getByText('Fetched stack trace')).toBeInTheDocument()
      // TOOL_LABELS['query_splunk_logs'] = 'Queried Splunk logs'
      expect(screen.getByText('Queried Splunk logs')).toBeInTheDocument()
    })

    it('shows fast-path message when tools_used is empty', async () => {
      await renderResolvedResult({
        tools_used: [],
        confidence_why: { evidence_count: 0, data_sources: [], is_llm_inference: false, investigation_depth: 'fast_path' },
      })
      expect(screen.getAllByText(/Fast-path classifier/i).length).toBeGreaterThan(0)
      expect(screen.getByText(/Deterministic path/i)).toBeInTheDocument()
    })

    it('shows recommended actions', async () => {
      await renderResolvedResult()
      expect(screen.getByText('Investigate DB connection pool')).toBeInTheDocument()
    })

    it('Re-analyse button resets to idle state', async () => {
      await renderResolvedResult()
      fireEvent.click(screen.getByRole('button', { name: /Re-analyse/i }))
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Analyse Root Cause/i })).toBeInTheDocument()
      })
    })

    it('Create Jira Defect button is disabled when requires_human_review is true', async () => {
      await renderResolvedResult({ requires_human_review: true })
      const jiraButton = screen.getByRole('button', { name: /Create Jira Defect/i })
      expect(jiraButton).toBeDisabled()
    })

    it('Create Jira Defect button is disabled when no project key provided', async () => {
      const { aiService } = await import('@/services/aiService')
      ;(aiService.analyze as ReturnType<typeof vi.fn>).mockResolvedValue(MOCK_ANALYSIS)
      render(<AIAnalysisPanel {...DEFAULT_PROPS} />)  // no projectKey prop
      fireEvent.click(await screen.findByRole('button', { name: /Analyse Root Cause/i }))

      await waitFor(() => screen.getByText('92%'))
      const jiraButton = screen.getByRole('button', { name: /Create Jira Defect/i })
      expect(jiraButton).toBeDisabled()
    })
  })

  describe('ConfidencePanel', () => {
    it('shows "Confidence + Why" heading', async () => {
      await renderResolvedResult()
      expect(screen.getByText('Confidence + Why')).toBeInTheDocument()
    })

    it('shows Investigation Trail heading', async () => {
      await renderResolvedResult()
      expect(screen.getByText('Investigation Trail')).toBeInTheDocument()
    })

    it('shows Standard ReAct investigation label for standard depth', async () => {
      await renderResolvedResult()
      expect(screen.getByText('Standard ReAct investigation')).toBeInTheDocument()
    })

    it('shows Fast-path classifier label when investigation_depth is fast_path', async () => {
      await renderResolvedResult({
        tools_used: [],
        confidence_why: { evidence_count: 0, data_sources: [], is_llm_inference: false, investigation_depth: 'fast_path' },
      })
      expect(screen.getAllByText('Fast-path classifier').length).toBeGreaterThan(0)
    })

    it('shows Deep ReAct investigation label for deep depth', async () => {
      await renderResolvedResult({
        confidence_why: { ...MOCK_ANALYSIS.confidence_why, investigation_depth: 'deep' },
      })
      expect(screen.getByText('Deep ReAct investigation')).toBeInTheDocument()
    })

    it('shows LLM inference label when is_llm_inference is true', async () => {
      await renderResolvedResult()
      expect(screen.getByText(/LLM inference used/i)).toBeInTheDocument()
    })

    it('shows Deterministic label when is_llm_inference is false', async () => {
      await renderResolvedResult({
        tools_used: [],
        confidence_why: { evidence_count: 0, data_sources: [], is_llm_inference: false, investigation_depth: 'fast_path' },
      })
      expect(screen.getByText(/Deterministic path/i)).toBeInTheDocument()
    })

    it('shows evidence count badge', async () => {
      await renderResolvedResult()
      expect(screen.getByText('1 evidence item')).toBeInTheDocument()
    })

    it('shows data source badge for splunk', async () => {
      await renderResolvedResult()
      expect(screen.getByText('Splunk')).toBeInTheDocument()
    })

    it('shows "No evidence gathered" when evidence_count is 0 and no data_sources', async () => {
      await renderResolvedResult({
        tools_used: [],
        confidence_why: { evidence_count: 0, data_sources: [], is_llm_inference: false, investigation_depth: 'fast_path' },
      })
      expect(screen.getByText('No evidence gathered')).toBeInTheDocument()
    })

    it('shows high-confidence explanation for score >= 80', async () => {
      await renderResolvedResult()
      expect(screen.getByText(/High confidence.*root cause is well-supported/i)).toBeInTheDocument()
    })

    it('shows medium-confidence explanation for score between 60 and 79', async () => {
      const { aiService } = await import('@/services/aiService')
      ;(aiService.analyze as ReturnType<typeof vi.fn>).mockResolvedValue({
        ...MOCK_ANALYSIS,
        confidence_score: 65,
      })
      render(<AIAnalysisPanel {...DEFAULT_PROPS} />)
      fireEvent.click(await screen.findByRole('button', { name: /Analyse Root Cause/i }))
      await waitFor(() => expect(screen.getByText('65%')).toBeInTheDocument())
      expect(screen.getByText(/Medium confidence.*some evidence gathered/i)).toBeInTheDocument()
    })

    it('shows low-confidence explanation for score < 60', async () => {
      const { aiService } = await import('@/services/aiService')
      ;(aiService.analyze as ReturnType<typeof vi.fn>).mockResolvedValue({
        ...MOCK_ANALYSIS,
        confidence_score: 40,
      })
      render(<AIAnalysisPanel {...DEFAULT_PROPS} />)
      fireEvent.click(await screen.findByRole('button', { name: /Analyse Root Cause/i }))
      await waitFor(() => expect(screen.getByText('40%')).toBeInTheDocument())
      expect(screen.getByText(/Low confidence.*insufficient telemetry/i)).toBeInTheDocument()
    })

    it('derives confidence_why from tools_used when field is absent', async () => {
      const { role_actions: _ra, confidence_why: _cw, ...withoutConfidenceWhy } = MOCK_ANALYSIS
      const { aiService } = await import('@/services/aiService')
      ;(aiService.analyze as ReturnType<typeof vi.fn>).mockResolvedValue(withoutConfidenceWhy)
      render(<AIAnalysisPanel {...DEFAULT_PROPS} />)
      fireEvent.click(await screen.findByRole('button', { name: /Analyse Root Cause/i }))
      await waitFor(() => expect(screen.getByText('92%')).toBeInTheDocument())
      // Should still render — no crash, confidence score visible
      expect(screen.getByText('Confidence + Why')).toBeInTheDocument()
    })
  })

  describe('RoleActionsPanel', () => {
    it('shows Role-Aware Actions heading when role actions are present', async () => {
      await renderResolvedResult()
      expect(screen.getByText('Role-Aware Actions')).toBeInTheDocument()
    })

    it('renders QA Engineer role card', async () => {
      await renderResolvedResult()
      expect(screen.getAllByText('QA')).toHaveLength(2)
      expect(screen.getByText('Re-run the test in isolation and capture the full stack trace.')).toBeInTheDocument()
    })

    it('renders Developer role card', async () => {
      await renderResolvedResult()
      expect(screen.getAllByText('Developer')).toHaveLength(2)
      expect(screen.getByText('Review connection pool config and recent code changes to DB layer.')).toBeInTheDocument()
    })

    it('renders SRE / Platform role card', async () => {
      await renderResolvedResult()
      expect(screen.getAllByText('SRE')).toHaveLength(2)
      expect(screen.getByText('Check pod resource limits and recent deployment events.')).toBeInTheDocument()
    })

    it('renders Release Manager role card', async () => {
      await renderResolvedResult()
      expect(screen.getAllByText('Release Manager')).toHaveLength(2)
      expect(screen.getByText('Hold release until infrastructure team confirms pool fix is deployed.')).toBeInTheDocument()
    })

    it('does not render Role-Aware Actions when all role actions are empty', async () => {
      await renderResolvedResult({
        role_actions: { qa: '', developer: '', sre: '', release_manager: '' },
      })
      expect(screen.queryByText('Role-Aware Actions')).not.toBeInTheDocument()
    })

    it('omits a role card when that role action is empty', async () => {
      await renderResolvedResult({
        role_actions: {
          qa: 'Re-run the test.',
          developer: '',
          sre: '',
          release_manager: '',
        },
      })
      expect(screen.getByText('Role-Aware Actions')).toBeInTheDocument()
      expect(screen.getAllByText('QA')).toHaveLength(2)
      expect(screen.queryAllByText('Developer')).toHaveLength(0)
      expect(screen.queryAllByText('SRE')).toHaveLength(0)
      expect(screen.queryAllByText('Release Manager')).toHaveLength(0)
    })

    it('does not render Role-Aware Actions when role_actions is absent', async () => {
      const { role_actions: _ra, ...withoutRoleActions } = MOCK_ANALYSIS
      const { aiService } = await import('@/services/aiService')
      ;(aiService.analyze as ReturnType<typeof vi.fn>).mockResolvedValue(withoutRoleActions)
      render(<AIAnalysisPanel {...DEFAULT_PROPS} />)
      fireEvent.click(await screen.findByRole('button', { name: /Analyse Root Cause/i }))
      await waitFor(() => expect(screen.getByText('92%')).toBeInTheDocument())
      expect(screen.queryByText('Role-Aware Actions')).not.toBeInTheDocument()
    })
  })
})
