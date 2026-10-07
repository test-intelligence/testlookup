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
import { MemoryRouter, useLocation } from 'react-router-dom'
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
    render(<DigestsPage />, { wrapper: MemoryRouter })
    const values = [...scheduleSelect().options].map(o => o.value)
    expect(values).toContain('WEEKLY_RETRO')
  })

  it('hides it when the flag is off, because dispatch would skip it silently', () => {
    mockFlags.byKey = { weekly_retro_digest: false }
    render(<DigestsPage />, { wrapper: MemoryRouter })
    const values = [...scheduleSelect().options].map(o => o.value)
    expect(values).not.toContain('WEEKLY_RETRO')
  })

  it('sends WEEKLY_RETRO to the API when chosen', async () => {
    mockFlags.byKey = { weekly_retro_digest: true }
    render(<DigestsPage />, { wrapper: MemoryRouter })

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
    render(<DigestsPage />, { wrapper: MemoryRouter })

    expect(screen.queryByText(/week in review/i)).not.toBeInTheDocument()
    fireEvent.change(scheduleSelect(), { target: { value: 'WEEKLY_RETRO' } })
    expect(screen.getByText(/week in review/i)).toBeInTheDocument()
  })
})

// P2 item 3: the digest workflow timeline is a collapsed "Pipeline"
// disclosure at the bottom of the page; it renders nothing until opened.
describe('DigestsPage workflow timeline', () => {
  it('starts closed below the tabs and opens on click', () => {
    render(<DigestsPage />, { wrapper: MemoryRouter })
    const pipeline = screen.getByRole('button', { name: 'Pipeline' })
    expect(pipeline).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Digest workflow')).toBeNull()
    const tab = screen.getByRole('tab', { name: 'Preview Digest' })
    expect(tab.compareDocumentPosition(pipeline) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(pipeline)
    expect(screen.getByText('Digest workflow')).toBeInTheDocument()
  })
})

// UX redesign P5 (the page template): the tabs are the Tabs primitive bound to
// `?tab=`, under a compact PageHeader with a help topic.
describe('DigestsPage tabs in ?tab=', () => {
  function LocationProbe() {
    const { search } = useLocation()
    return <output data-testid="search">{search}</output>
  }
  function renderAt(url: string) {
    return render(
      <MemoryRouter initialEntries={[url]}>
        <DigestsPage />
        <LocationProbe />
      </MemoryRouter>,
    )
  }

  it('renders the compact template header with a help topic', () => {
    renderAt('/settings/digests')
    expect(document.querySelector('[data-page-header]')).toHaveAttribute('data-compact', 'true')
    expect(screen.getByRole('button', { name: 'Help: Digests & Saved Views' })).toHaveAttribute('data-help-topic', 'administration')
  })

  it('opens Digest Subscriptions by default, in the Tabs primitive', () => {
    renderAt('/settings/digests')
    const tablist = screen.getByRole('tablist', { name: 'Digest sections' })
    expect(tablist).toHaveAttribute('data-tabs')
    expect(screen.getByRole('tab', { name: 'Digest Subscriptions' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByPlaceholderText(/weekly qa summary/i)).toBeInTheDocument()
  })

  it('opens Saved Views from ?tab=saved-views', () => {
    renderAt('/settings/digests?tab=saved-views')
    expect(screen.getByRole('tab', { name: 'Saved Views' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByPlaceholderText('View name')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/weekly qa summary/i)).toBeNull()
  })

  it('opens Preview Digest from ?tab=preview', () => {
    renderAt('/settings/digests?tab=preview')
    expect(screen.getByRole('tab', { name: 'Preview Digest' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'Generate Preview' })).toBeInTheDocument()
  })

  it('writes the chosen tab to the URL and drops it for the default', () => {
    renderAt('/settings/digests')
    fireEvent.click(screen.getByRole('tab', { name: 'Preview Digest' }))
    expect(screen.getByTestId('search').textContent).toBe('?tab=preview')
    fireEvent.click(screen.getByRole('tab', { name: 'Digest Subscriptions' }))
    expect(screen.getByTestId('search').textContent).toBe('')
  })
})
