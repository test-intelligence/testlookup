/**
 * The Ask-AI question box: grows with its text, Enter sends (Shift+Enter is a
 * new line), and the Send button becomes Stop while an answer is written.
 */
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react'
import { Send, Square } from 'lucide-react'
import { MAX_CHAT_MESSAGE_LENGTH } from '@/hooks/useChat'

export interface ChatComposerHandle {
  focus: () => void
}

interface Props {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  onStop: () => void
  busy: boolean
  disabled?: boolean
  placeholder?: string
}

const ChatComposer = forwardRef<ChatComposerHandle, Props>(function ChatComposer(
  { value, onChange, onSend, onStop, busy, disabled = false, placeholder },
  ref,
) {
  const area = useRef<HTMLTextAreaElement>(null)
  useImperativeHandle(ref, () => ({ focus: () => area.current?.focus() }), [])

  useEffect(() => {
    const el = area.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`
  }, [value])

  const remaining = MAX_CHAT_MESSAGE_LENGTH - value.length
  const canSend = !busy && !disabled && value.trim().length > 0

  return (
    <div>
      <div className="flex gap-2 items-end">
        <textarea
          ref={area}
          value={value}
          onChange={(e) => onChange(e.target.value.slice(0, MAX_CHAT_MESSAGE_LENGTH))}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              if (canSend) onSend()
            }
          }}
          placeholder={placeholder ?? 'Ask about failures, flaky tests, builds or release readiness…'}
          rows={1}
          maxLength={MAX_CHAT_MESSAGE_LENGTH}
          disabled={disabled}
          aria-label="Ask a question"
          className="input flex-1 resize-none text-sm py-2 leading-relaxed"
          style={{ maxHeight: 180, overflowY: 'auto' }}
          data-testid="chat-input"
        />
        {busy ? (
          <button
            type="button"
            onClick={onStop}
            className="btn-secondary p-2.5 shrink-0 inline-flex items-center gap-1.5 text-sm"
            title="Stop the answer"
            data-testid="chat-stop"
          >
            <Square className="w-4 h-4" /> Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={onSend}
            disabled={!canSend}
            className="btn-primary p-2.5 shrink-0 disabled:opacity-40"
            title="Send (Enter)"
            aria-label="Send"
            data-testid="chat-send"
          >
            <Send className="w-4 h-4" />
          </button>
        )}
      </div>
      <div className="flex justify-between mt-1 text-xs text-[var(--color-text-faint)]">
        <span>Enter to send · Shift+Enter for a new line</span>
        {remaining < 500 && <span className={remaining < 100 ? 'text-[var(--status-failed)]' : undefined}>{remaining} characters left</span>}
      </div>
    </div>
  )
})

export default ChatComposer
