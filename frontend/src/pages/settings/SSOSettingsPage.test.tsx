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
 *
 * W27b PAN-06 (S1-S3): every question is asked in the page (`InlineConfirm`),
 * never with the browser's prompt: a configuration's inside its card under the
 * action row, a SCIM token's right under its row. The native `confirm` is a
 * sentinel here that must never be called.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SSOSettingsPage from './SSOSettingsPage'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'
import { createSSOConfig, revokeSCIMToken, type SSOConfig } from '../../services/ssoService'

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
    // W20 ADM-09: the SCIM tab's groups section (none pushed here).
    listSCIMGroups: () => Promise.resolve({ groups: [], roles: ['VIEWER', 'TESTER', 'QA_ENGINEER', 'QA_LEAD'] }),
    putSCIMGroupMappings: vi.fn(),
  }
})
vi.mock('@/services/projectsService', () => ({ projectsService: { list: () => Promise.resolve([]) } }))

const permissions = { isAdmin: true }
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => permissions }))

let tabError: string | null = null
const tabDataEnabled: unknown[] = []
vi.mock('../../hooks/useSSOTabData', () => ({
  useSSOTabData: (_tab: string, enabled?: boolean) => {
    tabDataEnabled.push(enabled)
    return { data: tabData, isLoading: false, error: tabError, refresh: mockRefresh }
  },
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
/** The in-page question (W27b), or null. */
const strip = () => document.querySelector('[data-inline-confirm]') as HTMLElement | null
const question = () => (strip() as HTMLElement).querySelector('p')?.textContent
const ENFORCEMENT_SENTENCE =
  'Activate SSO enforcement? Password login will stop working for everyone except admins using the fallback. ' +
  'They must sign in through the identity provider.'

beforeEach(() => {
  vi.clearAllMocks()
  // A sentinel: the page asks in the page now, so the native prompt is never called.
  vi.stubGlobal('confirm', vi.fn(() => true))
  permissions.isAdmin = true
  tabError = null
  tabDataEnabled.length = 0
})

describe('who the page is for, and a failed load (browser E2E pass)', () => {
  it('a non-admin (a QA lead opens the route): the header and the admin-required state, nothing asked', () => {
    permissions.isAdmin = false
    renderWith([])
    expect(screen.getByRole('heading', { level: 1, name: 'SSO & Identity Management' })).toBeInTheDocument()
    expect(screen.getByText('Admin access required')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add SSO Configuration/ })).toBeNull()
    expect(screen.queryByText(/No SSO configurations/)).toBeNull()
    // Every SSO endpoint answers a QA lead 403.
    expect(tabDataEnabled).toEqual([false])
  })

  it('a failed load says so with Retry, and never claims "No SSO configurations" it could not read', () => {
    tabError = 'Request failed with status code 500'
    renderWith([])
    const banner = document.querySelector('[data-sso-load-error]') as HTMLElement
    expect(banner).toHaveTextContent('Could not load this tab: Request failed with status code 500')
    expect(screen.queryByText(/No SSO configurations/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    expect(mockRefresh).toHaveBeenCalledTimes(1)
  })
})

describe('activating SSO enforcement is confirmed', () => {
  it('asks before activating an SSO_REQUIRED config', async () => {
    renderWith([config({ enforcement_mode: 'SSO_REQUIRED', is_active: false })])

    fireEvent.click(toggleButton(/activate/i))

    // Asked in the card, under its action row; nothing sent yet.
    expect(mockUpdateSSOConfig).not.toHaveBeenCalled()
    expect(question()).toMatch(/password login will stop working/i)
    expect(question()).toBe(ENFORCEMENT_SENTENCE)
    const card = screen.getByRole('heading', { level: 3, name: 'Corp Okta' }).closest('.space-y-2') as HTMLElement
    expect(card.contains(strip())).toBe(true)
    const actions = within(card).getByRole('button', { name: 'Test Connection' }).parentElement as HTMLElement
    expect(actions.nextElementSibling).toBe(strip())

    fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Activate' }))
    expect(strip()).toBeNull()
    await waitFor(() => expect(mockUpdateSSOConfig).toHaveBeenCalledWith('cfg-1', { is_active: true }))
    expect(mockUpdateSSOConfig).toHaveBeenCalledTimes(1)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('does not activate when the confirmation is declined', () => {
    renderWith([config({ enforcement_mode: 'SSO_REQUIRED', is_active: false })])

    fireEvent.click(toggleButton(/activate/i))
    fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }))
    expect(strip()).toBeNull()

    // Escape in the strip is Cancel too.
    fireEvent.click(toggleButton(/activate/i))
    fireEvent.keyDown(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
    expect(strip()).toBeNull()

    expect(mockUpdateSSOConfig).not.toHaveBeenCalled()
  })
})

describe('the confirmation stays scoped to the action that can lock people out', () => {
  it('does not ask when activating an OPTIONAL config', async () => {
    // OPTIONAL leaves password login available, so there is nothing to warn
    // about — prompting here would train people to dismiss the prompt.
    renderWith([config({ enforcement_mode: 'OPTIONAL', is_active: false })])

    fireEvent.click(toggleButton(/activate/i))

    expect(strip()).toBeNull()
    expect(confirm).not.toHaveBeenCalled()
    await waitFor(() => expect(mockUpdateSSOConfig).toHaveBeenCalledWith('cfg-1', { is_active: true }))
  })

  it('does not ask when DEACTIVATING an enforced config', async () => {
    // Deactivating restores password login. Confirming the recovery action
    // would be backwards.
    renderWith([config({ enforcement_mode: 'SSO_REQUIRED', is_active: true })])

    fireEvent.click(toggleButton(/deactivate/i))

    expect(strip()).toBeNull()
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

    expect(mockDeleteSSOConfig).not.toHaveBeenCalled()
    expect(question()).toBe('Are you sure you want to delete this SSO configuration?')
    fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Delete' }))
    expect(strip()).toBeNull()
    await waitFor(() => expect(mockDeleteSSOConfig).toHaveBeenCalledWith('cfg-1'))
    expect(mockDeleteSSOConfig).toHaveBeenCalledTimes(1)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('shows the certificate fingerprint truncated, never a full secret', () => {
    renderWith([config()])

    // The list renders `fingerprint.slice(0, 16)`. A fingerprint is not
    // secret, but the truncation is the page's stated contract and the same
    // list is where a token would be mis-rendered if one were added later.
    expect(screen.getByText(/AA:BB:CC:DD:EE:F/)).toBeInTheDocument()
  })
})

describe('the settings page template (UX redesign P5)', () => {
  it('replaces the page-local h1 with the compact header and the route help topic', () => {
    renderWith([])
    expectTemplateHeader('SSO & Identity Management', '/settings/sso')
    // The tabs and the config tab's heading are unchanged.
    expect(screen.getByRole('button', { name: 'SSO Configuration' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'SSO Configurations' })).toBeInTheDocument()
  })
})

describe('the questions are asked in the page, one at a time (W27b S2, S3)', () => {
  const TOKEN = {
    id: 'tok-1', name: 'Okta provisioning', token_hint: 'scim_ab12…', is_active: true,
    last_used_at: null, created_at: '2026-08-01T00:00:00Z',
  }

  it('a SCIM token\'s Revoke asks right under its row; the strip sends one DELETE', async () => {
    tabData = { configs: [], scimTokens: [TOKEN], events: [], syncStatus: null }
    render(<SSOSettingsPage />)
    fireEvent.click(screen.getByRole('button', { name: 'SCIM Tokens' }))
    const tokenRow = screen.getByText('Okta provisioning').closest('.justify-between') as HTMLElement

    fireEvent.click(within(tokenRow).getByRole('button', { name: 'Revoke' }))
    expect(revokeSCIMToken).not.toHaveBeenCalled()
    expect(question()).toBe('Revoke this SCIM token?')
    expect(tokenRow.nextElementSibling).toBe((strip() as HTMLElement).closest('[data-in-page-row]'))

    fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Revoke' }))
    expect(strip()).toBeNull()
    await waitFor(() => expect(revokeSCIMToken).toHaveBeenCalledTimes(1))
    expect(revokeSCIMToken).toHaveBeenCalledWith('tok-1')
    await waitFor(() => expect(mockRefresh).toHaveBeenCalled())
    expect(confirm).not.toHaveBeenCalled()
  })

  it('Escape is Cancel for a token too; another tab drops the question', () => {
    tabData = { configs: [], scimTokens: [TOKEN], events: [], syncStatus: null }
    render(<SSOSettingsPage />)
    fireEvent.click(screen.getByRole('button', { name: 'SCIM Tokens' }))
    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }))
    fireEvent.keyDown(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
    expect(strip()).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Revoke' }))
    fireEvent.click(screen.getByRole('button', { name: 'SSO Configuration' }))
    fireEvent.click(screen.getByRole('button', { name: 'SCIM Tokens' }))
    expect(strip()).toBeNull()
    expect(revokeSCIMToken).not.toHaveBeenCalled()
  })

  it('one question per page: Delete after Activate replaces it, and nothing is sent', () => {
    renderWith([config({ enforcement_mode: 'SSO_REQUIRED', is_active: false })])
    fireEvent.click(toggleButton(/^activate$/i))
    expect(question()).toBe(ENFORCEMENT_SENTENCE)
    fireEvent.click(toggleButton(/^delete$/i))
    expect(document.querySelectorAll('[data-inline-confirm]')).toHaveLength(1)
    expect(question()).toBe('Are you sure you want to delete this SSO configuration?')
    expect(mockUpdateSSOConfig).not.toHaveBeenCalled()
    expect(mockDeleteSSOConfig).not.toHaveBeenCalled()
  })
})

describe('OIDC configurations (W20 ADM-08b)', () => {
  const OIDC = {
    provider_type: 'OIDC' as const, display_name: 'Keycloak', idp_entity_id: null, idp_sso_url: null,
    idp_certificate_fingerprint: '', sp_entity_id: null, sp_acs_url: null,
    oidc_issuer: 'https://keycloak.example/realms/testlookup', oidc_client_id: 'testlookup', oidc_scopes: null,
    oidc_groups_claim: 'groups', oidc_client_secret_set: true,
    oidc_redirect_uri: 'https://testlookup.example/api/v1/sso/oidc/callback',
  }

  it('the form asks for the OIDC fields only, and sends only them', async () => {
    vi.mocked(createSSOConfig).mockResolvedValue(config(OIDC))
    renderWith([])
    fireEvent.click(screen.getByRole('button', { name: 'Add SSO Configuration' }))
    fireEvent.change(screen.getByPlaceholderText('IdP Entity ID *'), { target: { value: 'typed-before-switching' } })
    fireEvent.change(screen.getByLabelText('Provider type'), { target: { value: 'OIDC' } })
    expect(screen.queryByPlaceholderText('IdP Certificate (PEM) *')).toBeNull()
    expect(screen.getByPlaceholderText('Client secret *')).toHaveAttribute('type', 'password')
    fireEvent.change(screen.getByPlaceholderText('Display Name *'), { target: { value: 'Keycloak' } })
    fireEvent.change(screen.getByPlaceholderText(/Issuer URL/), { target: { value: OIDC.oidc_issuer } })
    fireEvent.change(screen.getByPlaceholderText('Client ID *'), { target: { value: 'testlookup' } })
    fireEvent.change(screen.getByPlaceholderText('Client secret *'), { target: { value: 's3cret' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create Configuration' }))
    await waitFor(() => expect(createSSOConfig).toHaveBeenCalled())
    const sent = vi.mocked(createSSOConfig).mock.calls[0][0]
    expect(sent).toMatchObject({
      provider_type: 'OIDC', display_name: 'Keycloak', oidc_issuer: OIDC.oidc_issuer,
      oidc_client_id: 'testlookup', oidc_client_secret: 's3cret',
    })
    expect(sent).not.toHaveProperty('idp_entity_id', 'typed-before-switching')
    expect(sent.idp_entity_id).toBeUndefined()
  })

  it('an OIDC card shows the issuer, whether a secret is stored, and the redirect URI — never a secret', () => {
    renderWith([config(OIDC)])
    const card = document.querySelector('[data-oidc-config]') as HTMLElement
    expect(card).toHaveTextContent('OIDC | Issuer: https://keycloak.example/realms/testlookup')
    expect(card).toHaveTextContent('secret (set)')
    expect(card).toHaveTextContent('https://testlookup.example/api/v1/sso/oidc/callback')
    expect(screen.queryByText(/Cert fingerprint/)).toBeNull()
    expect(screen.getByLabelText('Groups claim')).toHaveValue('groups')
  })

  it('Replace secret sends the new secret alone', async () => {
    mockUpdateSSOConfig.mockResolvedValue(config(OIDC))
    renderWith([config(OIDC)])
    const replace = screen.getByRole('button', { name: 'Replace secret' })
    expect(replace).toBeDisabled()
    fireEvent.change(screen.getByLabelText('New client secret for Keycloak'), { target: { value: 'rotated' } })
    fireEvent.click(replace)
    await waitFor(() => expect(mockUpdateSSOConfig).toHaveBeenCalledWith('cfg-1', { oidc_client_secret: 'rotated' }))
    expect(mockRefresh).toHaveBeenCalled()
  })
})

describe('single logout on a SAML card (W20 ADM-08c)', () => {
  it('says on with the IdP SLO URL, off without one', () => {
    renderWith([config({ id: 'a', idp_slo_url: 'https://idp.example/slo' }), config({ id: 'b', display_name: 'Other' })])
    const lines = Array.from(document.querySelectorAll('[data-slo]')).map((n) => n.textContent)
    expect(lines[0]).toContain('Single logout: on')
    expect(lines[0]).toContain('https://idp.example/slo')
    expect(lines[1]).toBe('Single logout: off (no IdP SLO URL)')
  })
})
