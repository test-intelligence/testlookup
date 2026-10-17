/**
 * W06 DEF-01 §2.2-12 (dialog half): the status dialog lists the four statuses
 * with the current one checked, saves only a DIFFERENT one, sends exactly
 * `{ resolution_status }` (plus a note when one is written), toasts what the
 * Jira write-back did, renders a 409 inside itself and stays open, and shows
 * any other failure as ONE toast with the server's detail.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DefectItem } from '@/types/analytics'
import type { DefectPatchResponse } from '@/types/defectTriage'

const updateDefect = vi.hoisted(() => vi.fn())
vi.mock('@/services/defectService', () => ({ updateDefect }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

import DefectStatusDialog from './DefectStatusDialog'

const ROW: DefectItem = {
  id: 'd-1', project_id: 'p-1', title: 'Checkout fails', resolution_status: 'OPEN',
  created_at: '2026-10-01T00:00:00Z', test_name: 'test_checkout',
}

const answer = (row: Partial<DefectItem>, writeBack: DefectPatchResponse['write_back'] = null): DefectPatchResponse => ({
  defect: { ...ROW, ...row }, changed_fields: ['resolution_status'], jira: null, write_back: writeBack,
})

function renderDialog(defect: DefectItem = ROW) {
  const onClose = vi.fn()
  const onUpdated = vi.fn()
  const { container } = render(<DefectStatusDialog defect={defect} onClose={onClose} onUpdated={onUpdated} />)
  // W27 PAN-04: in the page — a region by the same name, rendered where it is mounted (no portal).
  const dialog = screen.getByRole('region', { name: 'Change status' })
  expect(container.contains(dialog)).toBe(true)
  return { onClose, onUpdated, dialog }
}

const save = () => screen.getByRole('button', { name: 'Save' })
const pick = (label: string) => fireEvent.click(screen.getByRole('radio', { name: label }))
const clickSave = async () => {
  await act(async () => {
    fireEvent.click(save())
  })
}

beforeEach(() => {
  updateDefect.mockReset()
  toast.success.mockReset()
  toast.error.mockReset()
})

describe('DefectStatusDialog', () => {
  it('lists Open, In progress, Resolved and Invalid with the current one checked', () => {
    const { dialog } = renderDialog({ ...ROW, resolution_status: 'in_progress' })
    const radios = within(dialog).getAllByRole('radio')
    expect(radios.map((r) => r.closest('label')?.textContent)).toEqual(['Open', 'In progress', 'Resolved', 'Invalid'])
    expect(screen.getByRole('radio', { name: 'In progress' })).toBeChecked()
    expect(within(dialog).getByLabelText('Note (recorded in the activity log)')).toHaveAttribute('maxLength', '1000')
  })

  it('keeps Save disabled until a different status is picked', () => {
    renderDialog()
    expect(save()).toBeDisabled()
    pick('Resolved')
    expect(save()).toBeEnabled()
    pick('Open')
    expect(save()).toBeDisabled()
  })

  it('sends exactly { resolution_status } once, with the toast-free config', async () => {
    updateDefect.mockResolvedValue(answer({ resolution_status: 'IN_PROGRESS' }))
    const { onUpdated, onClose } = renderDialog()
    pick('In progress')
    await clickSave()
    expect(updateDefect).toHaveBeenCalledTimes(1)
    expect(updateDefect).toHaveBeenCalledWith('p-1', 'd-1', { resolution_status: 'IN_PROGRESS' }, { suppressToast: true })
    expect(onUpdated).toHaveBeenCalledWith({ ...ROW, resolution_status: 'IN_PROGRESS' })
    expect(toast.success).toHaveBeenCalledWith('Status changed to In progress')
    expect(toast.error).not.toHaveBeenCalled()
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('adds a trimmed note when one is written', async () => {
    updateDefect.mockResolvedValue(answer({ resolution_status: 'RESOLVED' }))
    renderDialog()
    pick('Resolved')
    fireEvent.change(screen.getByLabelText('Note (recorded in the activity log)'), { target: { value: '  fixed in 2.4  ' } })
    await clickSave()
    expect(updateDefect).toHaveBeenCalledWith(
      'p-1', 'd-1', { resolution_status: 'RESOLVED', note: 'fixed in 2.4' }, { suppressToast: true },
    )
  })

  it.each([
    [{ status: 'sent' }, 'Status changed to Resolved · Jira updated'],
    [{ status: 'not_sent', reason: 'offline_mode' }, 'Status changed to Resolved · Jira not updated (offline)'],
    [{ status: 'skipped', reason: 'already_in_status' }, 'Status changed to Resolved'],
  ])('toasts the Jira write-back %o', async (writeBack, text) => {
    updateDefect.mockResolvedValue(answer({ resolution_status: 'RESOLVED' }, writeBack))
    renderDialog()
    pick('Resolved')
    await clickSave()
    expect(toast.success).toHaveBeenCalledWith(text)
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('adds a second, error toast when the write-back failed', async () => {
    updateDefect.mockResolvedValue(answer({ resolution_status: 'RESOLVED' }, { status: 'failed', reason: 'HTTP 400' }))
    renderDialog()
    pick('Resolved')
    await clickSave()
    expect(toast.success).toHaveBeenCalledWith('Status changed to Resolved')
    expect(toast.error).toHaveBeenCalledWith('Jira not updated: HTTP 400')
  })

  it('renders a 409 inside the dialog and stays open', async () => {
    const message = 'This test already has an open defect (ABC-7). Close that one first, or link this failure to it.'
    updateDefect.mockRejectedValue({
      response: { status: 409, data: { detail: { code: 'defect_already_open', message, defect_id: 'd-2' } } },
    })
    const { onUpdated, onClose, dialog } = renderDialog({ ...ROW, resolution_status: 'RESOLVED' })
    pick('Open')
    await clickSave()
    const conflict = dialog.querySelector('[data-defect-conflict]')
    expect(conflict).toHaveTextContent(message)
    expect(onUpdated).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
    expect(toast.error).not.toHaveBeenCalled()
    expect(screen.getByRole('region', { name: 'Change status' })).toBeInTheDocument()
    expect(save()).toBeEnabled()
  })

  it("shows any other failure as one toast with the server's detail", async () => {
    updateDefect.mockRejectedValue({
      response: { status: 422, data: { detail: 'resolution_status must be one of OPEN, IN_PROGRESS, RESOLVED, INVALID.' } },
    })
    const { onClose, dialog } = renderDialog()
    pick('Invalid')
    await clickSave()
    expect(toast.error).toHaveBeenCalledTimes(1)
    expect(toast.error).toHaveBeenCalledWith('resolution_status must be one of OPEN, IN_PROGRESS, RESOLVED, INVALID.')
    expect(dialog.querySelector('[data-defect-conflict]')).toBeNull()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('offers every status for a legacy word (CLOSED), none checked', () => {
    renderDialog({ ...ROW, resolution_status: 'CLOSED' })
    for (const radio of screen.getAllByRole('radio')) expect(radio).not.toBeChecked()
    expect(save()).toBeDisabled()
    pick('Resolved')
    expect(save()).toBeEnabled()
  })
})
