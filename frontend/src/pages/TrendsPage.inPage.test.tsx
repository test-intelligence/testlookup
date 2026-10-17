/**
 * W27b PAN-05b: on /trends the note form and the email form open IN THE PAGE,
 * where their action lives, rendered by the REAL `PinNoteDialog` and
 * `EmailReportDialog` (TrendsPage.test.tsx mocks both and records their props;
 * this file proves where they land):
 *
 *  - the cadence card's `Pin a note` (and a drag on its strip) → the note form
 *    right after the cadence card; the run-gap callout's `Pin a note for these
 *    days` → right after the callout; the hero's Notes › Edit → right after
 *    the hero chart;
 *  - one note form at a time: another place moves it there; typed text is asked
 *    about first (`Discard your changes to "Pin a note"?`);
 *  - More actions › Email report… → the email form right after the page header;
 *  - none of them is a dialog: no `[aria-modal]`, no `role="dialog"` (the
 *    Notes chip's anchored popover is OUT and is closed by Edit).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TrendsPage from './TrendsPage'

// A section behind a SectionErrorBoundary: the real reporter batches for 5 s
// and would post inside a later test (W08 gate).
vi.mock('@/utils/errorReporting', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/utils/errorReporting')>(),
  reportBoundaryError: vi.fn(),
}))
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => ({ data: { items: [] } }) }))
vi.mock('@/components/reports/catalogue/TrendsCatalogue', () => ({
  default: () => <div data-testid="trends-catalogue" />,
}))
vi.mock('@/hooks/useMetrics', () => ({ useTrendData: vi.fn(), useDashboardSummary: vi.fn(), useCoverage: vi.fn() }))
vi.mock('@/hooks/useRuns', () => ({ useRuns: vi.fn() }))
vi.mock('@/hooks/useSuiteOptions', () => ({ useSuiteOptions: vi.fn(() => ({ options: ['Checkout'], isLoading: false })) }))
vi.mock('@/hooks/useAnalyticsView', () => ({ useAnalyticsView: () => ({ widgetIds: [], setWidgets: vi.fn(), save: vi.fn() }) }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))
vi.mock('@/hooks/useTrendSummary', async () => {
  const metrics = await import('@/hooks/useMetrics')
  const { summaryFromTrend } = await import('@/test/trendSummaryFixture')
  const last = (hook: unknown) => {
    const results = (hook as ReturnType<typeof vi.fn>).mock.results
    return results.length ? (results[results.length - 1].value as { data?: { data?: unknown[]; suites?: unknown[] } } | undefined) : undefined
  }
  return {
    useTrendSummary: (days: number) => {
      const trend = (last(metrics.useTrendData)?.data?.data ?? []) as Parameters<typeof summaryFromTrend>[0]
      const suites = (last(metrics.useCoverage)?.data?.suites ?? []) as Parameters<typeof summaryFromTrend>[2]
      return { summary: summaryFromTrend(trend, days, suites), isLoading: false, error: null }
    },
  }
})
vi.mock('@/hooks/useSuiteSparklines', () => ({
  useSuiteSparklines: () => ({ lineOf: () => undefined, state: 'ready' }),
}))
const timeline = vi.hoisted(() => ({ notes: [] as unknown[] }))
const noteActions = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn(), remove: vi.fn() }))
vi.mock('@/hooks/useTimelineNotes', () => ({
  useTimelineNotes: () => ({ notes: timeline.notes, total: 0, truncated: false, isLoading: false, unavailable: false, mutate: async () => undefined }),
  useTimelineNoteActions: () => ({ projectId: 'proj-1', ...noteActions }),
}))
const role = vi.hoisted(() => ({ isQaLead: false, isQaEngineer: true, isAdmin: false }))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ role: 'QA_ENGINEER', ...role, hasRole: () => false }),
}))
vi.mock('@/hooks/useEmailAvailability', () => ({
  EMAIL_AVAILABILITY_KEY: 'email-trends-availability',
  useEmailAvailability: vi.fn(() => ({ availability: { available: true }, isLoading: false, error: undefined })),
}))
vi.mock('@/hooks/useUserManagement', () => ({
  useProjectMembers: vi.fn(() => ({ data: [], isLoading: false, error: undefined })),
}))
vi.mock('react-hot-toast', () => ({ default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }) }))

async function dayBack(back: number) {
  const { shiftDayIso, utcDayIso } = await import('@/utils/calendarDay')
  return shiftDayIso(utcDayIso(), -back)
}

async function renderPage(backs: number[] = [10, 11, 12, 13]) {
  const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
  const { useRuns } = await import('@/hooks/useRuns')
  const trend = await Promise.all(
    backs.map(async (back) => ({ date: await dayBack(back), passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 })),
  )
  vi.mocked(useTrendData).mockReturnValue({ data: { data: trend }, isLoading: false } as never)
  vi.mocked(useDashboardSummary).mockReturnValue({ data: {} } as never)
  vi.mocked(useCoverage).mockReturnValue({ data: { summary: {}, suites: [] } } as never)
  vi.mocked(useRuns).mockReturnValue({ data: { items: [] }, isLoading: false } as never)
  const { useTimeWindowStore } = await import('@/store/timeWindowStore')
  useTimeWindowStore.setState({ days: 14 })
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={['/trends']}>
        <Routes><Route path="/trends" element={<TrendsPage />} /></Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
  await screen.findByRole('heading', { name: 'Trends' })
}

const noteForm = (name: 'Pin a note' | 'Edit note') => screen.getByRole('region', { name })
/** The page-level block (a direct child of the column the form opens in) that holds `el`. */
const blockOf = (el: Element, column: Element) => {
  let node: Element | null = el
  while (node && node.parentElement !== column) node = node.parentElement
  return node as Element
}
const noModal = () => {
  expect(document.querySelector('[aria-modal]')).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
}
/** A browser focuses a button it clicks; jsdom's click does not. */
const press = (button: HTMLElement) => {
  button.focus()
  fireEvent.click(button)
}

beforeEach(() => {
  timeline.notes = []
  role.isQaLead = false
  role.isQaEngineer = true
  for (const fn of Object.values(noteActions)) fn.mockReset()
})

describe('TrendsPage — the note and email forms in the page (W27b)', () => {
  it('the cadence card\'s Pin a note renders the form right after the cadence card (not at the page end)', async () => {
    await renderPage()
    const pin = document.querySelector('[data-pin-note-open="cadence"]') as HTMLElement
    press(pin)
    const form = noteForm('Pin a note')
    noModal()
    const column = (document.querySelector('[data-tab-panel="volume"]') as HTMLElement).firstElementChild as HTMLElement
    const cadence = blockOf(pin, column)
    expect(cadence.nextElementSibling).toBe(form)
    expect(form).toHaveAttribute('data-pin-note-dialog', 'create')
    expect(form.querySelector('[data-pin-note="text"]')).toHaveFocus()
  })

  it('a drag on the cadence strip opens the form after the cadence card on those days', async () => {
    await renderPage()
    const cells = [...document.querySelectorAll<HTMLElement>('[data-day-cell]')]
    fireEvent.mouseDown(cells[3], { button: 0 })
    fireEvent.mouseEnter(cells[5])
    fireEvent.mouseUp(cells[5])
    const form = noteForm('Pin a note')
    const pin = document.querySelector('[data-pin-note-open="cadence"]') as HTMLElement
    const column = (document.querySelector('[data-tab-panel="volume"]') as HTMLElement).firstElementChild as HTMLElement
    expect(blockOf(pin, column).nextElementSibling).toBe(form)
    expect((form.querySelector('[data-pin-note="from"]') as HTMLInputElement).value).toBe(await dayBack(10))
    expect((form.querySelector('[data-pin-note="to"]') as HTMLInputElement).value).toBe(await dayBack(8))
  })

  it('the gap callout\'s Pin a note for these days renders the form right after the callout; it MOVES there from the cadence card', async () => {
    await renderPage()
    press(document.querySelector('[data-pin-note-open="cadence"]') as HTMLElement)
    expect(screen.getAllByRole('region', { name: 'Pin a note' })).toHaveLength(1)

    const gapPin = screen.getByRole('button', { name: 'Pin a note for these days' })
    press(gapPin)
    const forms = screen.getAllByRole('region', { name: 'Pin a note' })
    expect(forms, 'one note form at a time').toHaveLength(1)
    const callout = gapPin.closest('section') as HTMLElement
    expect(callout.nextElementSibling).toBe(forms[0])
    expect((forms[0].querySelector('[data-pin-note="from"]') as HTMLInputElement).value).toBe(await dayBack(9))
    expect((forms[0].querySelector('[data-pin-note="to"]') as HTMLInputElement).value).toBe(await dayBack(0))
    noModal()
  })

  it('typed text asks before another place takes the form; Keep editing keeps it, Discard changes moves it', async () => {
    await renderPage()
    press(document.querySelector('[data-pin-note-open="cadence"]') as HTMLElement)
    fireEvent.input(noteForm('Pin a note').querySelector('[data-pin-note="text"]') as HTMLElement, { target: { value: 'half typed' } })
    fireEvent.change(noteForm('Pin a note').querySelector('[data-pin-note="text"]') as HTMLElement, { target: { value: 'half typed' } })

    press(screen.getByRole('button', { name: 'Pin a note for these days' }))
    const form = noteForm('Pin a note')
    expect(within(form).getByText(/Discard your changes to/)).toHaveTextContent('Discard your changes to "Pin a note"?')
    fireEvent.click(within(form).getByRole('button', { name: 'Keep editing' }))
    expect((noteForm('Pin a note').querySelector('[data-pin-note="text"]') as HTMLTextAreaElement).value).toBe('half typed')
    const pin = document.querySelector('[data-pin-note-open="cadence"]') as HTMLElement
    const column = (document.querySelector('[data-tab-panel="volume"]') as HTMLElement).firstElementChild as HTMLElement
    expect(blockOf(pin, column).nextElementSibling).toBe(noteForm('Pin a note'))

    press(screen.getByRole('button', { name: 'Pin a note for these days' }))
    fireEvent.click(within(noteForm('Pin a note')).getByRole('button', { name: 'Discard changes' }))
    const moved = noteForm('Pin a note')
    expect(screen.getByRole('button', { name: 'Pin a note for these days' }).closest('section')?.nextElementSibling).toBe(moved)
    expect((moved.querySelector('[data-pin-note="text"]') as HTMLTextAreaElement).value).toBe('')
    expect(noteActions.create).not.toHaveBeenCalled()
  })

  it('the hero\'s Notes › Edit renders the edit form right after the hero chart; the chip\'s popover is closed', async () => {
    timeline.notes = [{
      id: 'n1', project_id: 'proj-1', starts_on: await dayBack(5), ends_on: await dayBack(3), text: 'release freeze',
      suite_name: null, created_by_name: 'Ada', can_edit: true, created_at: null, updated_at: null,
    }]
    await renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Notes (1)' }))
    fireEvent.click(document.querySelector('[data-timeline-note-edit="n1"]') as HTMLElement)
    const form = noteForm('Edit note')
    noModal()
    const hero = document.querySelector('[data-catalogue-section="trends-pass-rate"]') as HTMLElement
    expect(hero.nextElementSibling).toBe(form)
    expect(form).toHaveAttribute('data-pin-note-dialog', 'edit')
    expect((form.querySelector('[data-pin-note="text"]') as HTMLTextAreaElement).value).toBe('release freeze')
    expect(screen.queryByRole('region', { name: 'Pin a note' })).toBeNull()
  })

  it('Cancel, the X and Escape close the note form and send nothing', async () => {
    await renderPage()
    const pin = document.querySelector('[data-pin-note-open="cadence"]') as HTMLElement
    press(pin)
    fireEvent.click(within(noteForm('Pin a note')).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('region', { name: 'Pin a note' })).toBeNull()
    press(pin)
    fireEvent.click(within(noteForm('Pin a note')).getByRole('button', { name: 'Close note dialog' }))
    expect(screen.queryByRole('region', { name: 'Pin a note' })).toBeNull()
    press(pin)
    fireEvent.keyDown(noteForm('Pin a note').querySelector('[data-pin-note="text"]') as HTMLElement, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Pin a note' })).toBeNull())
    expect(pin).toHaveFocus()
    expect(noteActions.create).not.toHaveBeenCalled()
  })

  it('another tab takes a cadence form away (asking first when typed)', async () => {
    await renderPage()
    press(document.querySelector('[data-pin-note-open="cadence"]') as HTMLElement)
    const text = noteForm('Pin a note').querySelector('[data-pin-note="text"]') as HTMLElement
    fireEvent.change(text, { target: { value: 'typed' } })
    fireEvent.click(within(screen.getByRole('tablist', { name: 'Trend views' })).getByRole('tab', { name: 'Heatmap' }))
    expect(within(noteForm('Pin a note')).getByText(/Discard your changes to/)).toBeInTheDocument()
    fireEvent.click(within(noteForm('Pin a note')).getByRole('button', { name: 'Discard changes' }))
    await waitFor(() => expect(document.querySelector('[data-tab-panel="heatmap"]')).not.toBeNull())
    expect(screen.queryByRole('region', { name: 'Pin a note' })).toBeNull()
    fireEvent.click(within(screen.getByRole('tablist', { name: 'Trend views' })).getByRole('tab', { name: 'Volume' }))
    await waitFor(() => expect(document.querySelector('[data-tab-panel="volume"]')).not.toBeNull())
    expect(screen.queryByRole('region', { name: 'Pin a note' }), 'the form does not come back').toBeNull()
  })

  it('More actions › Email report… renders the email form right after the page header', async () => {
    role.isQaLead = true
    await renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(screen.getByRole('menuitem', { name: 'Email report…' }))
    const form = await screen.findByRole('region', { name: 'Email report' })
    noModal()
    const header = document.querySelector('[data-page-header]') as HTMLElement
    expect(header.nextElementSibling).toBe(form)
    expect(form).toHaveAttribute('data-email-report-dialog')
    fireEvent.click(within(form).getByRole('button', { name: 'Close email dialog' }))
    expect(screen.queryByRole('region', { name: 'Email report' })).toBeNull()
  })
})
