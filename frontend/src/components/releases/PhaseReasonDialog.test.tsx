/**
 * REL-10: the dialog the release page opens on a phase-gate 409. It quotes the
 * server's sentence, refuses a reason the server would refuse (under 3
 * characters once trimmed), and hands back the TRIMMED reason exactly once.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import PhaseReasonDialog from './PhaseReasonDialog'

const NO_GO = "This phase's gate says NO_GO: test_pay (checkout-e2e) is FAILED. Fix it, or supply gate_override_reason to complete it anyway."

function renderDialog(action: 'complete' | 'skip' = 'complete', busy = false) {
  const onSubmit = vi.fn()
  const onCancel = vi.fn()
  const view = render(
    <PhaseReasonDialog
      phaseName="Regression"
      action={action}
      serverMessage={NO_GO}
      busy={busy}
      onSubmit={onSubmit}
      onCancel={onCancel}
    />,
  )
  return { ...view, onSubmit, onCancel }
}

const reason = () => screen.getByLabelText('Reason (recorded in the audit log)')

describe('PhaseReasonDialog', () => {
  it('asks to complete the phase anyway, quoting the server sentence verbatim', () => {
    const { container } = renderDialog('complete')
    expect(screen.getByRole('region', { name: 'Complete "Regression" anyway?' })).toBeInTheDocument()
    expect(container.querySelector('[data-phase-gate-detail]')).toHaveTextContent(NO_GO)
    expect(screen.getByText('At least 3 characters.')).toBeInTheDocument()
    expect(reason()).toHaveAttribute('maxLength', '1000')
    expect(screen.getByRole('button', { name: 'Complete with override' })).toBeInTheDocument()
  })

  it('asks to skip with its own title and button', () => {
    renderDialog('skip')
    expect(screen.getByRole('region', { name: 'Skip "Regression"?' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Skip phase' })).toBeInTheDocument()
  })

  // W27 PAN-02: it renders in the page, where the release puts it, and keeps
  // the dialog's hooks (its heading id, data-phase-reason-dialog, the X's label).
  it('renders in the page, not over it, with the hooks the dialog had', () => {
    const { container, onCancel } = renderDialog('complete')
    const form = screen.getByRole('region', { name: 'Complete "Regression" anyway?' })
    expect(container.contains(form)).toBe(true)
    expect(document.querySelector('[role="dialog"], [aria-modal]')).toBeNull()
    expect(form).toHaveAttribute('data-phase-reason-dialog', 'complete')
    expect(form).toHaveAttribute('aria-labelledby', 'phase-reason-title')
    expect(document.getElementById('phase-reason-title')).toHaveTextContent('Complete "Regression" anyway?')
    // The reason field has focus, and Escape inside the form cancels it.
    expect(reason()).toHaveFocus()
    fireEvent.keyDown(reason(), { key: 'Escape' })
    expect(onCancel).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Close phase reason dialog' }))
    expect(onCancel).toHaveBeenCalledTimes(2)
  })

  it('mid-submit, Escape and the X do not close it', () => {
    const { onCancel } = renderDialog('skip', true)
    fireEvent.keyDown(reason(), { key: 'Escape' })
    expect(screen.getByRole('button', { name: 'Close phase reason dialog' })).toBeDisabled()
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('keeps Submit disabled until the TRIMMED reason has 3 characters', () => {
    const { onSubmit } = renderDialog('complete')
    const submit = screen.getByRole('button', { name: 'Complete with override' })
    expect(submit).toBeDisabled()
    fireEvent.change(reason(), { target: { value: '  ab   ' } })
    expect(submit).toBeDisabled()
    fireEvent.click(submit)
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.change(reason(), { target: { value: ' abc ' } })
    expect(submit).toBeEnabled()
  })

  it('submits the trimmed reason once', () => {
    const { onSubmit, onCancel } = renderDialog('complete')
    fireEvent.change(reason(), { target: { value: '  Customer waiver CAB-142  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Complete with override' }))
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onSubmit).toHaveBeenCalledWith('Customer waiver CAB-142')
    expect(onCancel).not.toHaveBeenCalled()
  })

  it('Cancel closes without submitting', () => {
    const { onSubmit, onCancel } = renderDialog('skip')
    fireEvent.change(reason(), { target: { value: 'Deferred to 2026.12' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('while the request is in flight Submit and Cancel are disabled', () => {
    renderDialog('skip', true)
    expect(screen.getByRole('button', { name: 'Skip phase' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled()
  })
})
