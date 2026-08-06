/**
 * Profile → Security. Every state this section can render.
 *
 * The service layer is mocked; the SWR hook is the real one (each test gets a
 * fresh cache) so the read path is genuinely exercised.
 *
 * The two states with teeth:
 *  - `sso_managed` must offer NO controls — an Enable button there is a lie.
 *  - `secret_unreadable` must read as BROKEN, never as "not enrolled". Showing
 *    an enrol prompt to a user in that state sends them into a 409 they cannot
 *    escape.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MfaSecuritySection from './MfaSecuritySection'
import type { MfaStatus } from '@/types/mfa'

const mockStatus = vi.fn<() => Promise<MfaStatus>>()
const mockEnrollStart = vi.fn()
const mockEnrollConfirm = vi.fn()
const mockDisable = vi.fn()
const mockRegenerate = vi.fn()

vi.mock('@/services/mfaService', () => ({
  mfaService: {
    status: () => mockStatus(),
    enrollStart: (t?: string) => mockEnrollStart(t),
    enrollConfirm: (c: string, t?: string) => mockEnrollConfirm(c, t),
    disable: (p: unknown) => mockDisable(p),
    regenerateRecoveryCodes: (p: unknown) => mockRegenerate(p),
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

const BASE: MfaStatus = {
  enabled: false,
  enrolled_at: null,
  recovery_codes_remaining: 0,
  required_by_policy: false,
  sso_managed: false,
  secret_unreadable: false,
}

const ENROLL_START = {
  secret: 'JBSWY3DPEHPK3PXP',
  otpauth_uri: 'otpauth://totp/TestLookup:alice?secret=JBSWY3DPEHPK3PXP',
  issuer: 'TestLookup',
  account_name: 'alice',
  digits: 6,
  period_seconds: 30,
}

const RECOVERY_CODES = Array.from({ length: 10 }, (_, i) => `AAAA-BBBB-CCCC-DD${i}${i}`)

function renderSection() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MfaSecuritySection />
    </SWRConfig>,
  )
}

function httpError(status: number, detail: string) {
  return { response: { status, data: { detail }, headers: {} } }
}

describe('MfaSecuritySection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('offers no controls at all when MFA is managed by the identity provider', async () => {
    mockStatus.mockResolvedValue({ ...BASE, sso_managed: true, enabled: true })
    renderSection()

    expect(await screen.findByTestId('mfa-sso-managed')).toBeInTheDocument()
    expect(screen.getByText(/identity provider/i)).toBeInTheDocument()
    expect(screen.queryAllByRole('button')).toHaveLength(0)
    expect(screen.queryByTestId('mfa-not-enrolled')).toBeNull()
  })

  it('renders a loud broken state for secret_unreadable — NOT "not enrolled"', async () => {
    mockStatus.mockResolvedValue({
      ...BASE,
      enabled: true,
      secret_unreadable: true,
      recovery_codes_remaining: 4,
    })
    renderSection()

    const broken = await screen.findByTestId('mfa-secret-unreadable')
    expect(broken).toHaveTextContent(/enabled but broken/i)
    expect(broken).toHaveTextContent(/contact an administrator/i)

    // Must not be mistaken for the not-enrolled state.
    expect(screen.queryByTestId('mfa-not-enrolled')).toBeNull()
    expect(screen.queryByRole('button', { name: /enable two-factor/i })).toBeNull()
  })

  it('shows Enable when not enrolled, and warns when policy already requires MFA', async () => {
    mockStatus.mockResolvedValue({ ...BASE, required_by_policy: true })
    renderSection()

    expect(await screen.findByTestId('mfa-not-enrolled')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /enable two-factor/i })).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(/requires two-factor/i)
  })

  it('walks voluntary enrollment and requires acknowledging the recovery codes', async () => {
    mockStatus.mockResolvedValue({ ...BASE })
    mockEnrollStart.mockResolvedValue(ENROLL_START)
    // Voluntary path: `tokens` is null — the user keeps their session.
    mockEnrollConfirm.mockResolvedValue({
      enabled: true,
      recovery_codes: RECOVERY_CODES,
      tokens: null,
    })
    renderSection()

    fireEvent.click(await screen.findByRole('button', { name: /enable two-factor/i }))

    // Manual-entry key sits beside the QR, always.
    expect(await screen.findByTestId('mfa-manual-secret')).toHaveTextContent(
      'JBSW Y3DP EHPK 3PXP',
    )

    fireEvent.change(screen.getByLabelText(/enter the code your app shows now/i), {
      target: { value: '123456' },
    })
    fireEvent.click(screen.getByRole('button', { name: /verify and enable/i }))

    await screen.findByTestId('recovery-codes-panel')
    expect(mockEnrollConfirm).toHaveBeenCalledWith('123456', undefined)

    const finish = screen.getByRole('button', { name: /finish/i })
    expect(finish).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: /saved these recovery codes/i }))
    expect(finish).toBeEnabled()
  })

  it('shows enrolled state with remaining codes, Regenerate and Disable', async () => {
    mockStatus.mockResolvedValue({
      ...BASE,
      enabled: true,
      enrolled_at: '2026-08-01T10:00:00+00:00',
      recovery_codes_remaining: 7,
    })
    renderSection()

    expect(await screen.findByTestId('mfa-enrolled')).toHaveTextContent(
      /7.*of 10 recovery codes remaining/i,
    )
    expect(screen.getByRole('button', { name: /regenerate recovery codes/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /turn off two-factor/i })).toBeInTheDocument()
  })

  it('requires a password and a second factor to disable', async () => {
    mockStatus.mockResolvedValue({ ...BASE, enabled: true, recovery_codes_remaining: 9 })
    mockDisable.mockResolvedValue(undefined)
    renderSection()

    fireEvent.click(await screen.findByRole('button', { name: /turn off two-factor/i }))
    const form = screen.getByTestId('mfa-disable-form')
    const submit = screen.getByRole('button', { name: /turn off mfa/i })

    // Nothing filled in → cannot submit.
    expect(submit).toBeDisabled()

    // Password alone is not enough.
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'hunter22' } })
    expect(submit).toBeDisabled()

    fireEvent.change(screen.getByLabelText(/authentication code/i), {
      target: { value: '123456' },
    })
    expect(submit).toBeEnabled()

    fireEvent.submit(form)
    await waitFor(() =>
      expect(mockDisable).toHaveBeenCalledWith({ password: 'hunter22', code: '123456' }),
    )
  })

  it('surfaces the policy-forbids-disable 403 verbatim instead of a generic failure', async () => {
    mockStatus.mockResolvedValue({
      ...BASE,
      enabled: true,
      required_by_policy: true,
      recovery_codes_remaining: 9,
    })
    mockDisable.mockRejectedValue(
      httpError(403, 'Workspace policy requires MFA for your role, so it cannot be disabled.'),
    )
    renderSection()

    fireEvent.click(await screen.findByRole('button', { name: /turn off two-factor/i }))
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'hunter22' } })
    fireEvent.change(screen.getByLabelText(/authentication code/i), {
      target: { value: '123456' },
    })
    fireEvent.submit(screen.getByTestId('mfa-disable-form'))

    expect(await screen.findByRole('alert')).toHaveTextContent(/workspace policy requires mfa/i)
  })

  it('warns that regenerating invalidates the old set, and gates dismissal', async () => {
    mockStatus.mockResolvedValue({ ...BASE, enabled: true, recovery_codes_remaining: 2 })
    mockRegenerate.mockResolvedValue({ recovery_codes: RECOVERY_CODES })
    renderSection()

    fireEvent.click(await screen.findByRole('button', { name: /regenerate recovery codes/i }))
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'hunter22' } })
    fireEvent.change(screen.getByLabelText(/authentication code/i), {
      target: { value: '123456' },
    })
    fireEvent.submit(screen.getByTestId('mfa-regenerate-form'))

    const panel = await screen.findByTestId('recovery-codes-panel')
    expect(panel).toHaveTextContent(/previous recovery codes have just been invalidated/i)
    expect(screen.getByTestId('recovery-codes-list').querySelectorAll('li')).toHaveLength(10)

    const done = screen.getByRole('button', { name: /^done$/i })
    expect(done).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: /saved these recovery codes/i }))
    expect(done).toBeEnabled()
  })

  it('never says "wrong password" when the backend answers 503', async () => {
    mockStatus.mockResolvedValue({ ...BASE, enabled: true, recovery_codes_remaining: 9 })
    mockDisable.mockRejectedValue(httpError(503, 'MFA is temporarily unavailable.'))
    renderSection()

    fireEvent.click(await screen.findByRole('button', { name: /turn off two-factor/i }))
    fireEvent.change(screen.getByLabelText(/^password$/i), { target: { value: 'hunter22' } })
    fireEvent.change(screen.getByLabelText(/authentication code/i), {
      target: { value: '123456' },
    })
    fireEvent.submit(screen.getByTestId('mfa-disable-form'))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/problem on our side/i)
    expect(alert.textContent ?? '').not.toMatch(/password is incorrect/i)
  })

  it('refreshes status when enrolment reports 409 already-enrolled', async () => {
    mockStatus.mockResolvedValue({ ...BASE })
    mockEnrollStart.mockRejectedValue(httpError(409, 'MFA is already enabled.'))
    renderSection()

    fireEvent.click(await screen.findByRole('button', { name: /enable two-factor/i }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/already enabled/i))
    // The status read is re-issued so the section can correct itself.
    await waitFor(() => expect(mockStatus.mock.calls.length).toBeGreaterThan(1))
  })
})
