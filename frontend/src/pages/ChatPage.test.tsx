import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatMessage } from '@/types/chat'
import type { PendingTurn } from '@/hooks/useChat'

const state = vi.hoisted(() => ({
  conversation: {} as Record<string, unknown>,
  mode: 'llm' as string,
  project: { id: 'p-1', name: 'Checkout Service' } as { id: string; name: string } | null,
  projects: [] as Array<{ id: string; name: string }>,
  setActiveProject: vi.fn(),
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
  useProjectStore: () => ({ activeProject: state.project, projects: state.projects, setActiveProject: state.setActiveProject }),
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
    state.projects = [{ id: 'p-1', name: 'Checkout Service' }, { id: 'p-2', name: 'E-Commerce Platform' }]
    state.setActiveProject = vi.fn()
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

  it("asks the run's own project when the link names it (homelab, 2026-10-10)", () => {
    // "Ask AI about this run" on an E-Commerce run, with Checkout Service
    // active, was answered in Checkout's scope: "I can't find build viz-3043".
    renderPage('/chat?prompt=What%20failed%20in%20build%20viz-3043%3F&project=p-2')
    expect(state.setActiveProject).toHaveBeenCalledWith({ id: 'p-2', name: 'E-Commerce Platform' })
    expect(screen.getByTestId('chat-input')).toHaveValue('What failed in build viz-3043?')
    expect(state.conversation.send).not.toHaveBeenCalled()
  })

  it('stays put for a project that is not one of the reader’s, or already active', () => {
    renderPage('/chat?prompt=Hi&project=p-unknown')
    renderPage('/chat?prompt=Hi&project=p-1')
    expect(state.setActiveProject).not.toHaveBeenCalled()
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

  /**
   * W27b PAN-05 (Y4): a conversation's delete asks right under that conversation
   * in the list, never with the browser's prompt. The trash button sends nothing;
   * the strip's Delete is the only sender, once; Cancel and Escape send nothing.
   */
  describe('a conversation\'s delete asks in the list', () => {
    const SESSIONS = [
      { id: 's-1', project_id: 'p-1', title: 'Why did Auth fail?', created_at: 't', updated_at: 't' },
      { id: 's-2', project_id: 'p-1', title: 'Flaky checkout tests', created_at: 't', updated_at: 't' },
    ]
    const strip = () => document.querySelector('[data-inline-confirm]') as HTMLElement | null
    const item = (title: string) =>
      screen.getAllByTestId('chat-session').find((el) => el.textContent?.includes(title)) as HTMLElement

    it('asks right under the conversation; the strip\'s Delete removes it, once', () => {
      const confirmSpy = vi.spyOn(window, 'confirm')
      const removeSession = vi.fn().mockResolvedValue(undefined)
      state.conversation = conversation({ sessions: SESSIONS, removeSession })
      renderPage()

      fireEvent.click(within(item('Why did Auth fail?')).getByRole('button', { name: 'Delete conversation' }))
      expect(removeSession).not.toHaveBeenCalled()
      const asked = strip() as HTMLElement
      expect(asked.querySelector('p')?.textContent).toBe('Delete this conversation?')
      // Right under that conversation, outside it (its click selects the conversation).
      expect(item('Why did Auth fail?').nextElementSibling).toBe(asked.closest('[data-in-page-row]'))
      expect(item('Why did Auth fail?').contains(asked)).toBe(false)
      expect(state.conversation.selectSession).not.toHaveBeenCalled()

      fireEvent.click(within(asked).getByRole('button', { name: 'Delete' }))
      expect(strip()).toBeNull()
      expect(removeSession).toHaveBeenCalledTimes(1)
      expect(removeSession).toHaveBeenCalledWith('s-1')
      expect(confirmSpy).not.toHaveBeenCalled()
      confirmSpy.mockRestore()
    })

    it('Cancel and Escape send nothing; one strip at a time', () => {
      const removeSession = vi.fn()
      state.conversation = conversation({ sessions: SESSIONS, removeSession })
      renderPage()

      fireEvent.click(within(item('Why did Auth fail?')).getByRole('button', { name: 'Delete conversation' }))
      fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }))
      expect(strip()).toBeNull()

      fireEvent.click(within(item('Why did Auth fail?')).getByRole('button', { name: 'Delete conversation' }))
      fireEvent.click(within(item('Flaky checkout tests')).getByRole('button', { name: 'Delete conversation' }))
      expect(document.querySelectorAll('[data-inline-confirm]')).toHaveLength(1)
      expect(item('Flaky checkout tests').nextElementSibling).toBe((strip() as HTMLElement).closest('[data-in-page-row]'))

      fireEvent.keyDown(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
      expect(strip()).toBeNull()
      expect(removeSession).not.toHaveBeenCalled()
    })
  })

  /**
   * W28 CHAT-01 (OR-05): during a conversation the starter questions stay available as chips right above the box;
   * a chip asks like typing it and leaves the reader's draft alone; "Hide suggestions" is remembered in this browser,
   * and a blocked storage never breaks the row. The welcome is unchanged (its cards are the suggestions there).
   */
  describe('suggested questions during a conversation', () => {
    const openConversation = (over: Record<string, unknown> = {}) =>
      conversation({ sessionId: 's-1', messages: [user('q-1', 'What failed?')], ...over })
    const row = () => screen.queryByRole('group', { name: 'Suggested questions' })

    beforeEach(() => window.localStorage.clear())
    afterEach(() => {
      vi.restoreAllMocks()
      window.localStorage.clear()
    })

    it('shows no chip on the welcome, whose five cards are unchanged', () => {
      renderPage()
      expect(screen.getByTestId('chat-welcome')).toBeInTheDocument()
      expect(screen.getAllByTestId('chat-starter')).toHaveLength(5)
      expect(screen.queryAllByTestId('chat-suggestion')).toHaveLength(0)
      expect(screen.queryByTestId('chat-suggestions-show')).toBeNull()
      expect(row()).toBeNull()
    })

    it('after a question the row sits right above the question box, in its column', () => {
      state.conversation = openConversation()
      renderPage()
      const chips = screen.getAllByTestId('chat-suggestion')
      expect(chips).toHaveLength(5)
      expect(chips[0]).toHaveAttribute('title', 'What failed in build 105, and why?')
      expect(screen.queryAllByTestId('chat-starter')).toHaveLength(0)
      const group = row() as HTMLElement
      const box = screen.getByTestId('chat-input')
      // Same max-w-3xl wrapper as the composer, before it.
      expect(group.parentElement).toBe(box.closest('.max-w-3xl'))
      expect(group.compareDocumentPosition(box) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
      expect(screen.getByTestId('chat-transcript').contains(group)).toBe(false)
    })

    it('also shows while the first answer of a new conversation is written, with the chips disabled', () => {
      state.conversation = conversation({ sessionId: null, busy: true, pending: pending({ status: 'Looking up build 105…' }) })
      renderPage()
      const chips = screen.getAllByTestId('chat-suggestion')
      expect(chips).toHaveLength(5)
      for (const chip of chips) expect(chip).toBeDisabled()
    })

    it("a chip asks its prompt through chat.send and keeps the reader's typed draft", async () => {
      state.conversation = openConversation()
      renderPage()
      fireEvent.change(screen.getByTestId('chat-input'), { target: { value: 'draft text' } })
      fireEvent.click(screen.getAllByTestId('chat-suggestion')[2])
      expect(state.conversation.send).toHaveBeenCalledTimes(1)
      expect(state.conversation.send).toHaveBeenCalledWith('Which tests are flaky right now, and are any of them quarantined?')
      // After the send has resolved (the welcome's ask() clears the box only then).
      await act(async () => { await Promise.resolve() })
      expect(screen.getByTestId('chat-input')).toHaveValue('draft text')
    })

    it('Hide suggestions is remembered across a remount; Show brings the row back and is remembered too', () => {
      state.conversation = openConversation()
      const first = renderPage()
      fireEvent.click(screen.getByTestId('chat-suggestions-hide'))
      expect(row()).toBeNull()
      expect(screen.getByTestId('chat-suggestions-show')).toBeInTheDocument()
      expect(window.localStorage.getItem('tl.chat.hideSuggestions')).toBe('1')
      first.unmount()

      const second = renderPage()
      expect(row()).toBeNull()
      fireEvent.click(screen.getByTestId('chat-suggestions-show'))
      expect(screen.getAllByTestId('chat-suggestion')).toHaveLength(5)
      expect(window.localStorage.getItem('tl.chat.hideSuggestions')).toBe('0')
      second.unmount()

      renderPage()
      expect(screen.getAllByTestId('chat-suggestion')).toHaveLength(5)
    })

    it('with localStorage throwing the row still renders, and Hide still hides it for the session', () => {
      vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
      vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
      state.conversation = openConversation()
      renderPage()
      expect(screen.getAllByTestId('chat-suggestion')).toHaveLength(5)
      fireEvent.click(screen.getByTestId('chat-suggestions-hide'))
      expect(row()).toBeNull()
      expect(screen.getByTestId('chat-suggestions-show')).toBeInTheDocument()
      fireEvent.click(screen.getByTestId('chat-suggestions-show'))
      expect(screen.getAllByTestId('chat-suggestion')).toHaveLength(5)
    })

    it('shows no row when chat is unavailable (Rules mode)', () => {
      state.mode = 'rules'
      state.conversation = openConversation()
      renderPage()
      expect(screen.queryAllByTestId('chat-suggestion')).toHaveLength(0)
      expect(screen.queryByTestId('chat-suggestions-show')).toBeNull()
    })
  })
})
