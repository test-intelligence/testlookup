import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'

const list = vi.fn()
const put = vi.fn()
vi.mock('@/services/ssoService', () => ({
  listSCIMGroups: () => list(),
  putSCIMGroupMappings: (...a: unknown[]) => put(...a),
}))
vi.mock('@/services/projectsService', () => ({
  projectsService: { list: () => Promise.resolve([{ id: 'p-pay', name: 'Payment Service' }, { id: 'p-gw', name: 'API Gateway' }]) },
}))

import SCIMGroupsSection from './SCIMGroupsSection'

const ROLES = ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD']
const GROUP = { id: 'g1', display_name: 'payments-qa', external_id: null, member_count: 3, mappings: [] }

describe('SCIMGroupsSection (W20 ADM-09)', () => {
  beforeEach(() => {
    list.mockReset()
    put.mockReset()
  })

  it('lists pushed groups with member counts and saves a project grant', async () => {
    list.mockResolvedValue({ groups: [GROUP], roles: ROLES })
    put.mockResolvedValue({ reconciled_users: 3, groups: [{ ...GROUP, mappings: [{ project_id: 'p-pay', project_name: 'Payment Service', role: 'QA_ENGINEER' }] }] })
    render(<SCIMGroupsSection />)
    const card = await screen.findByTestId('scim-group')
    expect(card).toHaveTextContent('payments-qa')
    expect(card).toHaveTextContent('3 members')
    fireEvent.click(within(card).getByRole('button', { name: /grant a project/i }))
    await waitFor(() => expect(within(card).getAllByRole('option', { name: 'Payment Service' }).length).toBeGreaterThan(0))
    fireEvent.change(within(card).getByLabelText('Project 1'), { target: { value: 'p-pay' } })
    fireEvent.click(within(card).getByRole('button', { name: /save grants/i }))
    await waitFor(() => expect(put).toHaveBeenCalledWith('g1', [{ project_id: 'p-pay', role: 'QA_ENGINEER' }]))
    expect(await screen.findByRole('status')).toHaveTextContent('3 members reconciled')
    expect(screen.getByLabelText('Project 1')).toHaveValue('p-pay')
  })

  it('says when no group was pushed, and shows a refusal', async () => {
    list.mockResolvedValue({ groups: [], roles: ROLES })
    const { unmount } = render(<SCIMGroupsSection />)
    expect(await screen.findByText('No groups pushed yet.')).toBeInTheDocument()
    unmount()
    list.mockRejectedValue(Object.assign(new Error('403'), { response: { data: { detail: 'Insufficient permissions' } } }))
    render(<SCIMGroupsSection />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Insufficient permissions')
  })
})
