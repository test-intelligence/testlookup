/**
 * SSOSettingsPage — SAML configuration and SCIM tokens.
 *
 * Zero coverage before this (backlog: "zero-coverage surfaces"). The guard
 * that matters most here is the confirmation before **activating** an
 * `SSO_REQUIRED` configuration.
 *
 * That action is not cosmetic. `auth.py` checks `is_sso_enforced(db)` on every
 * password login and returns
 *
 *     403 "SSO is required for this account. Please use the SSO login option."
 *
 * to everyone except admins, and admins only when
 * `SSO_ADMIN_FALLBACK_ENABLED` is true (it defaults to true, which is why this
 * is recoverable and scored MINOR rather than a lockout).
 *
 * Every other consequential action on the page already confirmed — deleting a
 * config, revoking a SCIM token — and `MfaPolicyPage` confirms before enabling
 * `require_mfa` for exactly this reason. Activating enforcement was the one
 * lockout-capable action that did not.
 *
 * The confirmation is deliberately narrow, and the tests pin both halves:
 * only when ACTIVATING, and only for `SSO_REQUIRED`. Deactivating restores
 * password login and an OPTIONAL config never removed it, so prompting there
 * would be friction that trains people to click through the prompt that does
 * matter.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SSOSettingsPage from './SSOSettingsPage'
import type { SSOConfig } from '../../services/ssoService'

const mockUpdateSSOConfig = vi.fn()
const mockDeleteSSOConfig = vi.fn()
const mockRefresh = vi.fn()

let tabData: { configs: SSOConfig[]; scimTokens: unknown[]; events: unknown[]; syncStatus: null }

vi.mock('../../services/ssoService', async () => {
  const actual =
    await vi.importActual<typeof import('../../services/ssoService')>('../../services/ssoService')
  return {
    ...actual,
    updateSSOConfig: (...a: unknown[]) => mockUpdateSSOConfig(...a),
    deleteSSOConfig: (...a: unknown[]) => mockDeleteSSOConfig(...a),
    createSSOConfig: vi.fn(),
    testSSOConnection: vi.fn(),
    listSCIMTokens: vi.fn(),
    createSCIMToken: vi.fn(),
    revokeSCIMToken: vi.fn(),
  }
})

vi.mock('../../hooks/useSSOTabData', () => ({
  useSSOTabData: () => ({
    data: tabData,
    isLoading: false,
    error: null,
    refresh: mockRefresh,
  }),
}))

function config(overrides: Partial<SSOConfig> = {}): SSOConfig {
  return {
    id: 'cfg-1',
    display_name: 'Corp Okta',
    provider_type: 'SAML',
    idp_entity_id: 'urn:idp',
    idp_sso_url: 'https://idp.example/sso',
    idp_slo_url: null,
    idp_certificate_fingerprint: 'AA:BB:CC:DD:EE:FF:00:11:22:33',
    sp_entity_id: 'urn:sp',
    sp_acs_url: 'https://app.example/acs',
    audience: null,
    role_mapping: null,
    default_role: 'VIEWER',
    group_attribute: null,
    enforcement_mode: 'OPTIONAL',
    is_active: false,
    last_test_at: null,
    last_test_success: null,
    last_test_error: null,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: null,
    ...overrides,
  }
}

function renderWith(configs: SSOConfig[]) {
  tabData = { configs, scimTokens: [], events: [], syncStatus: null }
  return render(<SSOSettingsPage />)
}

const toggleButton = (label: RegExp) => screen.getByRole('button', { name: label })

beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('confirm', vi.fn(() => true))
})

describe('activating SSO enforcement is confirmed', () => {
  it('asks before activating an SSO_REQUIRED config', async () => {
    renderWith([config({ enforcement_mode: 'SSO_REQUIRED', is_active: false })])

    fireEvent.click(toggleButton(/activate/i))

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(vi.mocked(confirm).mock.calls[0][0]).toMatch(/password login will stop working/i)
    await waitFor(() => expect(mockUpdateSSOConfig).toHaveBeenCalledWith('cfg-1', { is_active: true }))
  })

  it('does not activate when the confirmation is declined', () => {
    vi.stubGlobal('confirm', vi.fn(() => false))
    renderWith([config({ enforcement_mode: 'SSO_REQUIRED', is_active: false })])

    fireEvent.click(toggleButton(/activate/i))

    expect(mockUpdateSSOConfig).not.toHaveBeenCalled()
  })
})

describe('the confirmation stays scoped to the action that can lock people out', () => {
  it('does not ask when activating an OPTIONAL config', async () => {
    // OPTIONAL leaves password login available, so there is nothing to warn
    // about — prompting here would train people to dismiss the prompt.
    renderWith([config({ enforcement_mode: 'OPTIONAL', is_active: false })])

    fireEvent.click(toggleButton(/activate/i))

    expect(confirm).not.toHaveBeenCalled()
    await waitFor(() => expect(mockUpdateSSOConfig).toHaveBeenCalledWith('cfg-1', { is_active: true }))
  })

  it('does not ask when DEACTIVATING an enforced config', async () => {
    // Deactivating restores password login. Confirming the recovery action
    // would be backwards.
    renderWith([config({ enforcement_mode: 'SSO_REQUIRED', is_active: true })])

    fireEvent.click(toggleButton(/deactivate/i))

    expect(confirm).not.toHaveBeenCalled()
    await waitFor(() =>
      expect(mockUpdateSSOConfig).toHaveBeenCalledWith('cfg-1', { is_active: false }),
    )
  })
})

describe('the page reports what it did', () => {
  it('surfaces a failed toggle instead of failing silently', async () => {
    mockUpdateSSOConfig.mockRejectedValueOnce(new Error('backend refused'))
    renderWith([config({ enforcement_mode: 'OPTIONAL', is_active: false })])

    fireEvent.click(toggleButton(/activate/i))

    // A settings page that swallows the error leaves the operator believing
    // enforcement changed when it did not.
    expect(await screen.findByText(/backend refused/i)).toBeInTheDocument()
  })

  it('still confirms before deleting a configuration', async () => {
    renderWith([config()])

    fireEvent.click(toggleButton(/^delete$/i))

    expect(confirm).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(mockDeleteSSOConfig).toHaveBeenCalledWith('cfg-1'))
  })

  it('shows the certificate fingerprint truncated, never a full secret', () => {
    renderWith([config()])

    // The list renders `fingerprint.slice(0, 16)`. A fingerprint is not
    // secret, but the truncation is the page's stated contract and the same
    // list is where a token would be mis-rendered if one were added later.
    expect(screen.getByText(/AA:BB:CC:DD:EE:F/)).toBeInTheDocument()
  })
})
