import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'

const get = vi.fn()
vi.mock('@/services/appSettingsService', () => ({ getSecretProviders: () => get() }))

import SecretProvidersCard from './SecretProvidersCard'

const vault = (over: Record<string, unknown>) => ({ providers: [{ name: 'vault', configured: true, address_host: 'openbao.vault.svc', auth_method: 'approle', healthy: true, message: 'Healthy: logged in and the token is valid.', references: 2, ...over }], reference_syntax: 'vault:<path>#<key>' })

describe('SecretProvidersCard (W20 ADM-10)', () => {
  it('shows a healthy Vault with its host, auth method and reference count', async () => {
    get.mockResolvedValue(vault({}))
    render(<SecretProvidersCard />)
    const row = await screen.findByTestId('secret-provider-vault')
    expect(row).toHaveTextContent('healthy')
    expect(row).toHaveTextContent('openbao.vault.svc · approle auth')
    expect(row).toHaveTextContent('2 secrets reference it')
  })

  it('says unhealthy with the reason, and not configured without a host', async () => {
    get.mockResolvedValue(vault({ healthy: false, message: 'Vault login failed with the configured credentials.' }))
    const { unmount } = render(<SecretProvidersCard />)
    expect(await screen.findByText('unhealthy')).toBeInTheDocument()
    expect(screen.getByText(/Vault login failed/)).toBeInTheDocument()
    unmount()
    get.mockResolvedValue(vault({ configured: false, healthy: false, address_host: null, references: 0, message: 'Not configured: set SECRET_PROVIDER_VAULT_ADDR on the deployment.' }))
    render(<SecretProvidersCard />)
    expect(await screen.findByText('not configured')).toBeInTheDocument()
    expect(screen.queryByText(/auth$/)).toBeNull()
  })

  it("shows the server's refusal", async () => {
    get.mockRejectedValue(Object.assign(new Error('403'), { response: { data: { detail: 'Insufficient permissions' } } }))
    render(<SecretProvidersCard />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Insufficient permissions')
  })
})
