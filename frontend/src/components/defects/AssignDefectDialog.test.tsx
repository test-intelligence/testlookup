/**
 * W06 DEF-01 §2.2-13: the assign dialog offers the project's assignable
 * people (and keeps a non-member current assignee), prefills the team only
 * when it is the defect's own, and sends ONLY what changed: the assignee as
 * an id (`''` → `null`), the team trimmed (`''` → `null`).
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ProjectMember } from '@/services/userManagementService'
import type { DefectItem } from '@/types/analytics'

const updateDefect = vi.hoisted(() => vi.fn())
vi.mock('@/services/defectService', () => ({ updateDefect }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock('react-hot-toast', () => ({ default: toast }))

const member = (userId: string, fullName: string | null, username: string, flags: Partial<ProjectMember> = {}): ProjectMember => ({
  id: `m-${userId}`, user_id: userId, project_id: 'p-1', role: 'QA_ENGINEER', created_at: '2026-01-01T00:00:00Z',
  email: `${username}@example.test`, username, full_name: fullName, ...flags,
})
const MEMBERS = [
  member('u-priya', 'Priya Nair', 'priya'),
  member('u-arun', null, 'arun'),
  member('u-qa-bot', 'QA lead (Project One)', 'qa-lead-p-1', { is_synthetic: true }),
  member('u-ci', 'CI uploader', 'ci-uploader', { is_service_account: true }),
  member('u-gone', 'Former Colleague', 'former', { is_active: false }),
]
vi.mock('@/hooks/useUserManagement', () => ({ useProjectMembers: () => ({ data: MEMBERS, isLoading: false }) }))

import AssignDefectDialog from './AssignDefectDialog'

const ROW: DefectItem = {
  id: 'd-1', project_id: 'p-1', title: 'Checkout fails', resolution_status: 'OPEN',
  created_at: '2026-10-01T00:00:00Z', test_name: 'test_checkout',
}

function renderDialog(defect: DefectItem = ROW) {
  const onClose = vi.fn()
  const onUpdated = vi.fn()
  render(<AssignDefectDialog defect={defect} onClose={onClose} onUpdated={onUpdated} />)
  return { onClose, onUpdated, dialog: screen.getByRole('dialog', { name: 'Assign defect' }) }
}

const assignee = () => screen.getByRole('combobox', { name: 'Assignee' })
const team = () => screen.getByLabelText('Team')
const save = () => screen.getByRole('button', { name: 'Save' })
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

describe('AssignDefectDialog', () => {
  it('omits service, synthetic and inactive members and keeps a non-member current assignee', () => {
    renderDialog({ ...ROW, assignee_user_id: 'u-admin', assignee_name: 'Dana Admin' })
    expect(within(assignee()).getAllByRole('option').map((o) => o.textContent))
      .toEqual(['Unassigned', 'Dana Admin', 'arun', 'Priya Nair'])
    expect(assignee()).toHaveValue('u-admin')
  })

  it('keeps Save disabled until something changes', () => {
    renderDialog({ ...ROW, assignee_user_id: 'u-priya', assignee_name: 'Priya Nair' })
    expect(save()).toBeDisabled()
    fireEvent.change(assignee(), { target: { value: 'u-arun' } })
    expect(save()).toBeEnabled()
    fireEvent.change(assignee(), { target: { value: 'u-priya' } })
    expect(save()).toBeDisabled()
  })

  it('sends only the assignee when only the assignee changed', async () => {
    updateDefect.mockResolvedValue({
      defect: { ...ROW, assignee_user_id: 'u-priya', assignee_name: 'Priya Nair' }, changed_fields: ['assignee'],
    })
    const { onUpdated, onClose } = renderDialog({ ...ROW, owner_team: 'payments', owner_source: 'defect' })
    fireEvent.change(assignee(), { target: { value: 'u-priya' } })
    await clickSave()
    expect(updateDefect).toHaveBeenCalledTimes(1)
    expect(updateDefect).toHaveBeenCalledWith('p-1', 'd-1', { assignee_user_id: 'u-priya' }, { suppressToast: true })
    expect(onUpdated).toHaveBeenCalledWith({ ...ROW, assignee_user_id: 'u-priya', assignee_name: 'Priya Nair' })
    expect(toast.success).toHaveBeenCalledWith('Assigned to Priya Nair')
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("sends null for Unassigned and says so", async () => {
    updateDefect.mockResolvedValue({ defect: { ...ROW, assignee_user_id: null, assignee_name: null }, changed_fields: ['assignee'] })
    renderDialog({ ...ROW, assignee_user_id: 'u-priya', assignee_name: 'Priya Nair' })
    fireEvent.change(assignee(), { target: { value: '' } })
    await clickSave()
    expect(updateDefect).toHaveBeenCalledWith('p-1', 'd-1', { assignee_user_id: null }, { suppressToast: true })
    expect(toast.success).toHaveBeenCalledWith('Unassigned')
  })

  it("prefills the team only when it is the defect's own", () => {
    renderDialog({ ...ROW, owner_team: 'payments', owner_source: 'defect' })
    expect(team()).toHaveValue('payments')
    expect(team()).toHaveAttribute('maxLength', '255')
  })

  it('leaves the team empty when the ownership rules gave it', () => {
    renderDialog({ ...ROW, owner_team: 'checkout-team', owner_source: 'rule' })
    expect(team()).toHaveValue('')
    expect(save()).toBeDisabled()
  })

  it('sends only the trimmed team when only the team changed, and null when it is cleared', async () => {
    updateDefect.mockResolvedValue({ defect: { ...ROW, owner_team: 'checkout' }, changed_fields: ['owner_team'] })
    renderDialog()
    fireEvent.change(team(), { target: { value: '  checkout  ' } })
    await clickSave()
    expect(updateDefect).toHaveBeenLastCalledWith('p-1', 'd-1', { owner_team: 'checkout' }, { suppressToast: true })
    expect(toast.success).toHaveBeenLastCalledWith('Team set to checkout')
  })

  it('clears an own team with null', async () => {
    updateDefect.mockResolvedValue({ defect: { ...ROW, owner_team: null }, changed_fields: ['owner_team'] })
    renderDialog({ ...ROW, owner_team: 'payments', owner_source: 'defect' })
    fireEvent.change(team(), { target: { value: '   ' } })
    await clickSave()
    expect(updateDefect).toHaveBeenCalledWith('p-1', 'd-1', { owner_team: null }, { suppressToast: true })
    expect(toast.success).toHaveBeenCalledWith('Team cleared')
  })

  it("shows the server's refusal as one toast and stays open", async () => {
    updateDefect.mockRejectedValue({
      response: { status: 422, data: { detail: 'That user cannot be assigned defects in this project.' } },
    })
    const { onClose } = renderDialog()
    fireEvent.change(assignee(), { target: { value: 'u-arun' } })
    await clickSave()
    expect(toast.error).toHaveBeenCalledTimes(1)
    expect(toast.error).toHaveBeenCalledWith('That user cannot be assigned defects in this project.')
    expect(onClose).not.toHaveBeenCalled()
  })
})
