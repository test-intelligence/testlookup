/**
 * The log viewer's copy button is icon-only: it is named after what it copies,
 * with a tooltip, and says "Copied" once it has (the UX redesign's browser E2E
 * pass found it with no name at all, on every test's stack trace).
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/utils/clipboard', () => ({ copyTextToClipboard: vi.fn(async () => true) }))

import LogViewer from './LogViewer'

describe('LogViewer', () => {
  it('names its copy button after what it copies, then says Copied', async () => {
    render(<LogViewer content="AssertionError: boom" title="Stack trace" />)
    const copy = screen.getByRole('button', { name: 'Copy Stack trace' })
    expect(copy).toHaveAttribute('title', 'Copy Stack trace')
    await act(async () => {
      fireEvent.click(copy)
    })
    expect(await screen.findByRole('button', { name: 'Copied' })).toBeInTheDocument()
  })
})
