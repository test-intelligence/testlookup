/**
 * My API keys (UX redesign P5, Settings › Account): the signed-in person's own
 * keys, on a page every role that may own one can open. It was a tab of the
 * Users page (QA lead and admin only), while the API lets every QA engineer
 * own keys — so a QA engineer had keys and no screen for them.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const useApiKeysSpy = vi.fn()
const keysState = vi.hoisted(() => ({ error: undefined as unknown, data: [] as unknown[] | undefined }))
const perms = vi.hoisted(() => ({ value: { canGenerateApiKeys: true } }))

vi.mock('@/hooks/useApiKeys', () => ({
  useApiKeys: (...args: unknown[]) => {
    useApiKeysSpy(...args)
    return { data: keysState.data, error: keysState.error, isLoading: false, mutate: vi.fn() }
  },
  refreshApiKeys: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: () => perms.value }))
vi.mock('@/services/userManagementService', () => ({
  userManagementService: { revokeApiKey: vi.fn(), createApiKey: vi.fn() },
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

import PersonalKeysPage from './PersonalKeysPage'

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/settings/my-api-keys']}>
      <PersonalKeysPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  useApiKeysSpy.mockReset()
  perms.value = { canGenerateApiKeys: true }
  keysState.error = undefined
  keysState.data = []
})

describe('PersonalKeysPage — "My API keys"', () => {
  it('has the compact template header, named "My API keys", with the API keys help section', () => {
    renderPage()
    const header = document.querySelector('[data-page-header]') as HTMLElement
    expect(within(header).getByRole('heading', { level: 1, name: 'My API keys' })).toBeInTheDocument()
    expect(header.querySelector('[data-help-topic="administration"][data-help-anchor="api-keys"]')).not.toBeNull()
  })

  it("lists the signed-in user's own keys: the hook is called without a project", () => {
    renderPage()
    expect(useApiKeysSpy).toHaveBeenCalled()
    for (const call of useApiKeysSpy.mock.calls) expect(call[0]).toBeUndefined()
  })

  it("says whose keys they are and points to the project's Streaming API keys", () => {
    renderPage()
    const intro = document.querySelector('[data-my-api-keys-intro]') as HTMLElement
    expect(intro.textContent).toMatch(/API keys you own/)
    expect(intro.textContent).toMatch(/acts as you/)
    expect(within(intro).getByRole('link', { name: 'Streaming API keys' }).getAttribute('href')).toBe('/settings/api-keys')
    expect(screen.getByText(/You have no API keys/)).toBeInTheDocument()
  })

  it('a key\'s revoke button is named and has a tooltip (an unlabeled red bin, browser E2E pass)', () => {
    keysState.data = [{ id: 'k1', name: 'ci-runner', key_prefix: 'qai_abcd', scopes: [], expires_at: null, last_used_at: null }]
    renderPage()
    const revoke = screen.getByRole('button', { name: 'Revoke key ci-runner' })
    expect(revoke).toHaveAttribute('title', 'Revoke key ci-runner')
  })

  it('generates "my" key in a dialog with name | expiry side by side', () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /generate key/i }))
    const dialog = screen.getByRole('dialog', { name: 'Generate my API key' })
    const fields = dialog.querySelector('[data-create-key-fields]') as HTMLElement
    expect(fields.className).toContain('grid-cols-2')
    expect(within(fields).getByLabelText('Key name')).toBeInTheDocument()
    expect(within(fields).getByLabelText('Expiry (days, optional)')).toBeInTheDocument()
  })

  it('a role that cannot own keys is told so, and the list is never asked for (it would be a 403)', () => {
    perms.value = { canGenerateApiKeys: false }
    renderPage()
    expect(screen.getByText('Your role cannot own API keys')).toBeInTheDocument()
    expect(useApiKeysSpy).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /generate key/i })).toBeNull()
  })

  it('a failed list says so, instead of "You have no API keys" (which invites a second key)', () => {
    keysState.data = undefined
    keysState.error = Object.assign(new Error('Network Error'), { isAxiosError: true, code: 'ERR_NETWORK' })
    renderPage()
    expect(screen.getByTestId('my-api-keys-unavailable')).toBeInTheDocument()
    expect(screen.queryByText(/You have no API keys/)).toBeNull()
  })
})
