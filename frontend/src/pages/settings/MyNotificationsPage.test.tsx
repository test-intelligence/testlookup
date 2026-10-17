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
 *
 * W09 NTF-02: each event carries the top bar project's policy hint (§1.3),
 * read once per page and never in All Projects; NTF-01: "Flaky test
 * detected" is not offered, "Quality gate failed" is.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import toast from 'react-hot-toast'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MyNotificationsPage from './MyNotificationsPage'
import { invalidateNotifications } from '@/hooks/useNotifications'
import type { NotificationPreference } from '@/types/notifications'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'
import { useAuthStore } from '@/store/authStore'

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
// W27b: the channel card's Remove (no other case here writes).
const notify = vi.hoisted(() => ({ deletePreference: vi.fn() }))
vi.mock('@/services/notificationService', () => ({
  notificationService: {
    deletePreference: notify.deletePreference,
    updatePreference: vi.fn(),
    upsertPreference: vi.fn(),
    sendTest: vi.fn(),
  },
}))
const policyHook = vi.hoisted(() => ({ policy: null as unknown, calls: [] as (string | null)[] }))
vi.mock('@/hooks/useTransitionPolicy', () => ({
  useTransitionPolicy: (projectId: string | null) => {
    policyHook.calls.push(projectId)
    return { policy: policyHook.policy, error: undefined, isLoading: false, mutate: vi.fn() }
  },
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

beforeEach(() => {
  policyHook.policy = null
  policyHook.calls = []
  useProjectStore.setState({ activeProjectId: ALL_PROJECTS_ID, activeProject: null })
  useAuthStore.setState({ user: { id: 'u1', role: 'VIEWER' } as never })
})

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

// ── NTF-01 / NTF-02 ────────────────────────────────────────────

const P = '00000000-0000-4000-8000-000000000001'

function pref(channel: NotificationPreference['channel'], events: NotificationPreference['events']): NotificationPreference {
  return { ...SLACK_PREF, id: `pref-${channel}`, channel, events, slack_webhook_url: channel === 'slack' ? SLACK_PREF.slack_webhook_url : null }
}

function policy(over: Record<string, unknown> = {}) {
  return {
    project_id: P,
    transitions_enabled: true,
    per_run_events_enabled: false,
    enabled_events: [
      'test.newly_failing', 'test.recovered', 'test.newly_flaky', 'test.quarantined',
      'test.unquarantined', 'test.quarantine_stale', 'test.ready_to_unquarantine',
    ],
    consecutive_failure_threshold: 2,
    is_default: true,
    ...over,
  }
}

const inCheckout = () => useProjectStore.setState({ activeProjectId: P, activeProject: { id: P, name: 'Checkout' } as never })
const cards = (container: HTMLElement) => [...container.querySelectorAll('[data-channel-card]')] as HTMLElement[]
/** The label of one option inside a card (its checkbox is named by the option's own text). */
const optionLabel = (card: HTMLElement, name: string) => within(card).getByRole('checkbox', { name }).closest('label') as HTMLElement

describe('MyNotificationsPage: the events offered (NTF-01)', () => {
  it('offers Quality gate failed and never "Flaky test detected"', () => {
    prefs.data = [SLACK_PREF]
    const { container } = renderPage()
    const card = within(container.querySelector('[data-channel-card="slack"]') as HTMLElement)
    expect(card.getByRole('checkbox', { name: 'Quality gate failed' })).toBeInTheDocument()
    expect(card.getByRole('checkbox', { name: 'Test newly flaky (transition)' })).toBeInTheDocument()
    expect(card.queryByText('Flaky test detected')).toBeNull()
    expect(card.queryByRole('checkbox', { name: 'Flaky test detected' })).toBeNull()
  })

  it('a stored "flaky_test_detected" reads as "Test newly flaky" ticked, counted once', () => {
    prefs.data = [pref('slack', ['flaky_test_detected', 'test.newly_flaky'])]
    const { container } = renderPage()
    const card = container.querySelector('[data-channel-card="slack"]') as HTMLElement
    expect((within(card).getByRole('checkbox', { name: 'Test newly flaky (transition)' }) as HTMLInputElement).checked).toBe(true)
    expect(card).toHaveTextContent('1 event(s)')
  })
})

describe('MyNotificationsPage: the project policy hints (NTF-02)', () => {
  it('per-run off in Checkout: the hint under Run failed in every channel card, and none under ungated events', () => {
    inCheckout()
    policyHook.policy = policy({ per_run_events_enabled: false })
    prefs.data = [pref('email', ['run_failed']), pref('slack', ['run_failed']), pref('teams', ['run_failed'])]
    const { container } = renderPage()
    expect(cards(container)).toHaveLength(3)
    for (const card of cards(container)) {
      expect(within(optionLabel(card, 'Run failed')).getByText('Not sent in Checkout: per-run notifications are off')).toBeInTheDocument()
      expect(within(optionLabel(card, 'Run passed')).getByText(/per-run notifications are off/)).toBeInTheDocument()
      expect(within(optionLabel(card, 'High failure rate')).getByText(/per-run notifications are off/)).toBeInTheDocument()
      for (const free of ['Quality gate failed', 'AI analysis complete', 'A test or suite became slower', 'Test recovered (transition)']) {
        expect(optionLabel(card, free).querySelector('[data-event-hint]'), free).toBeNull()
      }
    }
    // One read for the page, for the top bar project.
    expect(new Set(policyHook.calls)).toEqual(new Set([P]))
  })

  it('per-run on: no per-run hint', () => {
    inCheckout()
    policyHook.policy = policy({ per_run_events_enabled: true })
    prefs.data = [SLACK_PREF]
    const { container } = renderPage()
    expect(container.querySelector('[data-event-hint]')).toBeNull()
    expect(screen.queryByText(/Not sent in/)).toBeNull()
  })

  it('state changes off / one state change off: the matching hint', () => {
    inCheckout()
    policyHook.policy = policy({ per_run_events_enabled: true, transitions_enabled: false })
    prefs.data = [SLACK_PREF]
    const { container, unmount } = renderPage()
    const card = container.querySelector('[data-channel-card="slack"]') as HTMLElement
    expect(within(optionLabel(card, 'Test recovered (transition)')).getByText('Not sent in Checkout: state-change notifications are off')).toBeInTheDocument()
    unmount()

    policyHook.policy = policy({ per_run_events_enabled: true, enabled_events: ['test.newly_failing'] })
    const second = renderPage()
    const card2 = second.container.querySelector('[data-channel-card="slack"]') as HTMLElement
    expect(within(optionLabel(card2, 'Test recovered (transition)')).getByText('Not sent in Checkout: this state change is turned off')).toBeInTheDocument()
    expect(optionLabel(card2, 'Test newly failing (transition)').querySelector('[data-event-hint]')).toBeNull()
  })

  it('All Projects: no policy is asked for (the hook gets null), one muted line, no hint', () => {
    policyHook.policy = policy({ per_run_events_enabled: false })
    prefs.data = [SLACK_PREF]
    const { container } = renderPage()
    expect(policyHook.calls.length).toBeGreaterThan(0)
    expect(policyHook.calls.every(id => id === null)).toBe(true)
    const card = container.querySelector('[data-channel-card="slack"]') as HTMLElement
    expect(within(card).getByText("Run and state-change events depend on each project's notification policy.")).toBeInTheDocument()
    expect(container.querySelector('[data-event-hint]')).toBeNull()
  })

  it('a QA lead sees "Change it in Email (SMTP) & channels" after each hint; a viewer does not', () => {
    inCheckout()
    policyHook.policy = policy({ per_run_events_enabled: false })
    prefs.data = [SLACK_PREF]
    useAuthStore.setState({ user: { id: 'u1', role: 'QA_LEAD' } as never })
    const lead = renderPage()
    const links = within(lead.container).getAllByRole('link', { name: 'Change it in Email (SMTP) & channels' })
    // Run failed, Run passed, High failure rate.
    expect(links).toHaveLength(3)
    for (const link of links) expect(link).toHaveAttribute('href', '/settings/notifications#notification-policy')
    lead.unmount()

    useAuthStore.setState({ user: { id: 'u1', role: 'VIEWER' } as never })
    const viewer = renderPage()
    expect(within(viewer.container).getAllByText(/per-run notifications are off/)).toHaveLength(3)
    expect(within(viewer.container).queryByRole('link', { name: /Change it in Email/ })).toBeNull()
  })

  it.each([
    ['an empty object', {}],
    ['a string', 'per-run off'],
    ['wrong-typed switches', { project_id: P, per_run_events_enabled: 'false', transitions_enabled: 0, enabled_events: [] }],
    ['events not a list', { ...policy(), enabled_events: 'all' }],
  ])('a garbage policy (%s): no hint, and the page renders', (_name, garbage) => {
    inCheckout()
    policyHook.policy = garbage
    prefs.data = [SLACK_PREF]
    const { container } = renderPage()
    expect(screen.getByRole('heading', { name: 'My notifications' })).toBeInTheDocument()
    expect(container.querySelector('[data-channel-card="slack"]')).toBeTruthy()
    expect(container.querySelector('[data-event-hint]')).toBeNull()
    expect(screen.queryByText(/Not sent in/)).toBeNull()
  })

  it('the project name is React text, never markup', () => {
    useProjectStore.setState({ activeProjectId: P, activeProject: { id: P, name: '<img src=x onerror=alert(1)>' } as never })
    policyHook.policy = policy({ per_run_events_enabled: false })
    prefs.data = [SLACK_PREF]
    const { container } = renderPage()
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getAllByText(/Not sent in <img src=x onerror=alert\(1\)>: per-run/).length).toBe(3)
  })
})

/**
 * W27b PAN-06 (Z1): a channel card's Remove asks inside the card, under its
 * actions row, never with the browser's prompt. The card's Remove sends
 * nothing; the strip's Remove is the only sender, once, and still refreshes
 * every notification key (`invalidateNotifications`); Cancel and Escape send
 * nothing.
 */
describe('MyNotificationsPage: Remove asks in the channel card (W27b)', () => {
  const strip = () => document.querySelector('[data-inline-confirm]') as HTMLElement | null

  beforeEach(() => {
    notify.deletePreference.mockReset().mockResolvedValue(undefined)
    vi.mocked(invalidateNotifications).mockClear()
    prefs.data = [SLACK_PREF]
  })

  it('asks under the actions row; the strip sends one DELETE, refreshes, and toasts', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm')
    const success = vi.spyOn(toast, 'success')
    const { container } = renderPage()
    const card = container.querySelector('[data-channel-card="slack"]') as HTMLElement
    const remove = within(card).getByRole('button', { name: 'Remove' })

    fireEvent.click(remove)
    expect(notify.deletePreference).not.toHaveBeenCalled()
    const asked = strip() as HTMLElement
    expect(asked.querySelector('p')?.textContent).toBe('Remove Slack notification preference?')
    expect(card.contains(asked)).toBe(true)
    const actions = remove.parentElement as HTMLElement
    expect(actions.nextElementSibling).toBe(asked)

    fireEvent.click(within(asked).getByRole('button', { name: 'Remove' }))
    expect(strip()).toBeNull()
    await waitFor(() => expect(success).toHaveBeenCalledWith('Slack preference removed'))
    expect(notify.deletePreference).toHaveBeenCalledTimes(1)
    expect(notify.deletePreference).toHaveBeenCalledWith('pref-slack')
    expect(invalidateNotifications).toHaveBeenCalledTimes(1)
    expect(confirmSpy).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
    success.mockRestore()
  })

  it('Cancel and Escape send nothing; collapsing the card drops the question', () => {
    const { container } = renderPage()
    const card = container.querySelector('[data-channel-card="slack"]') as HTMLElement
    const remove = () => within(card).getByRole('button', { name: 'Remove' })

    fireEvent.click(remove())
    fireEvent.click(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }))
    expect(strip()).toBeNull()
    fireEvent.click(remove())
    fireEvent.keyDown(within(strip() as HTMLElement).getByRole('button', { name: 'Cancel' }), { key: 'Escape' })
    expect(strip()).toBeNull()

    fireEvent.click(remove())
    const header = within(card).getByRole('button', { name: /Slack/ })
    fireEvent.click(header)
    fireEvent.click(header)
    expect(strip()).toBeNull()
    expect(notify.deletePreference).not.toHaveBeenCalled()
    expect(invalidateNotifications).not.toHaveBeenCalled()
  })
})
