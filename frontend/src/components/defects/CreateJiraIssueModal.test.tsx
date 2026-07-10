/**
 * CreateJiraIssueModal — PMF US-6.1 / US-6.3 dialog tests, following the
 * US-2.4 modal test pattern (mock hooks + service, exercise the dialog).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import CreateJiraIssueModal, { jiraUnavailableCopy } from './CreateJiraIssueModal'
import type { JiraDefectMetadata, JiraDefectPreview } from '@/services/defectJiraService'

vi.mock('@/hooks/useJiraDefects', () => ({
  useJiraDefectMetadata: vi.fn(),
  useJiraDefectPreview: vi.fn(),
}))

vi.mock('@/services/defectJiraService', () => ({
  defectJiraService: {
    create: vi.fn(),
  },
}))

const METADATA: JiraDefectMetadata = {
  available: true,
  reason: null,
  projects: [
    { key: 'QA', name: 'Quality' },
    { key: 'PLAT', name: 'Platform' },
  ],
  issue_types: ['Bug', 'Task'],
  default_project_key: 'QA',
  webhook_available: true,
}

const PREVIEW: JiraDefectPreview = {
  signature: 'f'.repeat(16),
  summary: '[TestLookup] checkout_flow_test',
  description:
    'Automated defect filed from TestLookup for: checkout_flow_test\n' +
    'Occurrence history:\n- Failing runs: 6\n' +
    'Suggested root cause (AI, confidence 82%): NPE in cart service',
  test_name: 'checkout_flow_test',
  suite_name: 'checkout',
  cluster_id: null,
  error_message: 'AssertionError',
  occurrences: { first_seen: '2026-07-01T00:00:00Z', last_seen: '2026-07-09T00:00:00Z', failing_runs: 6 },
  context: { branch: 'main', build_number: '1234', ci_run_url: null },
  ai_analysis: { root_cause: 'NPE in cart service', confidence: 82 },
  deep_link: 'http://localhost:3000/runs/abc',
  latest_run_id: 'abc',
  existing_defect: null,
}

async function mockHooks(
  metadata: JiraDefectMetadata | undefined = METADATA,
  preview: JiraDefectPreview | undefined = PREVIEW,
) {
  const { useJiraDefectMetadata, useJiraDefectPreview } = await import('@/hooks/useJiraDefects')
  ;(useJiraDefectMetadata as ReturnType<typeof vi.fn>).mockReturnValue({
    metadata, isLoading: false, isError: false,
  })
  ;(useJiraDefectPreview as ReturnType<typeof vi.fn>).mockReturnValue({
    preview, isLoading: false, isError: false,
  })
}

describe('CreateJiraIssueModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows the server-prefilled summary and description read-only, with pickers from metadata', async () => {
    await mockHooks()

    render(
      <CreateJiraIssueModal
        projectId="proj-1"
        fingerprint={'f'.repeat(16)}
        testName="checkout_flow_test"
        onClose={vi.fn()}
      />,
    )

    // Read-only preview — the user reviews before create, never silent.
    expect(await screen.findByText('[TestLookup] checkout_flow_test')).toBeInTheDocument()
    expect(screen.getByLabelText('Prefilled description').textContent).toContain(
      'Suggested root cause (AI, confidence 82%)',
    )
    // Pickers populated from the metadata endpoint.
    expect(screen.getByText('QA — Quality')).toBeInTheDocument()
    expect(screen.getByText('PLAT — Platform')).toBeInTheDocument()
    expect(screen.getByText('Task')).toBeInTheDocument()
    // Explicit create CTA — nothing is filed until the user clicks it.
    expect(screen.getByRole('button', { name: 'Create issue' })).toBeEnabled()
  })

  it('submits the reviewed payload to the create endpoint and reports the new key', async () => {
    await mockHooks()
    const { defectJiraService } = await import('@/services/defectJiraService')
    ;(defectJiraService.create as ReturnType<typeof vi.fn>).mockResolvedValue({
      target: 'jira',
      deduplicated: false,
      defect_id: 'd-1',
      jira_key: 'QA-42',
      jira_url: 'https://example.atlassian.net/browse/QA-42',
      external_status: 'Open',
      recurrence_count: 0,
      recurrence_comment_posted: false,
      subscriptions_notified: null,
      message: 'Created QA-42.',
    })
    const onClose = vi.fn()
    const onCreated = vi.fn()

    render(
      <CreateJiraIssueModal
        projectId="proj-1"
        fingerprint={'f'.repeat(16)}
        testName="checkout_flow_test"
        onClose={onClose}
        onCreated={onCreated}
      />,
    )

    fireEvent.change(await screen.findByLabelText(/Comment/), {
      target: { value: 'route to payments' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create issue' }))

    await waitFor(() => expect(defectJiraService.create).toHaveBeenCalledTimes(1))
    expect(defectJiraService.create).toHaveBeenCalledWith('proj-1', {
      fingerprint: 'f'.repeat(16),
      issue_type: 'Bug',
      jira_project_key: 'QA',
      extra_comment: 'route to payments',
      target: 'jira',
    })
    await waitFor(() => expect(onCreated).toHaveBeenCalled())
    expect(onClose).toHaveBeenCalled()
  })

  it('announces the dedup path up front and relabels the CTA to "Note recurrence"', async () => {
    await mockHooks(METADATA, {
      ...PREVIEW,
      existing_defect: {
        defect_id: 'd-9',
        jira_key: 'QA-17',
        jira_url: 'https://example.atlassian.net/browse/QA-17',
        external_status: 'In Progress',
      },
    })

    render(
      <CreateJiraIssueModal
        projectId="proj-1"
        fingerprint={'f'.repeat(16)}
        testName="checkout_flow_test"
        onClose={vi.fn()}
      />,
    )

    expect(await screen.findByText(/An open defect is already linked to/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'QA-17' })).toHaveAttribute(
      'href', 'https://example.atlassian.net/browse/QA-17',
    )
    expect(screen.getByText(/recurrence note to it instead of filing a duplicate/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Note recurrence' })).toBeEnabled()
  })

  it('disables the Jira target with reason copy and falls back to the webhook target when available', async () => {
    await mockHooks({
      ...METADATA,
      available: false,
      reason: 'offline_mode',
      projects: [],
      issue_types: [],
      webhook_available: true,
    })
    const { defectJiraService } = await import('@/services/defectJiraService')
    ;(defectJiraService.create as ReturnType<typeof vi.fn>).mockResolvedValue({
      target: 'webhook',
      deduplicated: false,
      defect_id: null,
      jira_key: null,
      jira_url: null,
      external_status: null,
      recurrence_count: 0,
      recurrence_comment_posted: false,
      subscriptions_notified: 2,
      message: 'defect.create_requested emitted to 2 subscription(s).',
    })

    render(
      <CreateJiraIssueModal
        projectId="proj-1"
        fingerprint={'f'.repeat(16)}
        testName="checkout_flow_test"
        onClose={vi.fn()}
      />,
    )

    // Jira radio disabled with the actionable tooltip; webhook auto-selected.
    const jiraRadio = await screen.findByRole('radio', { name: 'Jira' })
    expect(jiraRadio).toBeDisabled()
    expect(jiraRadio).toHaveAttribute('title', jiraUnavailableCopy('offline_mode'))
    expect(screen.getByRole('radio', { name: 'Webhook event' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getAllByText(/AI_OFFLINE_MODE is on/).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'Emit webhook event' }))
    await waitFor(() => expect(defectJiraService.create).toHaveBeenCalledTimes(1))
    expect(defectJiraService.create).toHaveBeenCalledWith('proj-1', {
      fingerprint: 'f'.repeat(16),
      issue_type: 'Bug',
      target: 'webhook',
    })
  })

  it('blocks submission entirely when both Jira and the webhook fallback are unavailable', async () => {
    await mockHooks({
      ...METADATA,
      available: false,
      reason: 'not_configured',
      projects: [],
      issue_types: [],
      webhook_available: false,
    })
    const { defectJiraService } = await import('@/services/defectJiraService')

    render(
      <CreateJiraIssueModal
        projectId="proj-1"
        fingerprint={'f'.repeat(16)}
        testName="checkout_flow_test"
        onClose={vi.fn()}
      />,
    )

    const submit = await screen.findByRole('button', { name: 'Create issue' })
    expect(submit).toBeDisabled()
    fireEvent.click(submit)
    expect(defectJiraService.create).not.toHaveBeenCalled()
  })
})
