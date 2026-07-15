/**
 * AI-6 copilot chat extras — hermetic contract tests.
 *
 * Pins:
 *  - the "How I looked this up" trace renders collapsed and expands to the
 *    per-tool summaries;
 *  - a propose_quarantine action opens the quarantine dialog pre-filled and
 *    submits through the existing audited flow (flakyQuarantineService)
 *    tagged with the ask-ai-copilot source;
 *  - a create_jira action opens the US-6.1 CreateJiraIssueModal with the
 *    prefilled project/fingerprint/test name;
 *  - splitMessageSources unpacks the carrier entries persisted inside the
 *    ChatMessage sources JSON and strips them from the plain chips.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AssistantMessageExtras from './AssistantMessageExtras'
import { splitMessageSources } from '@/types/chat'
import type { SuggestedAction, ToolTraceEntry } from '@/types/chat'

const mockPropose = vi.fn()
vi.mock('@/services/flakyQuarantineService', () => ({
  flakyQuarantineService: {
    propose: (...args: unknown[]) => mockPropose(...args),
  },
}))

// The Jira modal drags in SWR hooks + services — stub it and capture props.
const jiraModalProps = vi.fn()
vi.mock('@/components/defects/CreateJiraIssueModal', () => ({
  default: (props: Record<string, unknown>) => {
    jiraModalProps(props)
    return <div role="dialog" aria-label="Create Jira issue (stub)" />
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { error: vi.fn(), success: vi.fn() },
}))

const TRACE: ToolTraceEntry[] = [
  { tool: 'check_quarantine_status', summary: 'checked flip history — 3 prior corrections found' },
  { tool: 'list_run_failures', summary: 'listed 2 failing tests in build 512' },
]

const QUARANTINE_ACTION: SuggestedAction = {
  type: 'propose_quarantine',
  label: 'Propose quarantine: test_checkout_flow',
  prefill: {
    project_id: 'proj-1',
    test_fingerprint: 'fp-checkout',
    test_name: 'test_checkout_flow',
    suite_name: 'payments',
  },
}

const JIRA_ACTION: SuggestedAction = {
  type: 'create_jira',
  label: 'Create Jira issue: test_checkout_flow',
  prefill: {
    project_id: 'proj-1',
    fingerprint: 'fp-checkout',
    test_name: 'test_checkout_flow',
  },
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('AssistantMessageExtras', () => {
  it('renders nothing when there is no trace and no actions', () => {
    const { container } = render(
      <AssistantMessageExtras toolTrace={[]} suggestedActions={[]} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders the trace collapsed, then expands to per-tool summaries', () => {
    render(<AssistantMessageExtras toolTrace={TRACE} suggestedActions={[]} />)

    const toggle = screen.getByRole('button', { name: /how i looked this up/i })
    expect(toggle.textContent).toContain('2 checks')
    // Collapsed: summaries not visible yet
    expect(screen.queryByText(/3 prior corrections found/)).toBeNull()

    fireEvent.click(toggle)
    expect(screen.getByText(/3 prior corrections found/)).toBeTruthy()
    expect(screen.getByText('check_quarantine_status')).toBeTruthy()
    expect(screen.getByText(/listed 2 failing tests in build 512/)).toBeTruthy()
  })

  it('opens the quarantine dialog pre-filled and submits via the audited flow', async () => {
    mockPropose.mockResolvedValue({ status: 'PROPOSED' })
    render(
      <AssistantMessageExtras toolTrace={TRACE} suggestedActions={[QUARANTINE_ACTION]} />,
    )

    fireEvent.click(
      screen.getByRole('button', { name: /propose quarantine: test_checkout_flow/i }),
    )
    const dialog = screen.getByRole('dialog', { name: /propose quarantine/i })
    expect(dialog.textContent).toContain('test_checkout_flow')

    // Human supplies the reason — the agent never submits on its own
    fireEvent.change(screen.getByPlaceholderText(/why should this test stop gating/i), {
      target: { value: 'Flake confirmed by copilot investigation' },
    })
    fireEvent.click(screen.getByRole('button', { name: /^propose quarantine$/i }))

    await waitFor(() => expect(mockPropose).toHaveBeenCalledTimes(1))
    expect(mockPropose).toHaveBeenCalledWith({
      project_id: 'proj-1',
      test_fingerprint: 'fp-checkout',
      test_name: 'test_checkout_flow',
      suite_name: 'payments',
      detection_method: 'manual',
      rationale: {
        reason: 'Flake confirmed by copilot investigation',
        source: 'ask-ai-copilot',
      },
    })
  })

  it('opens the US-6.1 Jira modal with the prefilled identity', () => {
    render(
      <AssistantMessageExtras toolTrace={[]} suggestedActions={[JIRA_ACTION]} />,
    )
    fireEvent.click(
      screen.getByRole('button', { name: /create jira issue: test_checkout_flow/i }),
    )
    expect(screen.getByRole('dialog', { name: /create jira issue \(stub\)/i })).toBeTruthy()
    expect(jiraModalProps).toHaveBeenCalledWith(
      expect.objectContaining({
        projectId: 'proj-1',
        fingerprint: 'fp-checkout',
        testName: 'test_checkout_flow',
      }),
    )
  })
})

describe('splitMessageSources', () => {
  it('unpacks carrier entries and strips them from the plain chips', () => {
    const { plainSources, toolTrace, suggestedActions } = splitMessageSources([
      { type: 'tool', id: 'list_run_failures' },
      { type: 'tool_trace', trace: TRACE } as never,
      { type: 'suggested_actions', actions: [QUARANTINE_ACTION] } as never,
    ])
    expect(plainSources).toEqual([{ type: 'tool', id: 'list_run_failures' }])
    expect(toolTrace).toEqual(TRACE)
    expect(suggestedActions).toEqual([QUARANTINE_ACTION])
  })

  it('handles legacy messages with null or plain sources', () => {
    expect(splitMessageSources(null)).toEqual({
      plainSources: [], toolTrace: [], suggestedActions: [],
    })
    expect(splitMessageSources([{ type: 'test_run', id: 'r1' }]).plainSources)
      .toEqual([{ type: 'test_run', id: 'r1' }])
  })
})
