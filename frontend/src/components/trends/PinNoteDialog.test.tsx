/**
 * TRND-04: the Pin a note dialog — prefilled days, the 280-character text,
 * `Only for suite …` only when the page has a suite, the body the route reads,
 * an edit that sends only what changed, and the server's refusal as text.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const actions = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), remove: vi.fn() }))
vi.mock('@/hooks/useTimelineNotes', () => ({ useTimelineNoteActions: () => ({ projectId: 'p1', ...actions }) }))
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), toasts) }))

import PinNoteDialog, { noteFormError } from './PinNoteDialog'
import type { TimelineNote } from '@/types/timelineNotes'

const field = (name: string) => document.querySelector(`[data-pin-note="${name}"]`) as HTMLInputElement
const save = () => field('save') as unknown as HTMLButtonElement

const NOTE: TimelineNote = {
  id: 'n1',
  project_id: 'p1',
  starts_on: '2026-09-10',
  ends_on: '2026-09-12',
  text: 'release freeze',
  suite_name: 'Checkout',
  created_by_name: 'Ada',
  can_edit: true,
  created_at: null,
  updated_at: null,
}

beforeEach(() => {
  for (const fn of Object.values(actions)) fn.mockReset()
  actions.create.mockResolvedValue(NOTE)
  actions.update.mockResolvedValue(NOTE)
  toasts.success.mockReset()
})

describe('PinNoteDialog', () => {
  it('opens on the prefilled days with an empty text, Save disabled until there is text', () => {
    render(<PinNoteDialog range={{ from: '2026-09-13', to: '2026-09-15' }} suite={null} onClose={vi.fn()} />)
    expect(screen.getByRole('region', { name: 'Pin a note' })).toBeInTheDocument()
    expect(field('from').value).toBe('2026-09-13')
    expect(field('to').value).toBe('2026-09-15')
    expect(field('count')).toHaveTextContent('0 / 280')
    expect(save()).toBeDisabled()
    fireEvent.change(field('text'), { target: { value: 'release freeze' } })
    expect(field('count')).toHaveTextContent('14 / 280')
    expect(save()).toBeEnabled()
    // No page suite: no suite checkbox.
    expect(field('suite')).toBeNull()
  })

  it('pins a project-wide note: the body names the days and the trimmed text', async () => {
    const onClose = vi.fn()
    render(<PinNoteDialog range={{ from: '2026-09-13', to: '2026-09-15' }} suite={null} onClose={onClose} />)
    fireEvent.change(field('text'), { target: { value: '  release freeze  ' } })
    fireEvent.click(save())
    await waitFor(() => expect(actions.create).toHaveBeenCalledTimes(1))
    expect(actions.create).toHaveBeenCalledWith({ starts_on: '2026-09-13', ends_on: '2026-09-15', text: 'release freeze', suite_name: null })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(toasts.success).toHaveBeenCalledWith('Note pinned')
  })

  it('offers "Only for suite" when the page has a suite, and sends it when ticked', async () => {
    render(<PinNoteDialog range={{ from: '2026-09-13', to: '2026-09-13' }} suite="Checkout" onClose={vi.fn()} />)
    expect(field('suite').checked).toBe(false)
    expect(screen.getByText(/Only for suite/)).toHaveTextContent('Only for suite Checkout')
    fireEvent.click(field('suite'))
    fireEvent.change(field('text'), { target: { value: 'checkout down' } })
    fireEvent.click(save())
    await waitFor(() => expect(actions.create).toHaveBeenCalled())
    expect(actions.create.mock.calls[0][0]).toMatchObject({ starts_on: '2026-09-13', ends_on: '2026-09-13', suite_name: 'Checkout' })
  })

  it('refuses an end before the start, and a range over 366 days, before sending', () => {
    render(<PinNoteDialog range={{ from: '2026-09-13', to: '2026-09-15' }} suite={null} onClose={vi.fn()} />)
    fireEvent.change(field('text'), { target: { value: 'x' } })
    fireEvent.change(field('to'), { target: { value: '2026-09-01' } })
    expect(save()).toBeDisabled()
    expect(screen.getByText('A note ends on or after the day it starts, at most 366 days later.')).toBeInTheDocument()
    expect(noteFormError('2025-01-01', '2026-01-03', 'x')).toMatch(/at most 366/)
    expect(noteFormError('2025-01-01', '2026-01-02', 'x')).toBeNull()
    expect(noteFormError('2026-09-01', '2026-09-01', ' '.repeat(3))).toBe('A note is 1 to 280 characters.')
    expect(noteFormError('2026-09-01', '2026-09-01', 'é'.repeat(281))).toBe('A note is 1 to 280 characters.')
  })

  it('edits: sends only the fields that changed; an unchanged note sends nothing', async () => {
    const onClose = vi.fn()
    const view = render(<PinNoteDialog range={null} note={NOTE} suite="Checkout" onClose={onClose} />)
    expect(screen.getByRole('region', { name: 'Edit note' })).toBeInTheDocument()
    expect(field('suite').checked).toBe(true)
    fireEvent.click(save())
    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
    expect(actions.update).not.toHaveBeenCalled()
    view.unmount()

    render(<PinNoteDialog range={null} note={NOTE} suite={null} onClose={vi.fn()} />)
    fireEvent.change(field('text'), { target: { value: 'release freeze (extended)' } })
    fireEvent.change(field('to'), { target: { value: '2026-09-14' } })
    fireEvent.click(field('suite'))
    fireEvent.click(save())
    await waitFor(() => expect(actions.update).toHaveBeenCalledTimes(1))
    expect(actions.update).toHaveBeenCalledWith('n1', { ends_on: '2026-09-14', text: 'release freeze (extended)', suite_name: null })
  })

  it('shows the server’s refusal as text', async () => {
    actions.create.mockRejectedValue({ response: { status: 403, data: { detail: '<b>Only</b> the note’s author or a QA lead can change this note.' } } })
    render(<PinNoteDialog range={{ from: '2026-09-13', to: '2026-09-13' }} suite={null} onClose={vi.fn()} />)
    fireEvent.change(field('text'), { target: { value: 'x' } })
    fireEvent.click(save())
    expect(await screen.findByRole('alert')).toHaveTextContent('<b>Only</b> the note’s author or a QA lead can change this note.')
    expect(document.querySelector('[data-pin-note-refusal] b')).toBeNull()
  })
})
