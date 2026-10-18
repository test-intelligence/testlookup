/**
 * W28 CHAT-01: the chips above the question box during a conversation.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import ChatSuggestions from './ChatSuggestions'
import { starterPrompts } from './chatContent'

const PROMPTS = starterPrompts('Checkout Service', '105')

function renderRow(over: Partial<React.ComponentProps<typeof ChatSuggestions>> = {}) {
  const props = {
    prompts: PROMPTS,
    onAsk: vi.fn(),
    disabled: false,
    hidden: false,
    onHide: vi.fn(),
    onShow: vi.fn(),
    ...over,
  }
  render(<ChatSuggestions {...props} />)
  return props
}

describe('ChatSuggestions', () => {
  it('shows one chip per prompt with its label, the full question as its title, then Hide suggestions', () => {
    renderRow()
    const row = screen.getByRole('group', { name: 'Suggested questions' })
    expect(row).toHaveAttribute('data-chat-suggestions')
    expect(row.className).toContain('flex-wrap')
    const chips = within(row).getAllByTestId('chat-suggestion')
    expect(chips).toHaveLength(5)
    expect(chips.map((c) => c.textContent)).toEqual(['What failed', 'What changed', 'Flaky tests', 'Release', 'Standup summary'])
    expect(chips.map((c) => c.getAttribute('title'))).toEqual(PROMPTS.map((p) => p.prompt))
    expect(chips[0]).toHaveAttribute('title', 'What failed in build 105, and why?')
    expect(chips[3]).toHaveAttribute('title', 'Is Checkout Service ready to release? What blocks it?')
    // The prompt's icon, at the chip's size.
    const icon = chips[0].querySelector('svg')
    expect(icon?.getAttribute('class')).toContain('w-3.5')
    expect(icon?.getAttribute('class')).not.toContain('w-4')
    // The welcome's own cards keep their id; the chips never use it.
    expect(screen.queryByTestId('chat-starter')).toBeNull()
    const buttons = within(row).getAllByRole('button')
    expect(buttons[buttons.length - 1]).toBe(screen.getByTestId('chat-suggestions-hide'))
    expect(screen.getByTestId('chat-suggestions-hide')).toHaveTextContent('Hide suggestions')
    expect(screen.queryByTestId('chat-suggestions-show')).toBeNull()
  })

  it('a chip asks its question (the prompt, not the label)', () => {
    const props = renderRow()
    fireEvent.click(screen.getAllByTestId('chat-suggestion')[2])
    expect(props.onAsk).toHaveBeenCalledTimes(1)
    expect(props.onAsk).toHaveBeenCalledWith('Which tests are flaky right now, and are any of them quarantined?')
  })

  it('every chip is disabled while an answer is being written; Hide still works', () => {
    const props = renderRow({ disabled: true })
    for (const chip of screen.getAllByTestId('chat-suggestion')) expect(chip).toBeDisabled()
    fireEvent.click(screen.getAllByTestId('chat-suggestion')[0])
    expect(props.onAsk).not.toHaveBeenCalled()
    expect(screen.getByTestId('chat-suggestions-hide')).toBeEnabled()
    fireEvent.click(screen.getByTestId('chat-suggestions-hide'))
    expect(props.onHide).toHaveBeenCalledTimes(1)
  })

  it('Hide suggestions calls onHide and nothing else', () => {
    const props = renderRow()
    fireEvent.click(screen.getByTestId('chat-suggestions-hide'))
    expect(props.onHide).toHaveBeenCalledTimes(1)
    expect(props.onShow).not.toHaveBeenCalled()
    expect(props.onAsk).not.toHaveBeenCalled()
  })

  it('hidden shows only Show suggestions, which calls onShow', () => {
    const props = renderRow({ hidden: true })
    expect(screen.queryByRole('group', { name: 'Suggested questions' })).toBeNull()
    expect(screen.queryAllByTestId('chat-suggestion')).toHaveLength(0)
    expect(screen.queryByTestId('chat-suggestions-hide')).toBeNull()
    expect(screen.getAllByRole('button')).toHaveLength(1)
    fireEvent.click(screen.getByTestId('chat-suggestions-show'))
    expect(screen.getByTestId('chat-suggestions-show')).toHaveTextContent('Show suggestions')
    expect(props.onShow).toHaveBeenCalledTimes(1)
    expect(props.onHide).not.toHaveBeenCalled()
  })

  it('renders a prompt as text, never as markup', () => {
    renderRow({ prompts: [{ icon: null, label: '<b>Bold</b>', prompt: '<img src=x onerror=alert(1)>' }] })
    const chip = screen.getByTestId('chat-suggestion')
    expect(chip).toHaveTextContent('<b>Bold</b>')
    expect(chip.querySelector('b, img')).toBeNull()
    expect(chip).toHaveAttribute('title', '<img src=x onerror=alert(1)>')
  })
})
