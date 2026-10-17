/**
 * UX redesign P4: /quarantine became Flaky tests (`/flaky`), and a new
 * proposal waits under its Proposed tab. The modal and its toast named the
 * old URL, which now redirects to the Quarantined tab, where a proposal is not.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ProposeQuarantineModal from './ProposeQuarantineModal'

const { propose, success } = vi.hoisted(() => ({ propose: vi.fn(), success: vi.fn() }))
vi.mock('@/services/flakyQuarantineService', () => ({ flakyQuarantineService: { propose } }))
vi.mock('react-hot-toast', () => ({ default: { success, error: vi.fn() } }))

function renderModal() {
  return render(
    <ProposeQuarantineModal
      prefill={{ project_id: 'p-1', test_fingerprint: 'fp', test_name: 'checkout total' }}
      source="test"
      onClose={vi.fn()}
    />,
  )
}

describe('ProposeQuarantineModal', () => {
  beforeEach(() => {
    propose.mockReset()
    success.mockReset()
  })

  it('says where the proposal waits: Flaky tests › Proposed, not the old /quarantine URL', () => {
    const { container } = renderModal()
    // W27 PAN-04: a region in the page by the dialog's name, rendered where it is mounted.
    const dialog = screen.getByRole('region', { name: 'Propose quarantine' })
    expect(container.contains(dialog)).toBe(true)
    expect(document.querySelector('[aria-modal]')).toBeNull()
    expect(dialog).toHaveTextContent('A QA Lead approves or rejects it under Flaky tests › Proposed')
    expect(dialog).not.toHaveTextContent('/quarantine')
  })

  it('Escape inside closes it, Cancel closes it; neither sends anything', () => {
    const onClose = vi.fn()
    render(
      <ProposeQuarantineModal
        prefill={{ project_id: 'p-1', test_fingerprint: 'fp', test_name: 'checkout total' }}
        source="test"
        onClose={onClose}
      />,
    )
    // Focus lands on the reason (the first field), as the modal's did.
    expect(document.activeElement).toBe(screen.getByRole('textbox'))
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Escape' })
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(2)
    expect(propose).not.toHaveBeenCalled()
  })

  it('the toast after proposing names the same place', async () => {
    propose.mockResolvedValue({ status: 'PROPOSED' })
    renderModal()
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'fails on every third run' } })
    fireEvent.click(screen.getByRole('button', { name: /propose/i }))
    await waitFor(() => expect(success).toHaveBeenCalled())
    const [message] = success.mock.calls[0] as [string]
    expect(message).toContain('Flaky tests › Proposed')
    expect(message).not.toContain('/quarantine')
  })
})
