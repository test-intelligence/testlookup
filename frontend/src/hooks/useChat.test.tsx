import { createElement, type ReactNode } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatMessage, ChatStreamEvent } from '@/types/chat'

const svc = vi.hoisted(() => ({
  listSessions: vi.fn(),
  getRunSummaries: vi.fn(),
  createSession: vi.fn(),
  deleteSession: vi.fn(),
  getMessages: vi.fn(),
}))
const stream = vi.hoisted(() => ({
  streamChatTurn: vi.fn(),
}))

vi.mock('@/services/chatService', () => ({ default: svc }))
vi.mock('@/services/chatStream', async () => {
  const actual = await vi.importActual<typeof import('@/services/chatStream')>('@/services/chatStream')
  return { ...actual, streamChatTurn: stream.streamChatTurn }
})

import { ChatStreamError } from '@/services/chatStream'
import { MAX_CHAT_MESSAGE_LENGTH, pendingQuestionVisible, useChatConversation } from './useChat'

function wrapper({ children }: { children: ReactNode }) {
  return createElement(SWRConfig, { value: { provider: () => new Map(), dedupingInterval: 0 } }, children)
}

type Emit = (e: ChatStreamEvent) => void

/** A stream the test drives: emit events, then finish (or fail). */
function controllableStream() {
  let emit: Emit = () => {}
  let finish: () => void = () => {}
  let fail: (e: unknown) => void = () => {}
  let signal: AbortSignal | null = null
  stream.streamChatTurn.mockImplementationOnce(
    (_sid: string, _body: unknown, opts: { signal: AbortSignal; onEvent: Emit }) => {
      emit = opts.onEvent
      signal = opts.signal
      return new Promise<void>((resolve, reject) => {
        finish = resolve
        fail = reject
        opts.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
      })
    },
  )
  return {
    emit: (e: ChatStreamEvent) => act(() => emit(e)),
    finish: () => act(async () => finish()),
    fail: (e: unknown) => act(async () => fail(e)),
    aborted: () => signal?.aborted ?? false,
  }
}

const answer = (id: string, content: string): ChatMessage => ({
  id, session_id: 's-1', role: 'assistant', content, sources: [{ type: 'meta', status: 'complete' } as never],
  created_at: '2026-10-09T10:00:01Z',
})

describe('useChatConversation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    svc.listSessions.mockResolvedValue([])
    svc.getMessages.mockResolvedValue([])
    svc.createSession.mockResolvedValue({ id: 's-1', project_id: 'p-1', title: null })
  })

  it('shows the question at once, then streams the answer and keeps the saved one', async () => {
    const turn = controllableStream()
    const { result } = renderHook(() => useChatConversation('p-1'), { wrapper })

    let sent: Promise<boolean> = Promise.resolve(false)
    act(() => { sent = result.current.send('What failed in build 105?') })
    // Before the session exists or the server answers.
    expect(result.current.pending).toMatchObject({ question: 'What failed in build 105?', phase: 'sending' })
    expect(result.current.busy).toBe(true)

    await waitFor(() => expect(stream.streamChatTurn).toHaveBeenCalled())
    expect(svc.createSession).toHaveBeenCalledWith({ project_id: 'p-1' })
    expect(stream.streamChatTurn.mock.calls[0][1]).toEqual({ message: 'What failed in build 105?' })

    turn.emit({ event: 'start', data: { user_message_id: 'q-1', retry: false } })
    turn.emit({ event: 'status', data: { label: 'Reading the failures in build 105…', tool: 'list_run_failures' } })
    expect(result.current.pending?.status).toBe('Reading the failures in build 105…')
    turn.emit({ event: 'delta', data: { text: 'Build 105 ' } })
    turn.emit({ event: 'delta', data: { text: 'failed.' } })
    expect(result.current.pending).toMatchObject({ answer: 'Build 105 failed.', phase: 'streaming' })

    svc.getMessages.mockResolvedValue([
      { id: 'q-1', session_id: 's-1', role: 'user', content: 'What failed in build 105?', sources: null, created_at: 't' },
      answer('a-1', 'Build 105 failed.'),
    ])
    turn.emit({
      event: 'done',
      data: { message: answer('a-1', 'Build 105 failed.'), sources: [], tool_trace: [], suggested_actions: [], meta: { status: 'complete' } },
    })
    await turn.finish()
    await act(async () => { await sent })

    expect(result.current.pending).toBeNull()
    expect(result.current.busy).toBe(false)
    await waitFor(() => expect(result.current.messages.map((m) => m.id)).toEqual(['q-1', 'a-1']))
  })

  it('ignores a second send while an answer is being written', async () => {
    controllableStream()
    const { result } = renderHook(() => useChatConversation('p-1'), { wrapper })
    act(() => { void result.current.send('first question here') })
    let second = true
    await act(async () => { second = await result.current.send('second question here') })
    expect(second).toBe(false)
    await waitFor(() => expect(stream.streamChatTurn).toHaveBeenCalledTimes(1))
    expect(svc.createSession).toHaveBeenCalledTimes(1)
  })

  it('refuses a question over the server limit', async () => {
    const { result } = renderHook(() => useChatConversation('p-1'), { wrapper })
    let sent = true
    await act(async () => { sent = await result.current.send('x'.repeat(MAX_CHAT_MESSAGE_LENGTH + 1)) })
    expect(sent).toBe(false)
    expect(svc.createSession).not.toHaveBeenCalled()
  })

  it('stop aborts the stream and keeps the text shown', async () => {
    const turn = controllableStream()
    const { result } = renderHook(() => useChatConversation('p-1'), { wrapper })
    act(() => { void result.current.send('What failed?') })
    await waitFor(() => expect(stream.streamChatTurn).toHaveBeenCalled())
    turn.emit({ event: 'delta', data: { text: 'Partial' } })
    await act(async () => { result.current.stop() })

    expect(turn.aborted()).toBe(true)
    await waitFor(() => expect(result.current.pending?.phase).toBe('stopped'))
    expect(result.current.pending?.answer).toBe('Partial')
    expect(result.current.busy).toBe(false)
  })

  it('a failed turn says why, and retry answers the same question without sending it again', async () => {
    const turn = controllableStream()
    const { result } = renderHook(() => useChatConversation('p-1'), { wrapper })
    act(() => { void result.current.send('Is build 105 ready to release?') })
    await waitFor(() => expect(stream.streamChatTurn).toHaveBeenCalled())
    turn.emit({ event: 'start', data: { user_message_id: 'q-1', retry: false } })
    turn.emit({ event: 'error', data: { code: 'timeout', message: 'The AI provider took too long.', retryable: true } })
    await turn.finish()

    expect(result.current.pending).toMatchObject({ phase: 'error', error: { code: 'timeout' } })
    // The question is saved: the history is refetched.
    await waitFor(() => expect(svc.getMessages).toHaveBeenCalled())

    const again = controllableStream()
    act(() => { void result.current.retry() })
    await waitFor(() => expect(stream.streamChatTurn).toHaveBeenCalledTimes(2))
    expect(stream.streamChatTurn.mock.calls[1][1]).toEqual({ retry: true })
    expect(result.current.pending).toMatchObject({ question: 'Is build 105 ready to release?', retry: true, phase: 'sending' })
    await again.finish()
  })

  it('a refused turn uses the server message', async () => {
    const turn = controllableStream()
    const { result } = renderHook(() => useChatConversation('p-1'), { wrapper })
    act(() => { void result.current.send('What failed?') })
    await waitFor(() => expect(stream.streamChatTurn).toHaveBeenCalled())
    await turn.fail(new ChatStreamError({ code: 'turn_in_progress', message: 'An answer is already being written.', retryable: true }, 200))
    expect(result.current.pending?.error?.code).toBe('turn_in_progress')
  })

  it('switching project stops the turn and starts empty', async () => {
    const turn = controllableStream()
    const { result, rerender } = renderHook(({ p }) => useChatConversation(p), { wrapper, initialProps: { p: 'p-1' } })
    act(() => { void result.current.send('What failed?') })
    await waitFor(() => expect(stream.streamChatTurn).toHaveBeenCalled())
    rerender({ p: 'p-2' })
    expect(turn.aborted()).toBe(true)
    expect(result.current.pending).toBeNull()
    expect(result.current.sessionId).toBeNull()
  })

  it('lists the active project’s conversations only', async () => {
    renderHook(() => useChatConversation('p-7'), { wrapper })
    await waitFor(() => expect(svc.listSessions).toHaveBeenCalledWith('p-7'))
  })
})

describe('pendingQuestionVisible', () => {
  const pending = { question: 'q', userMessageId: 'q-1', retry: false, answer: '', status: null, phase: 'working' as const, error: null }
  it('shows the question until the saved history has it', () => {
    expect(pendingQuestionVisible(pending, [])).toBe(true)
    expect(pendingQuestionVisible(pending, [{ id: 'q-1' } as ChatMessage])).toBe(false)
    expect(pendingQuestionVisible({ ...pending, retry: true }, [])).toBe(false)
  })
})
