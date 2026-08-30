/**
 * Regression: a failed SMTP GET must not leave a savable form full of
 * placeholders.
 *
 * `SmtpConfigCard` initialises its fields to constructor defaults —
 * `localhost`, port 587, `noreply@testlookup.io`, disabled — and then
 * overwrites them from `getSmtpConfig()`. The load `.catch` was empty, with a
 * comment assuming the only possible failure was an insufficient role. Any
 * other failure (backend down, 500, timeout) therefore left those placeholders
 * on screen, presented as the current configuration, with Save fully armed.
 *
 * Save POSTs the whole object. One click during a blip would replace a working
 * production mail server with `localhost:587, disabled` — a destructive write
 * derived entirely from a read that never happened.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import { SmtpConfigCard } from './NotificationsPage'

vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: {
    getSmtpConfig: vi.fn(),
    updateSmtpConfig: vi.fn(),
    testSmtpConfig: vi.fn(),
  },
}))

const CONFIG = {
  enabled: true,
  host: 'smtp.corp.example.com',
  port: 465,
  user: 'mailer@corp.example.com',
  from_address: 'alerts@corp.example.com',
  implicit_tls: true,
  password_set: true,
}

describe('SmtpConfigCard when its config cannot be read', () => {
  beforeEach(() => vi.clearAllMocks())

  it('hides the form and offers no Save when the GET fails', async () => {
    const { appSettingsService } = await import('@/services/appSettingsService')
    ;(appSettingsService.getSmtpConfig as ReturnType<typeof vi.fn>).mockRejectedValue(
      Object.assign(new Error('Network Error'), { code: 'ERR_NETWORK' }),
    )

    render(<SmtpConfigCard />)

    await waitFor(() => expect(screen.getByTestId('smtp-config-unavailable')).toBeInTheDocument())

    // The placeholders that would have been written over the real server.
    expect(screen.queryByDisplayValue('localhost')).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue('noreply@testlookup.io')).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue('587')).not.toBeInTheDocument()
    // No Save button means no way to commit a value we never read.
    expect(screen.queryByRole('button', { name: /^save$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /send test email/i })).not.toBeInTheDocument()
  })

  it('explains that hiding the form is what prevents the overwrite', async () => {
    const { appSettingsService } = await import('@/services/appSettingsService')
    ;(appSettingsService.getSmtpConfig as ReturnType<typeof vi.fn>).mockRejectedValue(
      Object.assign(new Error('boom'), { response: { status: 500, data: {} } }),
    )

    render(<SmtpConfigCard />)

    const notice = await screen.findByTestId('smtp-config-unavailable')
    expect(notice).toHaveTextContent(/server failed to answer/i)
    expect(notice).toHaveTextContent(/overwrite the stored server on save/i)
  })

  it('reports a 403 as a permissions problem, without the overwrite warning', async () => {
    const { appSettingsService } = await import('@/services/appSettingsService')
    ;(appSettingsService.getSmtpConfig as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: { status: 403, data: { detail: 'Admin role required.' } },
    })

    render(<SmtpConfigCard />)

    const notice = await screen.findByTestId('smtp-config-unavailable')
    expect(notice).toHaveTextContent(/not authorized/i)
    expect(notice).toHaveTextContent(/Admin role required/)
    expect(notice).not.toHaveTextContent(/overwrite the stored server/i)
  })

  it('renders the real stored config, editable, when the GET succeeds', async () => {
    const { appSettingsService } = await import('@/services/appSettingsService')
    ;(appSettingsService.getSmtpConfig as ReturnType<typeof vi.fn>).mockResolvedValue(CONFIG)

    render(<SmtpConfigCard />)

    await waitFor(() => expect(screen.getByDisplayValue('smtp.corp.example.com')).toBeInTheDocument())
    expect(screen.getByDisplayValue('465')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^save$/i })).toBeInTheDocument()
    expect(screen.queryByTestId('smtp-config-unavailable')).not.toBeInTheDocument()
  })
})
