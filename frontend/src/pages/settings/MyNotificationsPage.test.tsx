/**
 * UX redesign P1: My notifications is the signed-in user's own preferences,
 * for every role. The bell used to link to the admin-only Notification
 * Settings page, which redirected a viewer away; the preference and history
 * endpoints were always per-user. The SMTP card stays on the admin page only:
 * its GET is admin, and a viewer must not be offered a server's mail config.
 *
 * P5: the personal page is its own module (`MyNotificationsPage.tsx`), the
 * admin page no longer renders it (`NotificationsPage.test.tsx`), and its
 * channel form is two columns at >= 1280 px.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import MyNotificationsPage from './MyNotificationsPage'
import type { NotificationPreference } from '@/types/notifications'

const settings = vi.hoisted(() => ({
  getSmtpConfig: vi.fn(() => new Promise(() => {})),
  getIntegrationsConfig: vi.fn(() => new Promise(() => {})),
}))
vi.mock('@/services/appSettingsService', () => ({
  appSettingsService: {
    getSmtpConfig: settings.getSmtpConfig,
    getIntegrationsConfig: settings.getIntegrationsConfig,
    updateSmtpConfig: vi.fn(),
    testSmtpConfig: vi.fn(),
  },
}))

const prefs = vi.hoisted(() => ({ data: [] as NotificationPreference[] | undefined, error: undefined as unknown }))
vi.mock('@/hooks/useNotifications', () => ({
  useNotificationPreferences: () => ({ data: prefs.data, error: prefs.error, mutate: vi.fn(), isLoading: false }),
  useNotificationHistory: () => ({ data: [], mutate: vi.fn() }),
  invalidateNotifications: vi.fn(),
}))

const SLACK_PREF: NotificationPreference = {
  id: 'pref-slack',
  user_id: 'user-1',
  channel: 'slack',
  enabled: true,
  events: ['run_failed'],
  failure_rate_threshold: 80,
  project_id: null,
  email_override: null,
  slack_webhook_url: 'https://hooks.slack.com/services/T/B/x',
  teams_webhook_url: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: null,
}

function renderPage() {
  return render(<MemoryRouter><MyNotificationsPage /></MemoryRouter>)
}

describe('MyNotificationsPage', () => {
  it('shows the three channels, and never the SMTP server card, the shared channels or their requests', () => {
    prefs.data = []
    settings.getSmtpConfig.mockClear()
    settings.getIntegrationsConfig.mockClear()
    renderPage()
    expect(screen.getByRole('heading', { name: 'My notifications' })).toBeInTheDocument()
    for (const channel of [/Email/, /Slack/, /Teams/]) expect(screen.getAllByText(channel).length).toBeGreaterThan(0)
    expect(screen.queryByText(/SMTP/)).toBeNull()
    expect(screen.queryByText('Shared channels')).toBeNull()
    expect(settings.getSmtpConfig).not.toHaveBeenCalled()
    expect(settings.getIntegrationsConfig).not.toHaveBeenCalled()
  })

  it('has the compact template header with its help topic', () => {
    prefs.data = []
    const { container } = renderPage()
    const header = container.querySelector('[data-page-header]')
    expect(header).toHaveAttribute('data-compact', 'true')
    expect(header?.querySelector('[data-help-topic]')).toHaveAttribute('data-help-topic', 'administration')
  })

  it('the page is not held to max-w-2xl: an open channel lays its target and threshold out two-up at >= 1280 px', () => {
    prefs.data = [SLACK_PREF]
    const { container } = renderPage()
    expect(container.querySelector('.max-w-2xl')).toBeNull()
    const card = container.querySelector('[data-channel-card="slack"]') as HTMLElement
    const grid = card.querySelector('[data-form-grid]') as HTMLElement
    expect(grid.className).toContain('xl:grid-cols-2')
    // The webhook and the threshold are the grid's two cells; the event list keeps the full width.
    expect(within(grid).getByLabelText('Webhook URL')).toHaveValue('https://hooks.slack.com/services/T/B/x')
    expect(within(grid).getByLabelText(/High failure rate threshold/)).toBeInTheDocument()
    expect(within(grid).queryByText('Notify me when')).toBeNull()
    expect(within(card).getByText('Notify me when')).toBeInTheDocument()
  })

  it('two open channels never share a field id (each label names its own input)', () => {
    prefs.data = [SLACK_PREF]
    const { container } = renderPage()
    // Open Teams beside the already-open Slack card.
    fireEvent.click(within(container.querySelector('[data-channel-card="teams"]') as HTMLElement).getByRole('button', { name: /Microsoft Teams/ }))
    const targets = screen.getAllByLabelText('Webhook URL')
    expect(targets).toHaveLength(2)
    expect(targets[0].id).not.toBe(targets[1].id)
  })

  it('says a blank Slack or Teams webhook goes to the shared channel', () => {
    prefs.data = [SLACK_PREF]
    renderPage()
    expect(screen.getByLabelText('Webhook URL')).toHaveAttribute('placeholder', expect.stringContaining('shared Slack channel'))
  })

  it('a failed read says so, instead of three empty channel cards to fill in again', () => {
    prefs.data = undefined
    prefs.error = Object.assign(new Error('Network Error'), { isAxiosError: true, code: 'ERR_NETWORK' })
    const { container } = renderPage()
    expect(screen.getByTestId('my-notifications-unavailable')).toBeInTheDocument()
    expect(container.querySelector('[data-channel-card]')).toBeNull()
    prefs.error = undefined
    prefs.data = []
  })
})
