import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatMessage } from '@/types/chat'
import type { PendingTurn } from '@/hooks/useChat'

const state = vi.hoisted(() => ({
  conversation: {} as Record<string, unknown>,
  mode: 'llm' as string,
  project: { id: 'p-1', name: 'Checkout Service' } as { id: string; name: string } | null,
}))

vi.mock('@/hooks/useChat', async () => {
  const actual = await vi.importActual<typeof import('@/hooks/useChat')>('@/hooks/useChat')
  return {
    ...actual,
    useChatConversation: () => state.conversation,
    useRunSummaries: () => ({
      data: [{
        test_run_id: 'run-105', project_id: 'p-1', build_number: '105', executive_summary: 'Build 105 completed — 2 tests failed.',
        markdown_report: null, anomaly_count: 0, is_regression: false, analysis_count: 0, generated_at: new Date().toISOString(),
      }],
      isLoading: false,
    }),
  }
})
vi.mock('@/hooks/useAIConfig', () => ({
  useAIConfig: () => ({ data: { analysis_mode: state.mode } }),
  isLLMAvailable: (c?: { analysis_mode: string }) => !c || c.analysis_mode === 'llm' || c.analysis_mode === 'auto',
}))
vi.mock('@/store/projectStore', () => ({
  useProjectStore: () => ({ activeProject: state.project }),
}))
vi.mock('@/components/chat/AssistantMessageExtras', () => ({ default: () => null }))

import ChatPage from './ChatPage'

const user = (id: string, content: string): ChatMessage => ({
  id, session_id: 's-1', role: 'user', content, sources: null, created_at: 't',
})

function conversation(overrides: Record<string, unknown> = {}) {
  return {
    sessionId: null,
    selectSession: vi.fn(),
    sessions: [],
    sessionsLoading: false,
    messages: [],
    messagesLoading: false,
    messagesError: null,
    reloadMessages: vi.fn(),
    pending: null,
    busy: false,
    send: vi.fn().mockResolvedValue(true),
    stop: vi.fn(),
    retry: vi.fn().mockResolvedValue(undefined),
    removeSession: vi.fn(),
    ...overrides,
  }
}

function renderPage(path = '/chat') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const pending = (over: Partial<PendingTurn>): PendingTurn => ({
  question: 'What failed?', userMessageId: null, retry: false, answer: '', status: null, phase: 'working', error: null, ...over,
})

describe('ChatPage', () => {
  beforeEach(() => {
    state.mode = 'llm'
    state.project = { id: 'p-1', name: 'Checkout Service' }
    state.conversation = conversation()
  })

  it('says what it is for and offers questions about this project', () => {
    renderPage()
    expect(screen.getByText('Ask about Checkout Service')).toBeInTheDocument()
    const starters = screen.getAllByTestId('chat-starter').map((b) => b.textContent)
    expect(starters.some((t) => t?.includes('What failed in build 105, and why?'))).toBe(true)
    expect(starters.some((t) => t?.includes('Is Checkout Service ready to release?'))).toBe(true)

    fireEvent.click(screen.getAllByTestId('chat-starter')[0])
    expect(state.conversation.send).toHaveBeenCalledWith('What failed in build 105, and why?')
  })

  it('renders an answer table as a table and links the builds and tests it names', () => {
    state.conversation = conversation({
      sessionId: 's-1',
      messages: [
        user('q-1', 'What failed?'),
        {
          id: 'a-1', session_id: 's-1', role: 'assistant', created_at: 't',
          content: '| Test | Status |\n|---|---|\n| test_refund_flow | FAILED |',
          sources: [
            { type: 'test_run', id: 'run-105', build: '105' },
            { type: 'test_case', id: 'case-1', run_id: 'run-105', name: 'test_refund_flow' },
            { type: 'meta', status: 'complete', model: 'mistralai/mistral-nemo', first_token_ms: 1400, total_ms: 4300, tool_calls: 1 } as never,
          ],
        },
      ],
    })
    renderPage()
    const answer = screen.getByTestId('chat-assistant-message')
    expect(within(answer).getByRole('table')).toBeInTheDocument()
    expect(within(answer).queryByText(/\|---\|/)).toBeNull()
    expect(within(answer).getByRole('link', { name: /Build 105/ })).toHaveAttribute('href', '/runs/run-105')
    expect(within(answer).getByRole('link', { name: /test_refund_flow/ })).toHaveAttribute('href', '/runs/run-105/tests/case-1')
    expect(screen.getByTestId('chat-answer-meta')).toHaveTextContent('mistral-nemo · first words in 1.4 s · answered in 4.3 s · 1 lookup')
  })

  it('shows what the assistant is doing, and Stop stops it', () => {
    state.conversation = conversation({
      sessionId: 's-1',
      busy: true,
      pending: pending({ status: 'Checking the history of test_refund_flow…' }),
    })
    renderPage()
    expect(screen.getByTestId('chat-user-message')).toHaveTextContent('What failed?')
    expect(screen.getByTestId('chat-status')).toHaveTextContent('Checking the history of test_refund_flow…')
    fireEvent.click(screen.getByTestId('chat-stop'))
    expect(state.conversation.stop).toHaveBeenCalled()
    expect(screen.queryByTestId('chat-send')).toBeNull()
  })

  it('streams the answer text as it arrives', () => {
    state.conversation = conversation({
      sessionId: 's-1', busy: true, pending: pending({ phase: 'streaming', answer: 'Build 105 **failed**' }),
    })
    renderPage()
    expect(screen.getByTestId('chat-pending-answer')).toHaveTextContent('Build 105 failed')
  })

  it('says why an answer failed and offers Retry', () => {
    state.conversation = conversation({
      sessionId: 's-1',
      pending: pending({ phase: 'error', error: { code: 'timeout', message: 'The AI provider took too long to answer.', retryable: true } }),
    })
    renderPage()
    expect(screen.getByTestId('chat-error')).toHaveTextContent('The AI provider took too long to answer.')
    fireEvent.click(within(screen.getByTestId('chat-error')).getByRole('button', { name: /Retry/ }))
    expect(state.conversation.retry).toHaveBeenCalled()
  })

  it('offers to answer a question left without an answer', () => {
    state.conversation = conversation({ sessionId: 's-1', messages: [user('q-1', 'What failed?')] })
    renderPage()
    fireEvent.click(within(screen.getByTestId('chat-unanswered')).getByRole('button'))
    expect(state.conversation.retry).toHaveBeenCalled()
  })

  it('sends with Enter and keeps the text when the send is refused', async () => {
    const send = vi.fn().mockResolvedValue(false)
    state.conversation = conversation({ send })
    renderPage()
    const input = screen.getByTestId('chat-input')
    fireEvent.change(input, { target: { value: 'Which tests are flaky?' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(send).toHaveBeenCalledWith('Which tests are flaky?')
    await screen.findByDisplayValue('Which tests are flaky?')
  })

  it('fills the question from ?prompt= without sending it', () => {
    renderPage('/chat?prompt=What%20failed%20in%20build%20105%3F')
    expect(screen.getByTestId('chat-input')).toHaveValue('What failed in build 105?')
    expect(state.conversation.send).not.toHaveBeenCalled()
  })

  it('explains itself in Rules mode instead of offering a box', () => {
    state.mode = 'rules'
    renderPage()
    expect(screen.getByText(/Chat is unavailable in Rules mode/)).toBeInTheDocument()
    expect(screen.queryByTestId('chat-input')).toBeNull()
  })

  it('says so when a conversation cannot be loaded', () => {
    state.conversation = conversation({ sessionId: 's-1', messagesError: new Error('boom') })
    renderPage()
    expect(screen.getByTestId('chat-messages-unavailable')).toBeInTheDocument()
  })
})
