/**
 * The schedule selector must offer WEEKLY_RETRO — and only when it will work.
 *
 * `WEEKLY_RETRO` existed end-to-end on the backend (ORM enum, beat schedule,
 * retro content renderer) with no way to reach it: the selector offered five
 * schedules and this was not one of them. A feature audit initially filed this
 * as "backend built, UI missing" for five features and was wrong about four —
 * this was the only real gap.
 *
 * The gating half matters as much as the option: `dispatch_scheduled_digests`
 * *silently skips* WEEKLY_RETRO subscriptions when the flag is off, so an
 * ungated option lets a user create a subscription that never delivers and
 * never explains why — the silent-failure class this codebase keeps producing.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DigestsPage from './DigestsPage'

const mockFlags = vi.hoisted(() => ({ byKey: {} as Record<string, boolean> }))
vi.mock('@/hooks/useFeatureFlags', () => ({
  useFeatureEnabled: (key: string) => mockFlags.byKey[key] ?? false,
  useFeatureFlags: () => ({ flags: [], isLoading: false, isError: false, refresh: vi.fn() }),
}))

vi.mock('@/hooks/useDigestData', () => ({
  useDigestSubscriptions: () => ({
    subscriptions: [], isLoading: false, isError: false, mutate: vi.fn(),
  }),
  useDigestSavedViews: () => ({
    views: [], isLoading: false, isError: false, mutate: vi.fn(),
  }),
}))

const createSubscription = vi.hoisted(() => vi.fn().mockResolvedValue({}))
vi.mock('../../services/digestService', () => ({
  createSubscription,
  deleteSubscription: vi.fn(),
  pauseSubscription: vi.fn(),
  resumeSubscription: vi.fn(),
  previewDigest: vi.fn(),
}))
vi.mock('../../services/savedViewsService', () => ({
  createSavedView: vi.fn(), deleteSavedView: vi.fn(),
}))

/** The labels here are bare siblings, not `htmlFor`-bound, so getByLabelText
 *  cannot find the control. PER_RELEASE is present in every flag state. */
const scheduleSelect = () => {
  const el = [...document.querySelectorAll('select')].find(s =>
    [...s.options].some(o => o.value === 'PER_RELEASE'),
  )
  if (!el) throw new Error('schedule select not found')
  return el
}

describe('DigestsPage schedule selector', () => {
  beforeEach(() => {
    mockFlags.byKey = {}
    createSubscription.mockClear()
  })

  it('offers Weekly Retro when the flag is on', () => {
    mockFlags.byKey = { weekly_retro_digest: true }
    render(<DigestsPage />)
    const values = [...scheduleSelect().options].map(o => o.value)
    expect(values).toContain('WEEKLY_RETRO')
  })

  it('hides it when the flag is off, because dispatch would skip it silently', () => {
    mockFlags.byKey = { weekly_retro_digest: false }
    render(<DigestsPage />)
    const values = [...scheduleSelect().options].map(o => o.value)
    expect(values).not.toContain('WEEKLY_RETRO')
  })

  it('sends WEEKLY_RETRO to the API when chosen', async () => {
    mockFlags.byKey = { weekly_retro_digest: true }
    render(<DigestsPage />)

    fireEvent.change(screen.getByPlaceholderText(/weekly qa summary/i), {
      target: { value: 'Team retro' },
    })
    fireEvent.change(scheduleSelect(), { target: { value: 'WEEKLY_RETRO' } })
    fireEvent.click(screen.getByRole('button', { name: /subscribe/i }))

    await waitFor(() =>
      expect(createSubscription).toHaveBeenCalledWith(
        expect.objectContaining({ name: 'Team retro', schedule: 'WEEKLY_RETRO' }),
      ),
    )
  })

  it('explains how a retro differs from a weekly digest', () => {
    mockFlags.byKey = { weekly_retro_digest: true }
    render(<DigestsPage />)

    expect(screen.queryByText(/week in review/i)).not.toBeInTheDocument()
    fireEvent.change(scheduleSelect(), { target: { value: 'WEEKLY_RETRO' } })
    expect(screen.getByText(/week in review/i)).toBeInTheDocument()
  })
})
