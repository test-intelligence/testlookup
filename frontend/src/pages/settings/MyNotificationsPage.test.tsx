/**
 * UX redesign P1: My notifications is the signed-in user's own preferences,
 * for every role. The bell used to link to the admin-only Notification
 * Settings page, which redirected a viewer away; the preference and history
 * endpoints were always per-user. The SMTP card stays on the admin page only:
 * its GET is admin, and a viewer must not be offered a server's mail config.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import NotificationsPage, { MyNotificationsPage } from './NotificationsPage'

const smtp = vi.hoisted(() => ({ getSmtpConfig: vi.fn(() => new Promise(() => {})) }))
vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: { getSmtpConfig: smtp.getSmtpConfig, updateSmtpConfig: vi.fn(), testSmtpConfig: vi.fn() },
}))
vi.mock('@/hooks/useNotifications', () => ({
  useNotificationPreferences: () => ({ data: [], mutate: vi.fn(), isLoading: false }),
  useNotificationHistory: () => ({ data: [], mutate: vi.fn() }),
  invalidateNotifications: vi.fn(),
}))

function renderPage(page: React.ReactElement) {
  return render(<MemoryRouter>{page}</MemoryRouter>)
}

describe('MyNotificationsPage', () => {
  it('shows the three channels, and never the SMTP server card or its request', () => {
    smtp.getSmtpConfig.mockClear()
    renderPage(<MyNotificationsPage />)
    expect(screen.getByRole('heading', { name: 'My notifications' })).toBeInTheDocument()
    for (const channel of [/Email/, /Slack/, /Teams/]) expect(screen.getAllByText(channel).length).toBeGreaterThan(0)
    expect(screen.queryByText(/SMTP/)).toBeNull()
    expect(smtp.getSmtpConfig).not.toHaveBeenCalled()
  })

  it('the admin page keeps the SMTP card above the same preferences', () => {
    smtp.getSmtpConfig.mockClear()
    renderPage(<NotificationsPage />)
    expect(screen.getByRole('heading', { name: 'Notification Settings' })).toBeInTheDocument()
    expect(smtp.getSmtpConfig).toHaveBeenCalled()
  })
})
