/** W21d ACT-02: the Live toggle (a pressed button, no dialog) and the status line under the filter row. */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import LiveToggle, { LiveStatusLine } from './LiveToggle'
import { STREAM_ERROR_SENTENCES } from '@/types/activityLive'

const dotOf = (button: HTMLElement) => button.querySelector('span[aria-hidden="true"]') as HTMLElement

describe('LiveToggle', () => {
  it('is an unpressed button with a muted dot while off, and asks to turn on', () => {
    const onToggle = vi.fn()
    render(<LiveToggle on={false} status="off" onToggle={onToggle} />)
    const button = screen.getByRole('button', { name: 'Live' })
    expect(button).toHaveAttribute('aria-pressed', 'false')
    expect(button).toHaveAttribute('data-activity-live')
    expect(dotOf(button).style.background).toBe('var(--color-text-muted)')
    fireEvent.click(button)
    expect(onToggle).toHaveBeenCalledWith(true)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('is pressed, with the accent dot ONLY while the stream is live', () => {
    const onToggle = vi.fn()
    const { rerender } = render(<LiveToggle on status="connecting" onToggle={onToggle} />)
    const button = screen.getByRole('button', { name: 'Live' })
    expect(button).toHaveAttribute('aria-pressed', 'true')
    expect(dotOf(button).style.background).toBe('var(--color-text-muted)')

    rerender(<LiveToggle on status="live" onToggle={onToggle} />)
    expect(dotOf(button).style.background).toBe('var(--color-accent)')
    expect(button).toHaveAttribute('data-live-status', 'live')
    fireEvent.click(button)
    expect(onToggle).toHaveBeenCalledWith(false)
  })
})

describe('LiveStatusLine', () => {
  it.each([
    ['off' as const, null],
    ['live' as const, null],
  ])('says nothing while %s', (status, message) => {
    const { container } = render(<LiveStatusLine status={status} message={message} />)
    expect(container).toBeEmptyDOMElement()
  })

  it.each([
    ['connecting' as const, null, 'Connecting…'],
    ['reconnecting' as const, null, 'Reconnecting…'],
    ['unavailable' as const, null, STREAM_ERROR_SENTENCES.live_unavailable],
    ['unavailable' as const, STREAM_ERROR_SENTENCES.server_busy, STREAM_ERROR_SENTENCES.server_busy],
    ['refused' as const, STREAM_ERROR_SENTENCES.too_many_streams, STREAM_ERROR_SENTENCES.too_many_streams],
    ['refused' as const, null, 'Live updates were refused.'],
  ])('says the %s state out loud', (status, message, sentence) => {
    render(<LiveStatusLine status={status} message={message} />)
    const line = screen.getByRole('status')
    expect(line).toHaveTextContent(sentence)
    expect(line).toHaveAttribute('data-activity-live-status', status)
  })

  it('renders a server sentence as text, never as markup', () => {
    const { container } = render(<LiveStatusLine status="refused" message={'<img src=x onerror="alert(1)">'} />)
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByRole('status')).toHaveTextContent('<img src=x onerror="alert(1)">')
  })
})
