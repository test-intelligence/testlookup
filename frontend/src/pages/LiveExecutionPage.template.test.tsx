/**
 * UX redesign P6 on /live: the page template's header and the shared window
 * control.
 *
 * - The page-made heading (title, LIVE / project / suite chips, a subtitle)
 *   is the template's `PageHeader`: one compact h1, the route's help topic,
 *   the chips folded into the one-line subtitle; the toolbar and the stream's
 *   status (LIVE, auto-refresh, last update, socket) share its row.
 * - The completed-sessions window was a hand-rolled tab list bound to the
 *   global time-window store. A window is not a tab (`?tab=` would cut it off
 *   from the store every other page reads), so it is the shared
 *   `WindowPicker`: the same four windows, the same store, the same fetch.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const live = vi.hoisted(() => ({ useLiveExecution: vi.fn() }))
vi.mock('@/hooks/useLiveExecution', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/hooks/useLiveExecution')>()
  return { ...actual, useLiveExecution: live.useLiveExecution }
})
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: vi.fn(() => ({ options: ['cart', 'payments'], isLoading: false })) }))

import LiveExecutionPage from './LiveExecutionPage'
import { useProjectStore } from '@/store/projectStore'
import { useTimeWindowStore } from '@/store/timeWindowStore'
import { expectTemplateHeader } from '@/test/expectTemplateHeader'

const PROJECT = 'aaaaaaaa-0000-4000-8000-000000000001'

/** The completed-sessions window (days) of the latest `useLiveExecution` call. */
const lastDaysArg = () => {
  const calls = live.useLiveExecution.mock.calls as unknown as unknown[][]
  return calls[calls.length - 1][2]
}

const header = () => document.querySelector('[data-page-header]') as HTMLElement

function stream(sessions: Record<string, unknown>[] = []) {
  live.useLiveExecution.mockReturnValue({
    sessions,
    activeSessions: [],
    completedSessions: [],
    recentEvents: [],
    wsStatus: 'open',
    isLoading: false,
  })
}

function renderPage() {
  return render(<MemoryRouter><LiveExecutionPage /></MemoryRouter>)
}

beforeEach(() => {
  localStorage.clear()
  live.useLiveExecution.mockReset()
  stream()
  useProjectStore.setState({ activeProjectId: PROJECT, activeProject: { id: PROJECT, name: 'Checkout' } as never })
  useTimeWindowStore.setState({ days: 7 })
})

describe('LiveExecutionPage — the template header', () => {
  it('renders one compact h1 with the route\'s help topic, the old chips as its one-line subtitle', () => {
    renderPage()
    expectTemplateHeader('Live Execution', '/live')
    expect(header()).toHaveTextContent('Real-time test execution stream — Checkout')
    // The project chip beside the title is gone: its name is in the subtitle, once.
    expect(within(header()).getAllByText(/Checkout/)).toHaveLength(1)
  })

  it('names a picked suite in the subtitle (it was a chip beside the title)', () => {
    renderPage()
    fireEvent.change(within(header()).getByRole('combobox', { name: 'Test suite' }), { target: { value: 'cart' } })
    expect(header()).toHaveTextContent('Real-time test execution stream — Checkout · Suite cart')
  })

  it('keeps the toolbar and the stream status in the header row: window, suite, LIVE, auto-refresh, last update, socket', () => {
    renderPage()
    const toolbar = header().querySelector('[data-page-toolbar]') as HTMLElement
    expect(within(toolbar).getByRole('radiogroup', { name: 'Time window' })).toBeInTheDocument()
    expect(within(toolbar).getByRole('combobox', { name: 'Test suite' })).toBeInTheDocument()
    expect(toolbar.querySelector('[data-live-badge]')).toHaveTextContent('LIVE')
    expect(toolbar).toHaveTextContent('Auto-refresh on')
    expect(toolbar).toHaveTextContent('Last update')
    expect(within(toolbar).getByText('Live')).toBeInTheDocument() // the open socket
  })

  it('the LIVE badge pulses only while a run is actively streaming', () => {
    const { unmount } = renderPage()
    expect(header().querySelector('[data-live-badge]')).toHaveAttribute('data-live-badge', 'idle')
    unmount()
    stream([{
      run_id: 'r1', project_id: PROJECT, build_number: '41', status: 'running',
      total: 10, passed: 9, failed: 1, skipped: 0, broken: 0, pass_rate: 90,
      started_at: new Date(Date.now() - 30_000).toISOString(),
      last_event_at: new Date(Date.now() - 5_000).toISOString(),
    }])
    renderPage()
    expect(header().querySelector('[data-live-badge]')).toHaveAttribute('data-live-badge', 'live')
  })
})

describe('LiveExecutionPage — the completed-sessions window is the shared WindowPicker', () => {
  it('renders no hand-rolled tab list: the window is a radio group of 24h, 7d, 14d, 30d', () => {
    const { container } = renderPage()
    expect(container.querySelector('[role="tablist"]')).toBeNull()
    const picker = screen.getByRole('radiogroup', { name: 'Time window' })
    expect(picker).toHaveAttribute('data-window-picker')
    expect(within(picker).getAllByRole('radio').map(r => r.textContent)).toEqual(['24h', '7d', '14d', '30d'])
    expect(within(picker).getByRole('radio', { name: '7d' })).toHaveAttribute('aria-checked', 'true')
    // Its scope, as the tooltip the old aria-label carried.
    expect(picker.parentElement).toHaveAttribute('title', 'Completed sessions window — active runs always show')
    expect(lastDaysArg()).toBe(7)
  })

  it('picking a window writes the global store and refetches with it', () => {
    renderPage()
    fireEvent.click(screen.getByRole('radio', { name: '24h' }))
    expect(useTimeWindowStore.getState().days).toBe(1)
    expect(screen.getByRole('radio', { name: '24h' })).toHaveAttribute('aria-checked', 'true')
    expect(lastDaysArg()).toBe(1)
  })

  it('a stored window Live does not offer (90d) reads as 30d — the first fetch included', () => {
    useTimeWindowStore.setState({ days: 90 })
    renderPage()
    expect(screen.getByRole('radio', { name: '30d' })).toHaveAttribute('aria-checked', 'true')
    expect(live.useLiveExecution.mock.calls.every(c => (c as unknown[])[2] === 30)).toBe(true)
  })
})
