import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const update = vi.fn()
const preview = vi.fn()
vi.mock('@/services/ssoService', () => ({
  updateSSOConfig: (...a: unknown[]) => update(...a),
  previewSSORole: (...a: unknown[]) => preview(...a),
}))

import SSORoleMappingEditor from './SSORoleMappingEditor'
import type { SSOConfig } from '@/services/ssoService'

const CONFIG = {
  id: 'cfg-1',
  role_mapping: { 'qa-leads': 'QA_LEAD' },
  default_role: 'VIEWER',
  group_attribute: 'memberOf',
} as unknown as SSOConfig

describe('SSORoleMappingEditor (W20 ADM-08a)', () => {
  beforeEach(() => {
    update.mockReset()
    preview.mockReset()
  })

  it('shows the stored rows and saves an added group through the PATCH', async () => {
    update.mockResolvedValue({})
    const onSaved = vi.fn()
    render(<SSORoleMappingEditor config={CONFIG} onSaved={onSaved} />)
    expect(screen.getByLabelText('IdP group 1')).toHaveValue('qa-leads')
    fireEvent.click(screen.getByRole('button', { name: /add group/i }))
    fireEvent.change(screen.getByLabelText('IdP group 2'), { target: { value: ' testers ' } })
    fireEvent.change(screen.getByLabelText('Role for group 2'), { target: { value: 'TESTER' } })
    fireEvent.change(screen.getByLabelText('Default role'), { target: { value: 'TESTER' } })
    fireEvent.click(screen.getByRole('button', { name: /save role mapping/i }))
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
    expect(update).toHaveBeenCalledWith('cfg-1', {
      role_mapping: { 'qa-leads': 'QA_LEAD', testers: 'TESTER' },
      default_role: 'TESTER',
      group_attribute: 'memberOf',
    })
    expect(screen.getByRole('status')).toHaveTextContent('Role mapping saved.')
  })

  it('previews the unsaved rows and says when the ceiling clamped', async () => {
    preview.mockResolvedValue({ role: 'QA_ENGINEER', matched_groups: ['qa-leads'], source: 'mapping', ceiling: 'QA_ENGINEER', clamped: true })
    render(<SSORoleMappingEditor config={CONFIG} onSaved={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('Groups to preview'), { target: { value: 'qa-leads, other ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }))
    const out = await screen.findByTestId('sso-role-preview')
    expect(out).toHaveTextContent('QA Engineer')
    expect(out).toHaveTextContent('matched qa-leads')
    expect(out).toHaveTextContent('capped at QA Engineer')
    expect(preview).toHaveBeenCalledWith('cfg-1', { groups: ['qa-leads', 'other'], role_mapping: { 'qa-leads': 'QA_LEAD' }, default_role: 'VIEWER' })
  })

  it('refuses to save a group listed twice', () => {
    render(<SSORoleMappingEditor config={CONFIG} onSaved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /add group/i }))
    fireEvent.change(screen.getByLabelText('IdP group 2'), { target: { value: 'qa-leads' } })
    expect(screen.getByRole('alert')).toHaveTextContent('listed twice')
    expect(screen.getByRole('button', { name: /save role mapping/i })).toBeDisabled()
  })

  it("shows the server's refusal", async () => {
    update.mockRejectedValue(Object.assign(new Error('400'), { response: { data: { detail: "Invalid role 'ROOT' in role_mapping for group 'x'" } } }))
    render(<SSORoleMappingEditor config={CONFIG} onSaved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /save role mapping/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent("Invalid role 'ROOT'")
  })

  it('removing the last row leaves only the default role', () => {
    render(<SSORoleMappingEditor config={CONFIG} onSaved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Remove group 1' }))
    expect(screen.getByText(/everyone gets the default role/)).toBeInTheDocument()
  })

  it('W20 ADM-08b: an OIDC config edits its groups CLAIM, not the SAML attribute', async () => {
    update.mockResolvedValue({})
    const oidc = { ...CONFIG, provider_type: 'OIDC', group_attribute: null, oidc_groups_claim: 'roles' } as unknown as SSOConfig
    render(<SSORoleMappingEditor config={oidc} onSaved={vi.fn()} />)
    expect(screen.getByLabelText('Groups claim')).toHaveValue('roles')
    expect(screen.queryByLabelText('Group attribute')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /save role mapping/i }))
    await waitFor(() => expect(update).toHaveBeenCalled())
    expect(update).toHaveBeenCalledWith('cfg-1', {
      role_mapping: { 'qa-leads': 'QA_LEAD' }, default_role: 'VIEWER', oidc_groups_claim: 'roles',
    })
  })
})
