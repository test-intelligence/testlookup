import { fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import TransitionReasonDialog from './TransitionReasonDialog'

function DialogHarness() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>Open transition</button>
      {open && (
        <TransitionReasonDialog
          title="Archive test case"
          description="Archive this governed case."
          confirmLabel="Archive"
          onCancel={() => setOpen(false)}
          onConfirm={vi.fn()}
        />
      )}
    </>
  )
}

describe('TransitionReasonDialog', () => {
  it('marks the reason required and describes the dialog and field', () => {
    render(
      <TransitionReasonDialog
        title="Archive test case"
        description="Archive this governed case."
        confirmLabel="Archive"
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: 'Archive test case' })
    const field = screen.getByRole('textbox', { name: /Reason/ })
    expect(dialog).toHaveAccessibleDescription('Archive this governed case.')
    expect(field).toBeRequired()
    expect(field).toHaveAttribute('aria-describedby', expect.stringContaining('-description'))
    expect(field).toHaveAttribute('aria-describedby', expect.stringContaining('-count'))
    expect(screen.getByRole('button', { name: 'Archive' })).toBeDisabled()
  })

  it('closes on Escape and restores focus to the trigger', () => {
    render(<DialogHarness />)
    const trigger = screen.getByRole('button', { name: 'Open transition' })
    trigger.focus()
    fireEvent.click(trigger)

    expect(screen.getByRole('textbox', { name: /Reason/ })).toHaveFocus()
    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('contains forward and backward Tab focus within the dialog', () => {
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
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(field).toHaveFocus()

    field.focus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(confirm).toHaveFocus()
  })
})
