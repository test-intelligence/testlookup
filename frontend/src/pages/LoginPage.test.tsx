/**
 * LoginPage — the MFA interstitials.
 *
 * `POST /auth/login` has three possible 200 bodies and the page must route
 * each one correctly. The failure modes worth guarding are the ones that make
 * MFA *feel* broken: a wrong code throwing away a still-valid challenge, and an
 * expired challenge failing silently instead of sending the user back.
 *
 * Service layer is mocked; the page's own state machine and the MFA components
 * are real.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import LoginPage from './LoginPage'
import type {
  LoginResponse,
  MfaEnrollConfirmResponse,
  MfaEnrollStartResponse,
  TokenResponse,
} from '@/types/mfa'

// ── Mocks ────────────────────────────────────────────────────────────────────
const mockLogin = vi.fn<(u: string, p: string) => Promise<LoginResponse>>()
const mockFetchUser = vi.fn()
vi.mock('@/services/authService', () => ({
  loginWithPassword: (u: string, p: string) => mockLogin(u, p),
  fetchCurrentUser: (t?: string) => mockFetchUser(t),
}))

const mockEnrollStart = vi.fn<(t?: string) => Promise<MfaEnrollStartResponse>>()
const mockEnrollConfirm = vi.fn<(c: string, t?: string) => Promise<MfaEnrollConfirmResponse>>()
const mockVerify = vi.fn()
vi.mock('@/hooks/useMfaStatus', () => ({
  startMfaEnrollment: (t?: string) => mockEnrollStart(t),
  confirmMfaEnrollment: (c: string, t?: string) => mockEnrollConfirm(c, t),
  verifyMfaChallenge: (p: unknown) => mockVerify(p),
}))

const mockSetAuth = vi.fn()
vi.mock('@/store/authStore', () => ({
  useAuthStore: (selector: (s: { setAuth: unknown }) => unknown) =>
    selector({ setAuth: mockSetAuth }),
}))

vi.mock('../store/authStore', () => ({
  useAuthStore: (selector: (s: { setAuth: unknown }) => unknown) =>
    selector({ setAuth: mockSetAuth }),
}))

const mockNavigate = vi.fn()
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('../services/ssoService', () => ({
  getSSOStatus: () => Promise.reject(new Error('sso off')),
}))

// The real `api` instance is still imported by LoginPage (dev-login / SSO).
vi.mock('../services/api', () => ({
  api: { post: vi.fn(), get: vi.fn() },
}))

// ── Fixtures ─────────────────────────────────────────────────────────────────
const TOKENS: TokenResponse = {
  access_token: 'acc',
  refresh_token: 'ref',
  token_type: 'bearer',
  expires_in: 3600,
  must_change_password: false,
}

const USER = {
  id: 'u1',
  email: 'a@b.c',
  username: 'alice',
  full_name: 'Alice',
  role: 'ADMIN',
  is_active: true,
  must_change_password: false,
  avatar_color: 'blue',
}

const ENROLL_START: MfaEnrollStartResponse = {
  secret: 'JBSWY3DPEHPK3PXP',
  otpauth_uri: 'otpauth://totp/TestLookup:alice?secret=JBSWY3DPEHPK3PXP&issuer=TestLookup',
  issuer: 'TestLookup',
  account_name: 'alice',
  digits: 6,
  period_seconds: 30,
}

const RECOVERY_CODES = Array.from({ length: 10 }, (_, i) => `ABCD-EFGH-JKLM-NP${i}${i}`)

function httpError(status: number, detail: string, headers: Record<string, string> = {}) {
  return { response: { status, data: { detail }, headers } }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  )
}

/** `fireEvent.change` sets the whole value at once, which also exercises the
 *  paste path through the field's normalizer. */
function setValue(el: HTMLElement, value: string) {
  fireEvent.change(el, { target: { value } })
}

async function submitPassword() {
  setValue(screen.getByLabelText(/email or username/i), 'alice')
  setValue(screen.getByLabelText(/^password$/i), 'hunter22')
  fireEvent.click(screen.getByRole('button', { name: /log in/i }))
}

describe('LoginPage — three-way login response routing', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetchUser.mockResolvedValue(USER)
  })

  it('signs the user straight in on a plain TokenResponse', async () => {
    mockLogin.mockResolvedValue(TOKENS)
    renderPage()
    await submitPassword()

    await waitFor(() => expect(mockSetAuth).toHaveBeenCalledWith('acc', 'ref', USER))
    expect(screen.queryByTestId('mfa-challenge-panel')).toBeNull()
  })

  it('does not resume the forced reset route after the reset session was cleared', async () => {
    mockLogin.mockResolvedValue(TOKENS)
    render(
      <MemoryRouter initialEntries={[{
        pathname: '/login',
        state: { from: { pathname: '/reset-password' } },
      }]}
      >
        <LoginPage />
      </MemoryRouter>,
    )

    await submitPassword()

    await waitFor(() => expect(mockSetAuth).toHaveBeenCalledWith('acc', 'ref', USER))
    expect(mockNavigate).toHaveBeenCalledWith('/overview', { replace: true })
  })

  it('still resumes an ordinary protected deep link after login', async () => {
    mockLogin.mockResolvedValue(TOKENS)
    render(
      <MemoryRouter initialEntries={[{
        pathname: '/login',
        state: { from: { pathname: '/reviews' } },
      }]}
      >
        <LoginPage />
      </MemoryRouter>,
    )

    await submitPassword()

    await waitFor(() => expect(mockSetAuth).toHaveBeenCalledWith('acc', 'ref', USER))
    expect(mockNavigate).toHaveBeenCalledWith('/reviews', { replace: true })
  })

  it('shows the code step on mfa_required', async () => {
    mockLogin.mockResolvedValue({
      mfa_required: true,
      challenge_token: 'chal-1',
      expires_in: 300,
      methods: ['totp', 'recovery_code'],
    })
    renderPage()
    await submitPassword()

    expect(await screen.findByTestId('mfa-challenge-panel')).toBeInTheDocument()
    expect(screen.getByLabelText(/authentication code/i)).toHaveAttribute(
      'autocomplete',
      'one-time-code',
    )
    expect(mockSetAuth).not.toHaveBeenCalled()
  })

  it('starts enrollment on mfa_enrollment_required and says why', async () => {
    mockLogin.mockResolvedValue({
      mfa_enrollment_required: true,
      enrollment_token: 'enr-1',
      expires_in: 300,
      required_for_role: 'QA_LEAD',
    })
    mockEnrollStart.mockResolvedValue(ENROLL_START)
    renderPage()
    await submitPassword()

    expect(await screen.findByText(/this workspace requires two-factor/i)).toBeInTheDocument()
    expect(screen.getByText(/qa lead/i)).toBeInTheDocument()
    expect(mockEnrollStart).toHaveBeenCalledWith('enr-1')
  })
})

describe('LoginPage — challenge step behaviour', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetchUser.mockResolvedValue(USER)
    mockLogin.mockResolvedValue({
      mfa_required: true,
      challenge_token: 'chal-1',
      expires_in: 300,
      methods: ['totp', 'recovery_code'],
    })
  })

  it('completes sign-in with a valid TOTP code', async () => {
    mockVerify.mockResolvedValue(TOKENS)
    renderPage()
    await submitPassword()

    setValue(await screen.findByLabelText(/authentication code/i), '123456')
    fireEvent.click(screen.getByRole('button', { name: /^verify$/i }))

    await waitFor(() =>
      expect(mockVerify).toHaveBeenCalledWith({ challenge_token: 'chal-1', code: '123456' }),
    )
    await waitFor(() => expect(mockSetAuth).toHaveBeenCalledWith('acc', 'ref', USER))
  })

  it('keeps the challenge on a wrong code — error shown, NOT bounced to password', async () => {
    mockVerify.mockRejectedValue(httpError(401, 'Invalid verification code.'))
    renderPage()
    await submitPassword()

    setValue(await screen.findByLabelText(/authentication code/i), '000000')
    fireEvent.click(screen.getByRole('button', { name: /^verify$/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/invalid verification code/i)
    // Still on the challenge step, with the field cleared and ready for retry.
    expect(screen.getByTestId('mfa-challenge-panel')).toBeInTheDocument()
    expect(screen.getByLabelText(/authentication code/i)).toHaveValue('')
    expect(mockSetAuth).not.toHaveBeenCalled()
  })

  it('sends the user back to the password step with a reason when the challenge expired', async () => {
    mockVerify.mockRejectedValue(
      httpError(401, 'Invalid or expired MFA token. Start over from the login screen.'),
    )
    renderPage()
    await submitPassword()

    setValue(await screen.findByLabelText(/authentication code/i), '123456')
    fireEvent.click(screen.getByRole('button', { name: /^verify$/i }))

    // Expired state is explicit, not a silent failure.
    expect(await screen.findByTestId('mfa-challenge-expired')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /enter your password again/i }))

    // Back on the password form, with the reason carried over.
    expect(screen.getByLabelText(/^password$/i)).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent(/timed out|expired/i)
  })

  it('verifies with a recovery code and never sends `code` alongside it', async () => {
    mockVerify.mockResolvedValue(TOKENS)
    renderPage()
    await submitPassword()

    fireEvent.click(await screen.findByRole('button', { name: /use a recovery code instead/i }))
    setValue(screen.getByLabelText(/recovery code/i), 'ABCD-EFGH-JKLM-NPQR')
    fireEvent.click(screen.getByRole('button', { name: /^verify$/i }))

    await waitFor(() =>
      expect(mockVerify).toHaveBeenCalledWith({
        challenge_token: 'chal-1',
        recovery_code: 'ABCD-EFGH-JKLM-NPQR',
      }),
    )
  })

  it('surfaces a 429 lockout with its Retry-After instead of "wrong code"', async () => {
    mockVerify.mockRejectedValue(
      httpError(429, 'Account temporarily locked after repeated failed attempts.', {
        'retry-after': '900',
      }),
    )
    renderPage()
    await submitPassword()

    setValue(await screen.findByLabelText(/authentication code/i), '123456')
    fireEvent.click(screen.getByRole('button', { name: /^verify$/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/temporarily locked/i)
    expect(alert).toHaveTextContent(/15 minutes/i)
  })

  it('says a 503 is our problem, not the user\'s code', async () => {
    mockVerify.mockRejectedValue(httpError(503, 'MFA is temporarily unavailable.'))
    renderPage()
    await submitPassword()

    setValue(await screen.findByLabelText(/authentication code/i), '123456')
    fireEvent.click(screen.getByRole('button', { name: /^verify$/i }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/problem on our side/i)
    expect(alert.textContent ?? '').not.toMatch(/did not match|wrong password/i)
  })
})

describe('LoginPage — register password confirmation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  function openRegister() {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: /^register$/i }))
  }

  it('flags a confirm-password mismatch inline and ties the error to the field', () => {
    openRegister()

    const confirm = screen.getByLabelText(/confirm password/i)
    // No error before the user has typed a confirmation.
    expect(confirm).toHaveAttribute('aria-invalid', 'false')
    expect(screen.queryByRole('alert')).toBeNull()

    setValue(screen.getByLabelText(/^password \*/i), 'hunter22')
    setValue(confirm, 'hunter23')

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent(/passwords do not match/i)
    expect(alert).toHaveAttribute('id', 'reg-confirm-error')
    // The input points assistive tech at the error message.
    expect(confirm).toHaveAttribute('aria-invalid', 'true')
    expect(confirm).toHaveAttribute('aria-describedby', 'reg-confirm-error')
  })

  it('clears the mismatch error once the two passwords agree', () => {
    openRegister()

    const confirm = screen.getByLabelText(/confirm password/i)
    setValue(screen.getByLabelText(/^password \*/i), 'hunter22')
    setValue(confirm, 'hunter23')
    expect(screen.getByRole('alert')).toBeInTheDocument()

    setValue(confirm, 'hunter22')
    expect(screen.queryByRole('alert')).toBeNull()
    expect(confirm).toHaveAttribute('aria-invalid', 'false')
    expect(confirm).not.toHaveAttribute('aria-describedby')
  })
})

describe('LoginPage — forced enrollment', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetchUser.mockResolvedValue(USER)
    mockLogin.mockResolvedValue({
      mfa_enrollment_required: true,
      enrollment_token: 'enr-1',
      expires_in: 300,
      required_for_role: 'ADMIN',
    })
    mockEnrollStart.mockResolvedValue(ENROLL_START)
  })

  it('completes sign-in with the tokens returned by confirm', async () => {
    mockEnrollConfirm.mockResolvedValue({
      enabled: true,
      recovery_codes: RECOVERY_CODES,
      tokens: TOKENS,
    })
    renderPage()
    await submitPassword()

    // Manual-entry secret is always offered next to the QR.
    expect(await screen.findByTestId('mfa-manual-secret')).toHaveTextContent('JBSW Y3DP EHPK 3PXP')

    setValue(screen.getByLabelText(/enter the code your app shows now/i), '123456')
    fireEvent.click(screen.getByRole('button', { name: /verify and enable/i }))

    // Recovery codes gate the completion — no sign-in until acknowledged.
    const panel = await screen.findByTestId('recovery-codes-panel')
    expect(mockSetAuth).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('checkbox', { name: /saved these recovery codes/i }))
    fireEvent.click(screen.getByRole('button', { name: /continue to testlookup/i }))

    await waitFor(() => expect(mockSetAuth).toHaveBeenCalledWith('acc', 'ref', USER))
    expect(panel).not.toBeInTheDocument()
    expect(mockEnrollConfirm).toHaveBeenCalledWith('123456', 'enr-1')
  })

  it('shows all ten recovery codes and blocks dismissal until acknowledged', async () => {
    mockEnrollConfirm.mockResolvedValue({
      enabled: true,
      recovery_codes: RECOVERY_CODES,
      tokens: TOKENS,
    })
    renderPage()
    await submitPassword()

    setValue(await screen.findByLabelText(/enter the code your app shows now/i),
      '123456',
    )
    fireEvent.click(screen.getByRole('button', { name: /verify and enable/i }))

    const list = await screen.findByTestId('recovery-codes-list')
    expect(list.querySelectorAll('li')).toHaveLength(10)
    expect(screen.getByText(/only time these codes will be shown/i)).toBeInTheDocument()
    expect(screen.getByText(/exactly once/i)).toBeInTheDocument()

    const done = screen.getByRole('button', { name: /continue to testlookup/i })
    expect(done).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: /saved these recovery codes/i }))
    expect(done).toBeEnabled()
  })

  it('returns to the password step when the enrollment token has expired', async () => {
    mockEnrollConfirm.mockRejectedValue(
      httpError(401, 'Invalid or expired MFA token. Start over from the login screen.'),
    )
    renderPage()
    await submitPassword()

    setValue(await screen.findByLabelText(/enter the code your app shows now/i),
      '123456',
    )
    fireEvent.click(screen.getByRole('button', { name: /verify and enable/i }))

    await waitFor(() => expect(screen.getByLabelText(/^password$/i)).toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent(/expired/i)
  })
})
