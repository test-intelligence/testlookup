/**
 * MfaPolicyPage — the ADMIN-only workspace policy.
 *
 * Guards: the in-component role gate, and the confirmation that must appear
 * before `require_mfa` is switched on. Turning that flag on forces everyone in
 * scope to enrol at their next login and can lock people out permanently, so
 * "save silently" is not an acceptable behaviour.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MfaPolicyPage from './MfaPolicyPage'
import type { MfaPolicy } from '@/types/mfa'

const mockGetPolicy = vi.fn<() => Promise<MfaPolicy>>()
const mockUpdatePolicy = vi.fn()
vi.mock('@/services/mfaService', () => ({
  mfaService: {
    getPolicy: () => mockGetPolicy(),
    updatePolicy: (p: unknown) => mockUpdatePolicy(p),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

const permissions = { isAdmin: true }
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => permissions,
}))

/** The live defaults on the running cluster today. */
const LIVE_DEFAULTS: MfaPolicy = {
  require_mfa: false,
  required_for_role: null,
  lockout_enabled: true,
  lockout_threshold: 10,
  lockout_duration_minutes: 15,
}

function renderPage() {
  return render(
    <MemoryRouter>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <MfaPolicyPage />
      </SWRConfig>
    </MemoryRouter>,
  )
}

describe('MfaPolicyPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permissions.isAdmin = true
    mockGetPolicy.mockResolvedValue({ ...LIVE_DEFAULTS })
  })

  it('shows an access-denied empty state and no controls for non-admins', () => {
    permissions.isAdmin = false
    renderPage()

    expect(screen.getByText(/admin access required/i)).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: /require mfa/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /save policy/i })).toBeNull()
  })

  it('loads the live policy into the form', async () => {
    renderPage()

    await waitFor(() =>
      expect(screen.getByLabelText(/failed attempts before lockout/i)).toHaveValue(10),
    )
    expect(screen.getByLabelText(/lockout duration/i)).toHaveValue(15)
    expect(screen.getByLabelText(/lock accounts after repeated failures/i)).toBeChecked()
    expect(screen.getByLabelText(/require mfa to sign in/i)).not.toBeChecked()
  })

  it('demands confirmation — stating the consequences — before enabling require_mfa', async () => {
    renderPage()
    await screen.findByLabelText(/require mfa to sign in/i)

    fireEvent.click(screen.getByLabelText(/require mfa to sign in/i))
    fireEvent.click(screen.getByRole('button', { name: /save policy/i }))

    // Nothing is written until the admin confirms.
    expect(mockUpdatePolicy).not.toHaveBeenCalled()

    const confirm = await screen.findByTestId('mfa-enable-confirm')
    expect(confirm).toHaveTextContent(/forced to enrol the next time they sign in/i)
    expect(confirm).toHaveTextContent(/identity provider are.*exempt/i)
    expect(confirm).toHaveTextContent(/breakglass reset script/i)
    expect(confirm).toHaveTextContent(/every user in this workspace/i)
  })

  it('cancels cleanly out of the confirmation without writing', async () => {
    renderPage()
    await screen.findByLabelText(/require mfa to sign in/i)

    fireEvent.click(screen.getByLabelText(/require mfa to sign in/i))
    fireEvent.click(screen.getByRole('button', { name: /save policy/i }))
    fireEvent.click(await screen.findByRole('button', { name: /^cancel$/i }))

    expect(screen.queryByTestId('mfa-enable-confirm')).toBeNull()
    expect(mockUpdatePolicy).not.toHaveBeenCalled()
  })

  it('sends clear_required_for_role when the scope is Everyone', async () => {
    mockUpdatePolicy.mockResolvedValue({ ...LIVE_DEFAULTS, require_mfa: true })
    renderPage()
    await screen.findByLabelText(/require mfa to sign in/i)

    fireEvent.click(screen.getByLabelText(/require mfa to sign in/i))
    fireEvent.click(screen.getByRole('button', { name: /save policy/i }))
    fireEvent.click(await screen.findByRole('button', { name: /yes, require mfa/i }))

    await waitFor(() =>
      expect(mockUpdatePolicy).toHaveBeenCalledWith({
        require_mfa: true,
        lockout_enabled: true,
        lockout_threshold: 10,
        lockout_duration_minutes: 15,
        clear_required_for_role: true,
      }),
    )
  })

  it('sends the selected role instead of the clear flag, and names it in the confirmation', async () => {
    mockUpdatePolicy.mockResolvedValue({
      ...LIVE_DEFAULTS,
      require_mfa: true,
      required_for_role: 'QA_LEAD',
    })
    renderPage()
    await screen.findByLabelText(/require mfa to sign in/i)

    fireEvent.click(screen.getByLabelText(/require mfa to sign in/i))
    fireEvent.change(screen.getByRole('combobox', { name: /applies to/i }), {
      target: { value: 'QA_LEAD' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save policy/i }))

    expect(await screen.findByTestId('mfa-enable-confirm')).toHaveTextContent(
      /every QA Lead and above/i,
    )

    fireEvent.click(screen.getByRole('button', { name: /yes, require mfa/i }))
    await waitFor(() =>
      expect(mockUpdatePolicy).toHaveBeenCalledWith(
        expect.objectContaining({ required_for_role: 'QA_LEAD' }),
      ),
    )
    expect(mockUpdatePolicy.mock.calls[0][0]).not.toHaveProperty('clear_required_for_role')
  })

  it('saves lockout-only changes without any confirmation', async () => {
    mockUpdatePolicy.mockResolvedValue({ ...LIVE_DEFAULTS, lockout_threshold: 5 })
    renderPage()
    await waitFor(() =>
      expect(screen.getByLabelText(/failed attempts before lockout/i)).toHaveValue(10),
    )

    fireEvent.change(screen.getByLabelText(/failed attempts before lockout/i), {
      target: { value: '5' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save policy/i }))

    expect(screen.queryByTestId('mfa-enable-confirm')).toBeNull()
    await waitFor(() =>
      expect(mockUpdatePolicy).toHaveBeenCalledWith(
        expect.objectContaining({ lockout_threshold: 5, require_mfa: false }),
      ),
    )
  })

  it('refuses an out-of-range lockout threshold client-side', async () => {
    renderPage()
    await waitFor(() =>
      expect(screen.getByLabelText(/failed attempts before lockout/i)).toHaveValue(10),
    )

    fireEvent.change(screen.getByLabelText(/failed attempts before lockout/i), {
      target: { value: '1' },
    })
    fireEvent.click(screen.getByRole('button', { name: /save policy/i }))

    await waitFor(() => expect(mockUpdatePolicy).not.toHaveBeenCalled())
  })
})
