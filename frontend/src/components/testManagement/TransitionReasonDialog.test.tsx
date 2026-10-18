import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import TransitionReasonDialog from './TransitionReasonDialog'

function DialogHarness({ busy = false }: { busy?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <div data-testid="host">
      <button type="button" onClick={() => setOpen(true)}>Open transition</button>
      {open && (
        <TransitionReasonDialog
          title="Archive test case"
          description="Archive this governed case."
          confirmLabel="Archive"
          busy={busy}
          onCancel={() => setOpen(false)}
          onConfirm={vi.fn()}
        />
      )}
    </div>
  )
}

describe('TransitionReasonDialog', () => {
  it('marks the reason required and describes the field', () => {
    render(
      <TransitionReasonDialog
        title="Archive test case"
        description="Archive this governed case."
        confirmLabel="Archive"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )

    expect(screen.getByRole('region', { name: 'Archive test case' })).toBeInTheDocument()
    const field = screen.getByRole('textbox', { name: /Reason/ })
    // The description is a paragraph in the form now; the field names it (and its count).
    expect(field).toHaveAccessibleDescription(/^Archive this governed case\./)
    expect(field).toBeRequired()
    expect(field).toHaveAttribute('aria-describedby', expect.stringContaining('-description'))
    expect(field).toHaveAttribute('aria-describedby', expect.stringContaining('-count'))
    expect(screen.getByRole('button', { name: 'Archive' })).toBeDisabled()
  })

  it('renders in the page: no dialog role, no aria-modal, no X, inside its host', () => {
    render(<DialogHarness />)
    fireEvent.click(screen.getByRole('button', { name: 'Open transition' }))

    const form = screen.getByRole('region', { name: 'Archive test case' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(screen.getByTestId('host')).toContainElement(form)
    expect(screen.queryByRole('button', { name: 'Close' })).toBeNull()
  })

  it('closes on Escape (focus inside) and restores focus to the trigger', () => {
    render(<DialogHarness />)
    const trigger = screen.getByRole('button', { name: 'Open transition' })
    trigger.focus()
    fireEvent.click(trigger)

    const field = screen.getByRole('textbox', { name: /Reason/ })
    expect(field).toHaveFocus()
    fireEvent.keyDown(field, { key: 'Escape' })

    expect(screen.queryByRole('region', { name: 'Archive test case' })).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('does not close on Escape while busy, and Cancel is disabled', () => {
    render(<DialogHarness busy />)
    fireEvent.click(screen.getByRole('button', { name: 'Open transition' }))

    fireEvent.keyDown(screen.getByRole('textbox', { name: /Reason/ }), { key: 'Escape' })
    expect(screen.getByRole('region', { name: 'Archive test case' })).toHaveAttribute('aria-busy', 'true')
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Saving…' })).toBeDisabled()
  })

  it('does not trap Tab: the key on the last control and Shift+Tab on the first are not prevented', () => {
    render(
      <TransitionReasonDialog
        title="Archive test case"
        description="Archive this governed case."
        confirmLabel="Archive"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )

    const field = screen.getByRole('textbox', { name: /Reason/ })
    fireEvent.change(field, { target: { value: 'Retention completed' } })
    const confirm = screen.getByRole('button', { name: 'Archive' })

    confirm.focus()
    expect(fireEvent.keyDown(confirm, { key: 'Tab' })).toBe(true)
    expect(confirm).toHaveFocus()

    field.focus()
    expect(fireEvent.keyDown(field, { key: 'Tab', shiftKey: true })).toBe(true)
    expect(field).toHaveFocus()
  })
})
