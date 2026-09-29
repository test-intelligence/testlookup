/**
 * VIZ-104 (package d) — the IntelligenceHub's two bars are the kit's
 * `GaugeBar`: the per-run pass-rate meter in "Recent runs analyzed" and the
 * monthly budget bar under "Intelligence spend".
 *
 *   - OD-13: a run with no pass rate is an empty track plus "—" (the kit's
 *     one not-measured look), never a 0 and never a bare dash.
 *   - OD-14: budget utilisation is lower-is-better, a warning from 80 %, bad
 *     at the cap; it was a hex teal gradient that said nothing.
 *   - `intelligence-table-geometry.spec.ts` measures the pass-rate cell's
 *     FIRST element child as "the meter plus its percentage": that child must
 *     be the whole meter, on one line.
 */
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import IntelligenceHubPage from './IntelligenceHubPage'

const state = vi.hoisted(() => ({
  projectId: 'all' as string,
  quota: null as null | { enabled: boolean; hard_cap_usd: number },
  usage: null as null | { total_cost_usd: number; total_llm_calls: number; utilization_pct?: number },
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
  useMostRecentRun: vi.fn(() => ({ data: undefined })),
}))

vi.mock('@/hooks/useLlmBudget', () => ({
  useProjectQuota: () => ({ quota: state.quota, isLoading: false }),
  useProjectUsage: () => ({ usage: state.usage, isLoading: false }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: 'all',
  useProjectStore: (selector: (s: { activeProjectId: string }) => unknown) => selector({ activeProjectId: state.projectId }),
}))

const run = (id: string, build: string, pass_rate: number | null, over: { status?: string; total_tests?: number } = {}) => ({
  id, build_number: build, status: 'PASSED', failed_tests: 0, passed_tests: 10, broken_tests: 0, skipped_tests: 0,
  total_tests: 10, pass_rate, branch: 'main', project_name: 'Project One', created_at: '2026-04-03T15:00:00Z',
  duration_ms: 60000, ...over,
})

async function renderHub(items: ReturnType<typeof run>[]) {
  const { useRuns } = await import('@/hooks/useRuns')
  ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items }, isLoading: false })
  render(
    <MemoryRouter initialEntries={['/intelligence']}>
      <Routes><Route path="/intelligence" element={<IntelligenceHubPage />} /></Routes>
    </MemoryRouter>,
  )
  await screen.findByText(/Recent runs analyzed/i)
}

/** The pass-rate cell of each body row, found by its column header. */
function passRateCells(): HTMLTableCellElement[] {
  const header = screen.getByRole('columnheader', { name: /Pass rate/i })
  const index = [...(header.parentElement as HTMLElement).children].indexOf(header)
  const table = header.closest('table') as HTMLTableElement
  return [...table.querySelectorAll('tbody tr')].map((tr) => tr.children[index] as HTMLTableCellElement)
}

beforeEach(() => {
  state.projectId = 'all'
  state.quota = null
  state.usage = null
})

describe('IntelligenceHub — the pass-rate meter', () => {
  it('each run with a pass rate is a meter; its bands are 80 / 60, higher is better', async () => {
    await renderHub([run('r1', '42', 96), run('r2', '41', 70), run('r3', '40', 12)])
    const cells = passRateCells()
    const meters = cells.map((c) => within(c).getByRole('meter', { name: 'Pass rate' }))
    expect(meters.map((m) => m.getAttribute('aria-valuenow'))).toEqual(['96', '70', '12'])
    expect(meters.map((m) => m.getAttribute('aria-valuetext'))).toEqual(['96%', '70%', '12%'])
    expect(meters.map((m) => m.getAttribute('data-tone'))).toEqual(['good', 'warn', 'bad'])
    expect(within(cells[0]).getByText('96%')).toBeInTheDocument()
  })

  // R1 F13: 96 / 70 / 12 pinned only that 12 < lo <= 70 < hi <= 96. Both edges, both sides.
  it('pins both band edges: 59 bad, 60 warn, 79 warn, 80 good', async () => {
    await renderHub([run('r1', '4', 59), run('r2', '3', 60), run('r3', '2', 79), run('r4', '1', 80)])
    const tones = passRateCells().map((c) => within(c).getByRole('meter', { name: 'Pass rate' }).getAttribute('data-tone'))
    expect(tones).toEqual(['bad', 'warn', 'warn', 'good'])
  })

  // R1 F5: the API reports `pass_rate: 0` for a run still RUNNING, or one that
  // ran no tests. /runs (OD-18) calls that run not measured; so does the Hub,
  // rather than a red 0% meter.
  it.each([
    ['a running run', { status: 'RUNNING' }],
    ['a queued run', { status: 'QUEUED' }],
    ['a run that ran no tests', { total_tests: 0 }],
  ])('%s reporting pass_rate 0 is not measured, never a red 0%%', async (_name, over) => {
    await renderHub([run('r1', '42', 0, over)])
    const cell = passRateCells()[0]
    expect(within(cell).queryByRole('meter')).toBeNull()
    expect(within(cell).getByRole('img', { name: 'Pass rate: not measured' })).toBeInTheDocument()
    expect(within(cell).queryByText(/0%/)).toBeNull()
  })

  it('a finished run that really passed 0% is a measured, bad 0', async () => {
    await renderHub([run('r1', '42', 0, { status: 'FAILED' })])
    const meter = within(passRateCells()[0]).getByRole('meter', { name: 'Pass rate' })
    expect(meter).toHaveAttribute('aria-valuenow', '0')
    expect(meter).toHaveAttribute('data-tone', 'bad')
  })

  it('the cell\'s first element child is the whole meter: the 56 x 5 track then the percentage, on one line', async () => {
    await renderHub([run('r1', '42', 100)])
    const first = passRateCells()[0].firstElementChild as HTMLElement
    expect(first).toHaveAttribute('role', 'meter')
    expect(first.className).toContain('inline-flex')
    expect(first.className).toContain('whitespace-nowrap')
    const [track, value] = [...first.children] as HTMLElement[]
    expect(track).toHaveAttribute('data-gauge-track')
    expect(track.style.width).toBe('56px')
    expect(track.style.height).toBe('5px')
    expect(value).toHaveTextContent('100%')
  })

  it('a run with no pass rate is an empty track and a dash (OD-13) — never 0', async () => {
    await renderHub([run('r1', '42', null)])
    const cell = passRateCells()[0]
    const bar = within(cell).getByRole('img', { name: 'Pass rate: not measured' })
    expect(cell.firstElementChild).toBe(bar)
    expect(bar.querySelector('[data-gauge-track]')).not.toBeNull()
    expect(bar.querySelector('[data-gauge-fill]')).toBeNull()
    expect(within(cell).getByText('—')).toBeInTheDocument()
    expect(within(cell).queryByText(/0%/)).toBeNull()
    expect(within(cell).queryByRole('meter')).toBeNull()
  })
})

describe('IntelligenceHub — the monthly budget bar (OD-14)', () => {
  const budgetBar = () => screen.getByRole('meter', { name: 'Monthly budget' })

  it.each([
    [25, 'good'],
    [79, 'good'],
    [80, 'warn'],
    [99, 'warn'],
    [100, 'bad'],
  ])('a budget %s percent used is %s: lower is better', async (pct, tone) => {
    state.projectId = 'proj-1'
    state.quota = { enabled: true, hard_cap_usd: 100 }
    state.usage = { total_cost_usd: pct, total_llm_calls: 3, utilization_pct: pct }
    await renderHub([run('r1', '42', 90)])
    expect(budgetBar()).toHaveAttribute('aria-valuenow', String(pct))
    expect(budgetBar()).toHaveAttribute('aria-valuetext', `${pct}% used`)
    expect(budgetBar()).toHaveAttribute('data-tone', tone)
    // Tokens only: the teal hex gradient is gone.
    expect(document.body.innerHTML).not.toMatch(/#14b8a6|#5eead4/)
  })

  it('over the cap still reads as the full bar, bad', async () => {
    state.projectId = 'proj-1'
    state.quota = { enabled: true, hard_cap_usd: 10 }
    state.usage = { total_cost_usd: 14, total_llm_calls: 3 }
    await renderHub([run('r1', '42', 90)])
    expect(budgetBar()).toHaveAttribute('aria-valuenow', '100')
    expect(budgetBar()).toHaveAttribute('data-tone', 'bad')
    expect((budgetBar().querySelector('[data-gauge-fill]') as HTMLElement).style.width).toBe('100%')
  })

  it('no budget is not measured: an empty track, not a 0 % bar', async () => {
    state.projectId = 'proj-1'
    state.quota = { enabled: false, hard_cap_usd: 0 }
    state.usage = { total_cost_usd: 3, total_llm_calls: 3 }
    await renderHub([run('r1', '42', 90)])
    expect(screen.getByText('configure in settings')).toBeInTheDocument()
    expect(screen.queryByRole('meter', { name: 'Monthly budget' })).toBeNull()
    const bar = screen.getByRole('img', { name: 'Monthly budget: not measured' })
    expect(bar.querySelector('[data-gauge-fill]')).toBeNull()
  })
})
