/**
 * W08 TRND-07 / TRND-04: the Coverage History tab's section over the REAL
 * history hook and guard (the service mocked) — the drawn chart with its
 * notes, the sentence before the first snapshot, "not available" for a
 * malformed answer, and All Projects asking nothing.
 */
import { cloneElement, isValidElement, type ReactElement, type ReactNode } from 'react'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const service = vi.hoisted(() => ({ getCoverageHistory: vi.fn(), getUntaggedTests: vi.fn() }))
vi.mock('@/services/coverageHistoryService', () => ({ UNTAGGED_PAGE_SIZE: 50, coverageHistoryService: service }))

const notes = vi.hoisted(() => ({ items: [] as unknown[], calls: [] as unknown[][] }))
vi.mock('@/hooks/useTimelineNotes', () => ({
  TIMELINE_NOTES_KEY: 'timeline-notes',
  useTimelineNotes: (...args: unknown[]) => {
    notes.calls.push(args)
    return { notes: notes.items, total: notes.items.length, truncated: false, isLoading: false, unavailable: false, mutate: vi.fn() }
  },
  useTimelineNoteActions: () => ({ create: vi.fn(), update: vi.fn(), remove: vi.fn() }),
}))

const project = vi.hoisted(() => ({ id: 'proj-1' }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (selector: (s: { activeProjectId: string }) => unknown) => selector({ activeProjectId: project.id }),
}))

// jsdom lays nothing out: the container hands the chart a size so Recharts draws.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children) ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 640, height: 260 }) : null,
  }
})

import CoverageHistorySection, { HISTORY_FIRST_SNAPSHOT, HISTORY_PICK_PROJECT, HISTORY_UNAVAILABLE } from './CoverageHistorySection'

function renderSection(suite: string | null = null) {
  return render(
    <MemoryRouter>
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0, shouldRetryOnError: false }}>
        <CoverageHistorySection days={7} suite={suite} todayIso="2026-10-16" />
      </SWRConfig>
    </MemoryRouter>,
  )
}

const note = (id: string, from: string, to: string, text: string, suite: string | null = null) => ({
  id,
  project_id: 'proj-1',
  starts_on: from,
  ends_on: to,
  text,
  suite_name: suite,
  created_by_name: 'Ana',
  can_edit: false,
  created_at: null,
  updated_at: null,
})

describe('CoverageHistorySection', () => {
  beforeEach(() => {
    service.getCoverageHistory.mockReset()
    notes.items = []
    notes.calls = []
    project.id = 'proj-1'
  })

  it('draws the snapshots and the notes of the same days; a suite note shows only under its suite', async () => {
    service.getCoverageHistory.mockResolvedValue({
      window_days: 7,
      measured: true,
      reason: null,
      first_snapshot_day: '2026-10-01',
      points: [
        { day: '2026-10-12', catalogue_tests: 100, exercised_tests: 80, exercised_pct: 80, suites: 4 },
        { day: '2026-10-15', catalogue_tests: 104, exercised_tests: 90, exercised_pct: 86.5, suites: 4 },
      ],
    })
    notes.items = [
      note('n1', '2026-10-13', '2026-10-13', 'release freeze'),
      note('n2', '2026-10-10', '2026-10-11', '<img src=x onerror=alert(1)>'),
      note('n3', '2026-10-14', '2026-10-14', 'only payments', 'payments'),
    ]
    const { container } = renderSection()
    expect(await screen.findByRole('heading', { name: 'Coverage history' })).toBeInTheDocument()
    await screen.findByText('2 of 7 days have a snapshot · project-wide (the suite and release filters do not apply)')
    expect(service.getCoverageHistory).toHaveBeenCalledWith('proj-1', 7)
    // The notes' window is the chart's axis (7 days ending yesterday), no suite.
    expect(notes.calls[notes.calls.length - 1]).toEqual([{ from: '2026-10-09', to: '2026-10-15' }, null])
    const drawn = [...container.querySelectorAll('[data-chart-note]')].map((g) => g.getAttribute('data-chart-note'))
    expect(drawn.sort()).toEqual(['n1', 'n2'])
    // A hostile note text is text: no element is made from it.
    expect(container.querySelector('[data-chart-note] img')).toBeNull()
    expect(screen.getByRole('button', { name: 'Notes (2)' })).toBeInTheDocument()
  })

  it('under the page suite, that suite’s notes show too (any case)', async () => {
    service.getCoverageHistory.mockResolvedValue({
      window_days: 7, measured: true, reason: null, first_snapshot_day: '2026-10-01',
      points: [{ day: '2026-10-14', catalogue_tests: 10, exercised_tests: 5, exercised_pct: 50, suites: 1 }],
    })
    notes.items = [note('n3', '2026-10-14', '2026-10-14', 'only payments', 'Payments')]
    const { container } = renderSection('payments')
    await screen.findByText(/1 of 7 days have a snapshot/)
    expect(notes.calls[notes.calls.length - 1]?.[1]).toBe('payments')
    expect(container.querySelectorAll('[data-chart-note]')).toHaveLength(1)
  })

  it('before the first snapshot: the server’s sentence, not a chart of zeros', async () => {
    service.getCoverageHistory.mockResolvedValue({
      window_days: 7, measured: false, reason: HISTORY_FIRST_SNAPSHOT, first_snapshot_day: null, points: [],
    })
    renderSection()
    expect(await screen.findByText(HISTORY_FIRST_SNAPSHOT)).toBeInTheDocument()
  })

  it.each([
    ['garbage', { points: 3 }],
    ['an empty body', undefined],
  ])('a %s answer says "%s" and nothing crashes', async (_label, answer) => {
    service.getCoverageHistory.mockResolvedValue(answer)
    renderSection()
    expect(await screen.findByText(HISTORY_UNAVAILABLE)).toBeInTheDocument()
  })

  it('All Projects asks nothing (no history, no notes) and says to pick a project', async () => {
    project.id = '__ALL__'
    renderSection()
    expect(screen.getByText(HISTORY_PICK_PROJECT)).toBeInTheDocument()
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(service.getCoverageHistory).not.toHaveBeenCalled()
    expect(notes.calls.every((args) => args[0] === null)).toBe(true)
  })
})
