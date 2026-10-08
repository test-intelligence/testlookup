/**
 * `useDefects` loads every defect in scope, not one page of it.
 *
 * The Defects page derives all of its counts from the rows (open, P0/P1, the
 * oldest P0, the status tabs), and with one 20-row page it read "Open defects
 * 20" on a project with 80 (the UX redesign's browser E2E pass, 2026-10-08).
 */
import { render, screen, waitFor } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getDefects = vi.hoisted(() => vi.fn())
vi.mock('@/services/analyticsService', () => ({
  analyticsService: { getDefects: (...a: unknown[]) => getDefects(...a) },
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: (sel: (s: { activeProjectId: string | null }) => unknown) => sel({ activeProjectId: 'proj-1' }),
}))

import { DEFECTS_CAP, useDefects } from './useMetrics'

function pageOf(page: number, size: number, total: number) {
  const start = (page - 1) * size
  const items = Array.from({ length: Math.max(0, Math.min(size, total - start)) }, (_, i) => ({
    id: `d-${start + i}`, resolution_status: 'OPEN', created_at: '2026-10-01T00:00:00Z', test_name: 't',
  }))
  return { items, total, page, size, pages: Math.ceil(total / size) }
}

function Probe() {
  const { data } = useDefects()
  return <p data-testid="defects">{data ? `${data.items.length} of ${data.total}` : '…'}</p>
}

function renderProbe() {
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <Probe />
    </SWRConfig>,
  )
}

describe('useDefects', () => {
  beforeEach(() => {
    getDefects.mockReset()
    getDefects.mockImplementation((_p: string, params: { page: number; size: number }) =>
      Promise.resolve(pageOf(params.page, params.size, 250)))
  })

  it('pages through every defect, 100 at a time, and keeps the whole total', async () => {
    renderProbe()
    await waitFor(() => expect(screen.getByTestId('defects')).toHaveTextContent('250 of 250'))
    expect(getDefects.mock.calls.map((c) => c[1])).toEqual([
      { size: 100, resolution_status: undefined, page: 1 },
      { size: 100, resolution_status: undefined, page: 2 },
      { size: 100, resolution_status: undefined, page: 3 },
    ])
    expect(getDefects.mock.calls.every((c) => c[0] === 'proj-1')).toBe(true)
  })

  it(`stops at ${DEFECTS_CAP}, and the total still says how many there are`, async () => {
    getDefects.mockImplementation((_p: string, params: { page: number; size: number }) =>
      Promise.resolve(pageOf(params.page, params.size, 1500)))
    renderProbe()
    await waitFor(() => expect(screen.getByTestId('defects')).toHaveTextContent(`${DEFECTS_CAP} of 1500`))
    expect(getDefects).toHaveBeenCalledTimes(DEFECTS_CAP / 100)
  })
})
