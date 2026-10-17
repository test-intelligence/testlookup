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
  // US-15.1: the trust chrome renders on EVERY assistant answer, including
  // the plainest one with no trace and no actions.
  it('renders the AI-suggested badge even with no trace and no actions', () => {
    render(<AssistantMessageExtras toolTrace={[]} suggestedActions={[]} />)
    expect(screen.getByTestId('ai-suggested-badge')).toHaveTextContent('AI-suggested')
  })

  it('renders NO confidence — the copilot has none to report', () => {
    render(<AssistantMessageExtras toolTrace={TRACE} suggestedActions={[]} />)
    expect(screen.queryByTestId('ai-confidence')).not.toBeInTheDocument()
    expect(screen.queryByTestId('ai-basis-chip')).not.toBeInTheDocument()
    expect(screen.queryByText(/% *$/)).not.toBeInTheDocument()
  })

  it('renders no provenance line until the backend supplies one', () => {
    const { rerender } = render(
      <AssistantMessageExtras toolTrace={[]} suggestedActions={[]} />,
    )
    expect(screen.queryByTestId('ai-provenance')).not.toBeInTheDocument()
    rerender(
      <AssistantMessageExtras
        toolTrace={[]}
        suggestedActions={[]}
        provenanceRaw={{ mode_used: 'llm', llm_model: 'qwen2.5:7b' }}
      />,
    )
    expect(screen.getByTestId('ai-provenance')).toHaveTextContent('qwen2.5:7b')
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
    // W27 PAN-04: the form renders in place, inside the message (a region by the dialog's name).
    const dialog = screen.getByRole('region', { name: /propose quarantine/i })
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

  // W27 PAN-04: no backdrop stops the other action any more, so a typed reason asks before it is replaced.
  it('switching to the other action asks first when the open form has a typed reason; a clean one switches at once', () => {
    render(
      <AssistantMessageExtras toolTrace={[]} suggestedActions={[QUARANTINE_ACTION, JIRA_ACTION]} />,
    )
    const openQuarantine = () =>
      fireEvent.click(screen.getByRole('button', { name: /propose quarantine: test_checkout_flow/i }))
    const openJira = () => fireEvent.click(screen.getByRole('button', { name: /create jira issue: test_checkout_flow/i }))

    // Clean: at once.
    openQuarantine()
    openJira()
    expect(screen.queryByRole('region', { name: /propose quarantine/i })).toBeNull()
    expect(screen.getByRole('dialog', { name: /create jira issue \(stub\)/i })).toBeTruthy()

    // Typed: the question, inside the open form.
    openQuarantine()
    const reason = screen.getByPlaceholderText(/why should this test stop gating/i)
    fireEvent.change(reason, { target: { value: 'half typed' } })
    openJira()
    const form = screen.getByRole('region', { name: /propose quarantine/i })
    expect(form).toHaveTextContent('Discard your changes to "Propose quarantine"?')
    fireEvent.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByPlaceholderText(/why should this test stop gating/i)).toHaveValue('half typed')
    expect(screen.queryByRole('dialog', { name: /create jira issue \(stub\)/i })).toBeNull()

    openJira()
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }))
    expect(screen.queryByRole('region', { name: /propose quarantine/i })).toBeNull()
    expect(screen.getByRole('dialog', { name: /create jira issue \(stub\)/i })).toBeTruthy()
    expect(mockPropose).not.toHaveBeenCalled()
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
      plainSources: [], toolTrace: [], suggestedActions: [], provenanceRaw: null, meta: null,
    })
    expect(splitMessageSources([{ type: 'test_run', id: 'r1' }]).plainSources)
      .toEqual([{ type: 'test_run', id: 'r1' }])
  })

  it('unpacks how the answer was produced, and never shows it as a chip', () => {
    const meta = { type: 'meta', status: 'stopped', model: 'mistralai/mistral-nemo', first_token_ms: 1400 }
    const { plainSources, meta: unpacked } = splitMessageSources([
      { type: 'test_run', id: 'r1', build: '105' },
      meta as never,
    ])
    expect(plainSources).toEqual([{ type: 'test_run', id: 'r1', build: '105' }])
    expect(unpacked).toMatchObject({ status: 'stopped', model: 'mistralai/mistral-nemo', first_token_ms: 1400 })
  })
})
