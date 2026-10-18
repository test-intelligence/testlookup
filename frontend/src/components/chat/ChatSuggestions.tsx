/**
 * W28 CHAT-01 (OR-05): the suggested questions stay available during a
 * conversation, as a row of chips right above the question box.
 *
 * The welcome screen's cards (`chat-starter`) are unchanged; these chips carry
 * their own test id (`chat-suggestion`) and show the LABEL, with the full
 * question as the title. A chip asks its question like typing it, without
 * touching what the reader has typed in the box (the host sends it), and is
 * disabled while an answer is being written. "Hide suggestions" collapses the
 * row to one small "Show suggestions" button in the same place; the host
 * remembers the choice (`chatSuggestionsPreference`).
 */
import { cloneElement, isValidElement, type ReactElement } from 'react'
import type { StarterPrompt } from '@/components/chat/chatContent'

interface ChatSuggestionsProps {
  prompts: StarterPrompt[]
  onAsk: (prompt: string) => void
  disabled: boolean
  hidden: boolean
  onHide: () => void
  onShow: () => void
}

const TEXT_BUTTON =
  'text-xs px-1 py-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:underline'

/** The prompt's icon at the chip's size (the welcome draws it at w-4). */
function chipIcon(icon: React.ReactNode): React.ReactNode {
  if (!isValidElement(icon)) return icon
  return cloneElement(icon as ReactElement<{ className?: string; 'aria-hidden'?: boolean }>, {
    className: 'w-3.5 h-3.5 shrink-0',
    'aria-hidden': true,
  })
}

export default function ChatSuggestions({ prompts, onAsk, disabled, hidden, onHide, onShow }: ChatSuggestionsProps) {
  if (hidden) {
    return (
      <div className="flex flex-wrap items-center gap-1.5 mb-2" data-chat-suggestions-hidden="">
        <button type="button" onClick={onShow} className={TEXT_BUTTON} data-testid="chat-suggestions-show">
          Show suggestions
        </button>
      </div>
    )
  }
  return (
    <div role="group" aria-label="Suggested questions" data-chat-suggestions="" className="flex flex-wrap items-center gap-1.5 mb-2">
      {prompts.map((p) => (
        <button
          key={p.label}
          type="button"
          onClick={() => onAsk(p.prompt)}
          disabled={disabled}
          title={p.prompt}
          className="inline-flex items-center gap-1 text-xs px-2 py-1 rounded-full border border-[var(--color-border)] bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-hover)] hover:text-[var(--color-text)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          data-testid="chat-suggestion"
        >
          {chipIcon(p.icon)}
          <span>{p.label}</span>
        </button>
      ))}
      <button type="button" onClick={onHide} className={TEXT_BUTTON} data-testid="chat-suggestions-hide">
        Hide suggestions
      </button>
    </div>
  )
}
