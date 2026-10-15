/**
 * EXEC-05: "A test or suite became slower" is one toggle for two events,
 * `test.newly_slow` and `suite.newly_slow` (one message names both). It is
 * off for an existing preference and for a new one (opt-in), saving it on
 * subscribes to both, and saving it off clears both -- including a
 * preference that the API subscribed to only one of the pair.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import MyNotificationsPage from './MyNotificationsPage'
import type { NotificationPreference } from '@/types/notifications'

const svc = vi.hoisted(() => ({
  updatePreference: vi.fn(() => Promise.resolve({})),
  upsertPreference: vi.fn(() => Promise.resolve({})),
}))
vi.mock('@/services/notificationService', () => ({
  notificationService: {
    updatePreference: svc.updatePreference,
    upsertPreference: svc.upsertPreference,
    deletePreference: vi.fn(),
    sendTest: vi.fn(),
  },
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

const prefs = vi.hoisted(() => ({ data: [] as NotificationPreference[] }))
vi.mock('@/hooks/useNotifications', () => ({
  useNotificationPreferences: () => ({ data: prefs.data, error: undefined, mutate: vi.fn(), isLoading: false }),
  useNotificationHistory: () => ({ data: [], mutate: vi.fn() }),
  invalidateNotifications: vi.fn(() => Promise.resolve()),
}))

function slackPref(events: NotificationPreference['events']): NotificationPreference {
  return {
    id: 'pref-slack',
    user_id: 'user-1',
    channel: 'slack',
    enabled: true,
    events,
    failure_rate_threshold: 80,
    project_id: null,
    email_override: null,
    slack_webhook_url: 'https://hooks.slack.com/services/T/B/x',
    teams_webhook_url: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: null,
  }
}

function slackCard(container: HTMLElement) {
  return within(container.querySelector('[data-channel-card="slack"]') as HTMLElement)
}

const SLOWER = 'A test or suite became slower'

describe('MyNotificationsPage: became slower', () => {
  beforeEach(() => {
    svc.updatePreference.mockClear()
    svc.upsertPreference.mockClear()
  })

  it('is one checkbox, off for an existing preference, and saving it on subscribes to both events', async () => {
    prefs.data = [slackPref(['run_failed'])]
    const { container } = render(<MemoryRouter><MyNotificationsPage /></MemoryRouter>)
    const card = slackCard(container)
    expect(card.getAllByLabelText(SLOWER)).toHaveLength(1)
    const box = card.getByLabelText(SLOWER) as HTMLInputElement
    expect(box.checked).toBe(false)

    fireEvent.click(box)
    expect(box.checked).toBe(true)
    fireEvent.click(card.getByRole('button', { name: /Save/ }))
    await waitFor(() => expect(svc.updatePreference).toHaveBeenCalledTimes(1))
    const [, payload] = svc.updatePreference.mock.calls[0] as unknown as [string, { events: string[] }]
    expect(payload.events).toEqual(['run_failed', 'test.newly_slow', 'suite.newly_slow'])
  })

  it('reads on when the preference holds either event, and saving it off clears both', async () => {
    prefs.data = [slackPref(['run_failed', 'suite.newly_slow'])]
    const { container } = render(<MemoryRouter><MyNotificationsPage /></MemoryRouter>)
    const card = slackCard(container)
    const box = card.getByLabelText(SLOWER) as HTMLInputElement
    expect(box.checked).toBe(true)

    fireEvent.click(box)
    fireEvent.click(card.getByRole('button', { name: /Save/ }))
    await waitFor(() => expect(svc.updatePreference).toHaveBeenCalledTimes(1))
    const [, payload] = svc.updatePreference.mock.calls[0] as unknown as [string, { events: string[] }]
    expect(payload.events).toEqual(['run_failed'])
  })

  it('is not in a new preference by default (opt-in)', () => {
    prefs.data = []
    const { container } = render(<MemoryRouter><MyNotificationsPage /></MemoryRouter>)
    fireEvent.click(slackCard(container).getByRole('button', { name: /Slack/ }))
    expect((screen.getByLabelText(SLOWER) as HTMLInputElement).checked).toBe(false)
  })
})
